"""Simula la app "Estadillo Digital" de Christian hablando con el Organizer
por la LAN, para probar el flujo (`GET /api/estadillo/ping`, `GET
/api/estadillo/espera`, `POST /api/estadillo`) sin tener esa app instalada.
También sirve para controlar el kiosco por HTTP (subcomandos `discos`,
`carpetas`, `carpeta`, `organizar`, `estado`, `cancelar`) usando el mismo
contrato `X-Atom-Pin` que la UI del kiosco.

Contrato exacto en `atom_core/api_docs.py` (`especificacion()`) y
`atom_core/webserver.py` (`_RUTAS_LAN_ABIERTAS`). Columnas de cada vuelo y sus
alias, en `atom_core/estadillo._CSV_COLUMNAS_CONOCIDAS` /
`_CSV_ALIAS_CAMPO_JSON` (coinciden con `FlightRecord` del repo real de
Christian, `Aerotools-UAV/Atom-Estadillo_Digital`, confirmado por lectura).

Solo stdlib (urllib): mismo criterio que `webserver.py`, sin dependencias
nuevas (salvo `--desde-bd`, que usa `docker exec`/`docker cp`, ya
disponibles en el host de desarrollo).

Requiere que el kiosco tenga el modo espera de estadillo ACTIVO (con carpeta
elegida) antes del POST: sin eso, el Organizer devuelve 409
`sin_espera_activa`/`sin_carpeta_seleccionada`, que este script trata como
resultado válido, no como fallo del propio script.

Dos modos para el subcomando `enviar` (ver `_cmd_enviar`):
- **Sintético (por defecto)**: genera `--vuelos` FlightRecord completos (31
  campos + `sync_uid` con el MISMO formato que `generateSyncUid`,
  `electron/postgresSync.ts`), con horas `HH:MM:SS` consecutivas de 25 min
  desde `--hora-inicio` (default `08:00`, como antes; ajústala a la hora
  real de vuelo -p.ej. EXIF de la tarjeta- para que no se descarten por
  fecha/hora al validar contra fotos reales). `--fuera-de-tarjeta N` añade N
  vuelos más, 3h antes de `--hora-inicio`, para comprobar que el filtro por
  tarjeta del Organizer (`_estadillo_filtrar_por_tarjeta`, `app_webview.py`)
  los descarta.
- **`--desde-bd --planta X --fecha YYYY-MM-DD`**: EMULA la fase 2 real de la
  app de Christian (el POST que ella aún no manda): lee filas REALES de
  `indai.estadillos` (SOLO SELECT, vía `docker exec suite-backend-saez`, la
  misma conexión que ya usa el contenedor de desarrollo; este script nunca
  abre su propia conexión ni escribe nada) y las envía tal cual, con su
  `sync_uid` real. Sin `--desde-bd`, se usa el modo sintético.

Ejemplos:
    python3 scripts/simular_estadillo_digital.py enviar --vuelos 3 --fuera-de-tarjeta 1
    python3 scripts/simular_estadillo_digital.py enviar --vuelos 3 --hora-inicio 13:27 --fecha 2026-08-19
    python3 scripts/simular_estadillo_digital.py enviar --desde-bd --planta KL05 --fecha 2026-09-20

Ejemplos de control del kiosco (requiere `--pin` o `ATOM_PIN`):
    python3 scripts/simular_estadillo_digital.py --url http://organizer.atom --pin 1234 discos
    python3 scripts/simular_estadillo_digital.py --pin 1234 carpetas /media/usb
    python3 scripts/simular_estadillo_digital.py --pin 1234 carpeta /media/usb/PLANTA_X
    python3 scripts/simular_estadillo_digital.py --pin 1234 organizar
    python3 scripts/simular_estadillo_digital.py --pin 1234 estado --seguir 5
    python3 scripts/simular_estadillo_digital.py --pin 1234 cancelar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

_RUTA_PING = "/api/estadillo/ping"
_RUTA_ESPERA = "/api/estadillo/espera"
_RUTA_RECIBIR = "/api/estadillo"

_RUTA_CTRL_DISCOS = "/api/control/discos"
_RUTA_CTRL_CARPETAS = "/api/control/carpetas"
_RUTA_CTRL_CARPETA = "/api/control/carpeta"
_RUTA_CTRL_ORGANIZAR = "/api/control/organizar"
_RUTA_CTRL_ESTADO = "/api/control/estado"
_RUTA_CTRL_CANCELAR = "/api/control/cancelar"


def _get(url: str, timeout: float, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: float, headers: dict | None = None) -> tuple[int, dict]:
    cuerpo = json.dumps(payload).encode("utf-8")
    cabeceras = {"Content-Type": "application/json"}
    cabeceras.update(headers or {})
    req = urllib.request.Request(
        url, data=cuerpo, method="POST",
        headers=cabeceras,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _cabeceras_pin(pin: str | None) -> dict:
    return {"X-Atom-Pin": pin} if pin else {}


def _mostrar_error(codigo: int, cuerpo: dict) -> int:
    codigo_error = cuerpo.get("codigo") if isinstance(cuerpo, dict) else None
    print(f"  ERROR {codigo}: {json.dumps(cuerpo, ensure_ascii=False)}"
          if codigo_error is None else f"  ERROR {codigo}: {codigo_error}",
          file=sys.stderr)
    return 1


def _control_get(base: str, ruta: str, pin: str | None, timeout: float) -> int:
    codigo, cuerpo = _get(base + ruta, timeout, _cabeceras_pin(pin))
    if codigo >= 400:
        return _mostrar_error(codigo, cuerpo)
    print(json.dumps(cuerpo, ensure_ascii=False, indent=2))
    return 0


def _control_post(base: str, ruta: str, payload: dict, pin: str | None, timeout: float) -> int:
    codigo, cuerpo = _post_json(base + ruta, payload, timeout, _cabeceras_pin(pin))
    if codigo >= 400:
        return _mostrar_error(codigo, cuerpo)
    print(json.dumps(cuerpo, ensure_ascii=False, indent=2))
    return 0


def _sync_uid(fecha: str, piloto: str, num_vuelo: str, hora_inicio: str, pb: str) -> str:
    """Mismo formato que `generateSyncUid` (`electron/postgresSync.ts` del
    repo real de Christian): `<fecha>_<PILOTO>_V<vuelo>_H<hora>_PB<pb>`."""
    fecha_limpia = re.sub(r"[:\-/]", "-", (fecha or "").strip())
    piloto_limpio = re.sub(r"\s+", "_", (piloto or "").strip().upper())
    hora_limpia = re.sub(r"[:\s]", "", (hora_inicio or "").strip())
    pb_limpio = re.sub(r"[:\s]", "", (pb or "").strip())
    return f"{fecha_limpia}_{piloto_limpio}_V{num_vuelo}_H{hora_limpia}_PB{pb_limpio}"


def _flight_record(*, planta: str, fecha: str, piloto: str, dron: str,
                    pb: str, num_vuelo: str, hora_inicio: str, hora_final: str) -> dict:
    """Un `FlightRecord` REAL y completo (31 campos + `sync_uid`), con horas
    `HH:MM:SS` -no `HH:MM`-, valores plausibles en el resto de campos (no
    se usan para el filtro por tarjeta, solo tienen que existir con forma
    correcta, como los mandaría la app de Christian)."""
    return {
        "Empresa": "Aerotools",
        "Trabajo": planta,
        "Fecha": fecha,
        "Piloto": piloto,
        "Equipo_de_vuelo": dron,
        "Pitch": "2",
        "Hora_de_inicio": hora_inicio,
        "Hora_final": hora_final,
        "PB": pb,
        "Vuelo": num_vuelo,
        "Desplazado": "No",
        "Vel_vuelo": "5",
        "Alt_vuelo": "30",
        "Vel_de_aire": "2",
        "Temp_aire": "18",
        "Nubes": "Despejado",
        "Radiacion": "800",
        "Tiempo_vuelo": "25",
        "Dist_Recorrida": "1200",
        "Set_Bat_1": "1",
        "Set_Bat_2": "2",
        "Set_Bat_3": "",
        "Volt_inicial": "25.2",
        "Volt_final": "22.8",
        "GB1/": "GB1",
        "GB2/": "GB2",
        "Anotaciones": "Vuelo de simulacion (simular_estadillo_digital.py)",
        "Termica": 1,
        "RGB": 1,
        "Cali_Ini": 1,
        "Cali_Final": 1,
        "Tipologia": "Fija",
        "Vuelo_abortado": 0,
        "sync_uid": _sync_uid(fecha, piloto, num_vuelo, hora_inicio, pb),
    }


def _vuelos_de_prueba(planta: str, fecha: str, piloto: str, dron: str,
                       n_vuelos: int, fuera_de_tarjeta: int = 0,
                       hora_inicio: str = "08:00") -> list[dict]:
    """`n_vuelos` FlightRecord completos, consecutivos de 25 min desde
    `hora_inicio` (`HH:MM`, misma `fecha`/PB 1; default "08:00", como antes).
    `fuera_de_tarjeta` añade N vuelos MÁS con PB "9", 3 horas antes de
    `hora_inicio` -lejos de cualquier foto real de una tarjeta normal-,
    pensados para comprobar que `_estadillo_filtrar_por_tarjeta`
    (`app_webview.py`) los descarta."""
    base_h, base_m = (int(p) for p in hora_inicio.split(":", 1))
    base_minutos = base_h * 60 + base_m

    vuelos = []
    minutos = 0
    for i in range(1, n_vuelos + 1):
        inicio_h, inicio_m = divmod(base_minutos + minutos, 60)
        fin_h, fin_m = divmod(base_minutos + minutos + 25, 60)
        vuelos.append(_flight_record(
            planta=planta, fecha=fecha, piloto=piloto, dron=dron,
            pb="1", num_vuelo=str(i),
            hora_inicio=f"{inicio_h:02d}:{inicio_m:02d}:00",
            hora_final=f"{fin_h:02d}:{fin_m:02d}:00",
        ))
        minutos += 30

    # 3h antes de hora_inicio, sin cruzar de dia hacia atras (si no cabe, se
    # queda pegado a medianoche: sigue quedando claramente fuera de tarjeta).
    fuera_minutos = max(base_minutos - 180, 0)
    fuera_h, fuera_m = divmod(fuera_minutos, 60)
    fuera_fin_h, fuera_fin_m = divmod(fuera_minutos + 25, 60)
    for j in range(1, fuera_de_tarjeta + 1):
        vuelos.append(_flight_record(
            planta=planta, fecha=fecha, piloto=piloto, dron=dron,
            pb="9", num_vuelo=f"F{j}",
            hora_inicio=f"{fuera_h:02d}:{fuera_m:02d}:00",
            hora_final=f"{fuera_fin_h:02d}:{fuera_fin_m:02d}:00",
        ))

    return vuelos


def _leer_vuelos_bd(planta: str, fecha: str, contenedor: str = "suite-backend-saez") -> list[dict]:
    """Lee las filas REALES de `indai.estadillos` para `(planta, fecha)` vía
    `docker exec <contenedor>` -la misma conexión (`DB_HOST`/`DB_USER`/...)
    que ya usa el backend de desarrollo, SOLO SELECT, este script nunca abre
    su propia conexión ni escribe nada-.

    El script Node se copia al contenedor con `docker cp` (nunca por
    `-e`/heredoc con comillas: se rompe con el apóstrofe de `indai.estadillos`
    en comentarios) y se ejecuta con `docker exec ... node /tmp/<script>`.

    Devuelve una lista de `FlightRecord` (cabeceras CSV reales) + `sync_uid`,
    tal cual saldrían de la app de Christian -mapeando las columnas en
    minúscula de la BD a las cabeceras `Sync_UID`/`GB1/`/`GB2/` etc. que
    reconoce `atom_core.estadillo._CSV_COLUMNAS_CONOCIDAS`-."""
    script_js = """
const { Pool } = require('pg');
const pool = new Pool({
  host: process.env.DB_HOST, port: process.env.DB_PORT,
  user: process.env.DB_USER, password: process.env.DB_PASSWORD,
  database: process.env.DB_NAME,
});
const [, , planta, fecha] = process.argv;
pool.query(
  `select empresa, trabajo, to_char(fecha, 'YYYY-MM-DD') as fecha, piloto,
          equipo_de_vuelo, pitch, hora_de_inicio, hora_final, pb, vuelo,
          desplazado, vel_vuelo, alt_vuelo, vel_de_aire, temp_aire, nubes,
          radiacion, tiempo_vuelo, dist_recorrida, set_bat_1, set_bat_2,
          set_bat_3, volt_inicial, volt_final, gb1, gb2, anotaciones,
          termica, rgb, cali_ini, cali_final, tipologia, vuelo_abortado,
          sync_uid
     from indai.estadillos
    where trabajo = $1 and fecha = $2
    order by pb, vuelo`,
  [planta, fecha]
).then((r) => {
  process.stdout.write(JSON.stringify(r.rows));
  pool.end();
}).catch((e) => {
  console.error(e.message);
  pool.end();
  process.exitCode = 1;
});
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script_js)
        ruta_local = fh.name
    ruta_remota = "/tmp/simular_estadillo_leer_bd.js"
    try:
        subprocess.run(["docker", "cp", ruta_local, f"{contenedor}:{ruta_remota}"],
                        check=True, capture_output=True, text=True)
        proc = subprocess.run(
            ["docker", "exec", contenedor, "node", ruta_remota, planta, fecha],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"No se pudo leer indai.estadillos vía docker exec {contenedor}: "
            f"{exc.stderr or exc.stdout}"
        ) from exc
    finally:
        os.unlink(ruta_local)

    filas = json.loads(proc.stdout or "[]")
    if not filas:
        raise RuntimeError(
            f"No hay filas en indai.estadillos para planta={planta!r} fecha={fecha!r}."
        )

    _MAPA_DB_A_CSV = {
        "empresa": "Empresa", "trabajo": "Trabajo", "fecha": "Fecha",
        "piloto": "Piloto", "equipo_de_vuelo": "Equipo_de_vuelo", "pitch": "Pitch",
        "hora_de_inicio": "Hora_de_inicio", "hora_final": "Hora_final",
        "pb": "PB", "vuelo": "Vuelo", "desplazado": "Desplazado",
        "vel_vuelo": "Vel_vuelo", "alt_vuelo": "Alt_vuelo", "vel_de_aire": "Vel_de_aire",
        "temp_aire": "Temp_aire", "nubes": "Nubes", "radiacion": "Radiacion",
        "tiempo_vuelo": "Tiempo_vuelo", "dist_recorrida": "Dist_Recorrida",
        "set_bat_1": "Set_Bat_1", "set_bat_2": "Set_Bat_2", "set_bat_3": "Set_Bat_3",
        "volt_inicial": "Volt_inicial", "volt_final": "Volt_final",
        "gb1": "GB1/", "gb2": "GB2/", "anotaciones": "Anotaciones",
        "termica": "Termica", "rgb": "RGB", "cali_ini": "Cali_Ini",
        "cali_final": "Cali_Final", "tipologia": "Tipologia",
        "vuelo_abortado": "Vuelo_abortado", "sync_uid": "sync_uid",
    }
    vuelos = []
    for fila in filas:
        vuelo = {}
        for clave_db, valor in fila.items():
            columna = _MAPA_DB_A_CSV.get(clave_db)
            if columna and valor not in (None, ""):
                vuelo[columna] = valor
        vuelos.append(vuelo)
    return vuelos


def _cmd_enviar(args) -> int:
    base = args.url.rstrip("/")

    print(f"[1/3] GET {base}{_RUTA_PING}")
    try:
        codigo, cuerpo = _get(base + _RUTA_PING, args.timeout)
    except Exception as exc:  # noqa: BLE001 — informativo, es una herramienta de prueba manual
        print(f"  ERROR de red: {exc}", file=sys.stderr)
        return 2
    print(f"  {codigo} {json.dumps(cuerpo, ensure_ascii=False)}")
    if args.solo_ping:
        return 0

    print(f"[2/3] GET {base}{_RUTA_ESPERA}")
    codigo, cuerpo = _get(base + _RUTA_ESPERA, args.timeout)
    print(f"  {codigo} {json.dumps(cuerpo, ensure_ascii=False)}")
    if codigo != 200:
        print("  El kiosco no tiene espera activa (o caducó): no se manda el POST."
              " Inicia la espera desde el kiosco y reintenta.")
        return 0

    if args.desde_bd:
        print(f"    (--desde-bd) leyendo indai.estadillos planta={args.planta!r} fecha={args.fecha!r} vía docker exec")
        try:
            vuelos = _leer_vuelos_bd(args.planta, args.fecha)
        except RuntimeError as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"    {len(vuelos)} vuelo(s) reales leídos de la BD")
    else:
        vuelos = _vuelos_de_prueba(args.planta, args.fecha, args.piloto, args.dron,
                                    args.vuelos, args.fuera_de_tarjeta, args.hora_inicio)
    if args.con_alias:
        for v in vuelos:
            if "Equipo_de_vuelo" in v:
                v["dron"] = v.pop("Equipo_de_vuelo")

    print(f"[3/3] POST {base}{_RUTA_RECIBIR}  ({len(vuelos)} vuelos)")
    codigo, cuerpo = _post_json(base + _RUTA_RECIBIR, {"vuelos": vuelos}, args.timeout)
    print(f"  {codigo} {json.dumps(cuerpo, ensure_ascii=False, indent=2)}")
    return 0 if codigo == 200 else 1


def _cmd_discos(args) -> int:
    return _control_get(args.url.rstrip("/"), _RUTA_CTRL_DISCOS, args.pin, args.timeout)


def _cmd_carpetas(args) -> int:
    qs = urllib.parse.urlencode({"path": args.path})
    return _control_get(args.url.rstrip("/"), f"{_RUTA_CTRL_CARPETAS}?{qs}", args.pin, args.timeout)


def _cmd_carpeta(args) -> int:
    return _control_post(args.url.rstrip("/"), _RUTA_CTRL_CARPETA, {"path": args.path}, args.pin, args.timeout)


def _cmd_organizar(args) -> int:
    return _control_post(args.url.rstrip("/"), _RUTA_CTRL_ORGANIZAR, {}, args.pin, args.timeout)


def _cmd_estado(args) -> int:
    base = args.url.rstrip("/")
    while True:
        codigo, cuerpo = _get(base + _RUTA_CTRL_ESTADO, args.timeout, _cabeceras_pin(args.pin))
        if codigo >= 400:
            return _mostrar_error(codigo, cuerpo)
        print(json.dumps(cuerpo, ensure_ascii=False, indent=2))
        if not args.seguir or not cuerpo.get("en_curso", False):
            return 0
        time.sleep(args.seguir)


def _cmd_cancelar(args) -> int:
    return _control_post(args.url.rstrip("/"), _RUTA_CTRL_CANCELAR, {}, args.pin, args.timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8765",
                     help="Base del Organizer, p.ej. http://organizer.atom o http://<ip>:8765 (default: %(default)s)")
    ap.add_argument("--pin", default=os.environ.get("ATOM_PIN"),
                     help="PIN del kiosco (X-Atom-Pin), o variable de entorno ATOM_PIN (default: %(default)s)")
    ap.add_argument("--timeout", type=float, default=10.0)

    sub = ap.add_subparsers(dest="comando")

    ap_enviar = sub.add_parser("enviar", help="simula el envio del estadillo (comportamiento por defecto)")
    ap_enviar.add_argument("--planta", default="PRUEBA_SIM")
    ap_enviar.add_argument("--fecha", default=date.today().isoformat())
    ap_enviar.add_argument("--piloto", default="Rebeca")
    ap_enviar.add_argument("--dron", default="M300")
    ap_enviar.add_argument("--vuelos", type=int, default=2, help="numero de vuelos de prueba a generar (default: %(default)s)")
    ap_enviar.add_argument("--hora-inicio", default="08:00", metavar="HH:MM",
                            help="hora local (HH:MM) del primer vuelo sintetico en tarjeta; desplaza tambien los "
                                 "siguientes vuelos consecutivos, para cuadrar con el EXIF de fotos reales "
                                 "(ignorado con --desde-bd; default: %(default)s)")
    ap_enviar.add_argument("--fuera-de-tarjeta", type=int, default=0, metavar="N",
                            help="anade N vuelos sinteticos mas, a una hora/dia lejos de cualquier foto real, "
                                 "para comprobar que el Organizer los descarta al filtrar por tarjeta (ignorado con --desde-bd)")
    ap_enviar.add_argument("--desde-bd", action="store_true",
                            help="en vez de generar vuelos sinteticos, lee filas REALES de indai.estadillos "
                                 "(SOLO SELECT, via docker exec suite-backend-saez) para --planta/--fecha y las envia tal cual, "
                                 "con su sync_uid real: emula la fase 2 de la app de Christian")
    ap_enviar.add_argument("--con-alias", action="store_true",
                            help="usa 'dron'/'GB1'/'GB2' (alias de Christian) en vez de las cabeceras CSV reales, para probar _CSV_ALIAS_CAMPO_JSON")
    ap_enviar.add_argument("--solo-ping", action="store_true", help="hace solo el GET /api/estadillo/ping y termina")
    ap_enviar.set_defaults(func=_cmd_enviar)

    ap_discos = sub.add_parser("discos", help="GET /api/control/discos: lista discos/USB montados")
    ap_discos.set_defaults(func=_cmd_discos)

    ap_carpetas = sub.add_parser("carpetas", help="GET /api/control/carpetas?path=...: lista carpetas de path")
    ap_carpetas.add_argument("path")
    ap_carpetas.set_defaults(func=_cmd_carpetas)

    ap_carpeta = sub.add_parser("carpeta", help="POST /api/control/carpeta: fija la carpeta a organizar")
    ap_carpeta.add_argument("path")
    ap_carpeta.set_defaults(func=_cmd_carpeta)

    ap_organizar = sub.add_parser("organizar", help="POST /api/control/organizar: lanza la organizacion")
    ap_organizar.set_defaults(func=_cmd_organizar)

    ap_estado = sub.add_parser("estado", help="GET /api/control/estado")
    ap_estado.add_argument("--seguir", type=int, default=0, metavar="N",
                            help="sondea cada N segundos hasta que en_curso sea false")
    ap_estado.set_defaults(func=_cmd_estado)

    ap_cancelar = sub.add_parser("cancelar", help="POST /api/control/cancelar")
    ap_cancelar.set_defaults(func=_cmd_cancelar)

    args = ap.parse_args()

    if args.comando is None:
        args.planta, args.fecha = "PRUEBA_SIM", date.today().isoformat()
        args.piloto, args.dron = "Rebeca", "M300"
        args.vuelos, args.con_alias, args.solo_ping = 2, False, False
        args.fuera_de_tarjeta, args.desde_bd = 0, False
        args.hora_inicio = "08:00"
        return _cmd_enviar(args)

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())


# Lanzarlo desde el portátil de Rodrigo (fuera de la LAN del kiosco, vía el
# hotspot/red de la Pi):
#   python3 scripts/simular_estadillo_digital.py --url http://organizer.atom --vuelos 3
# Contra la Pi por SSH con forward de puerto (sin estar en su wifi):
#   ssh -i ~/claves-pi/pi_kiosk_key -p 2223 -L 8765:localhost:8765 pi@localhost
#   python3 scripts/simular_estadillo_digital.py --url http://127.0.0.1:8765
# Solo detectar la Pi en la LAN (sin tocar el modo espera):
#   python3 scripts/simular_estadillo_digital.py --url http://organizer.atom --solo-ping
