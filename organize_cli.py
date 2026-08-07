"""Ejecuta el pipeline de organización sin GUI, desde la línea de comandos.

Existe por dos motivos que apuntan al mismo sitio:

1. **Medir.** Hasta ahora la única forma de correr el pipeline era abrir la
   ventana y darle a «Organizar completo», así que no había manera limpia de
   cronometrar un vuelo ni de comparar dos máquinas. Aquí el tiempo total y el
   de cada fase salen por stdout.
2. **El servidor.** La dirección del producto es «la app sube, el servidor
   organiza». Un contenedor no tiene ventana: necesita un proceso que reciba
   rutas, trabaje y termine con un código de salida. Esto es ese proceso, y es
   la pieza que faltaba para poder probar el pipeline en Cloud Run.

No duplica lógica: `atom_core.organize.run_task` ya era headless y es lo que usa
el bridge de la UI. Esto solo le da una entrada por argumentos y una salida por
consola, así que la app de escritorio y el servidor ejecutan exactamente el
mismo código.

Uso típico:

    python organize_cli.py --origen ./VUELO --destino ./SALIDA \\
        --estadillo ./ESTADILLOS/planta.csv

El código de salida es 0 si el pipeline terminó y 1 si emitió un error: es lo
que mira un Cloud Run Job para decidir si el trabajo fue bien.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import sys
import time
from pathlib import Path

# `atom_core.sharding` solo importa `os`: se puede traer arriba sin pagar los
# segundos de imports pesados que sí cuesta `atom_core.organize` (numpy, PIL,
# SDK térmico), que por eso se importa dentro de `main`.
from atom_core import sharding

# Segundos por imagen y etapa, medidos en v3.4.32 sobre ANTOLIN (2.516 imágenes,
# 9,14 GB) con N=8 tareas. UN SOLO dataset: son un orden de magnitud, no una
# promesa, y por eso la Suite los pinta siempre con un «aprox.».
_SEG_POR_IMAGEN = {"split": 0.045, "struct": 0.026, "post": 0.102}
_SEG_POR_IMAGEN["todo"] = sum(_SEG_POR_IMAGEN.values())  # 0.173


def _contar_imagenes(origen: Path) -> int:
    """Cuenta imágenes bajo `origen` recursivamente con `os.scandir`.

    Nada de `glob`/`Path.rglob`: un vuelo son decenas de miles de ficheros y
    esto tiene que tardar milisegundos, no segundos. Fail-open: si algo falla
    (permisos, ruta rara) devuelve 0 — sin conteo no hay ETA, pero el run
    sigue igual.
    """
    exts = {".jpg", ".jpeg", ".dng", ".tif", ".tiff", ".png"}
    total = 0
    try:
        pendientes = [origen]
        while pendientes:
            actual = pendientes.pop()
            with os.scandir(actual) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pendientes.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if os.path.splitext(entry.name)[1].lower() in exts:
                                total += 1
                    except OSError:
                        continue
    except Exception:  # noqa: BLE001 - fail-open: sin conteo no hay ETA
        return 0
    return total


def _calcular_eta(*, etapa: str, n_imagenes: int, shard_count: int,
                   transcurrido: float, fases_cerradas: int,
                   fases_total: int | None) -> int | None:
    """ETA en segundos, recalculada en CADA cambio de fase (nunca fijada al
    inicio). Asume fases de peso similar dentro de una etapa: es la
    aproximación que hay sin perfilado por fase, y es la razón del «aprox.»
    con el que la Suite pinta este dato.

    - Mientras no se haya cerrado ninguna fase, usa el presupuesto inicial
      derivado de `_SEG_POR_IMAGEN` (medido con N=8, de ahí el ×8 para
      des-escalar según el shard real).
    - En cuanto hay >=1 fase cerrada, es más fiable calibrar con el tiempo
      real transcurrido en ESTA máquina y ESTE vuelo que seguir fiándose de
      los coeficientes de otro dataset.
    """
    try:
        if fases_cerradas > 0 and fases_total:
            k = fases_cerradas
            total = fases_total
            if k >= total:
                return 0
            return max(0, int(round(transcurrido * (total - k) / k)))

        seg_por_imagen = _SEG_POR_IMAGEN.get(etapa)
        if seg_por_imagen is None or n_imagenes <= 0:
            return None
        presupuesto = seg_por_imagen * n_imagenes * 8 / max(shard_count, 1)
        return max(0, int(round(presupuesto - transcurrido)))
    except Exception:  # noqa: BLE001 - fail-open: sin ETA no pasa nada
        return None


def _formatear(segundos: float) -> str:
    """`1h 04m 12s`. Un vuelo entero se mide en decenas de minutos, y leer
    «3852.4 s» obliga a dividir a mano cada vez que se compara con otra corrida."""
    segundos = int(round(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}m {seg:02d}s"
    if minutos:
        return f"{minutos}m {seg:02d}s"
    return f"{seg}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline de ATOM Organizer sin interfaz gráfica.")
    parser.add_argument("--origen", required=True,
                        help="Carpeta del vuelo (la que se sube al bucket).")
    parser.add_argument("--destino", required=True,
                        help="Carpeta de salida. Se crea si no existe.")
    parser.add_argument("--estadillo", default="",
                        help="CSV del estadillo. Sin él no se organiza en la "
                             "estructura de carpetas de la planta.")
    parser.add_argument("--task", default="split_images",
                        help="Task del pipeline (por defecto el completo).")
    parser.add_argument("--sin-tif", action="store_true",
                        help="Salta la conversión térmica a TIF. Útil para "
                             "medir solo la parte RGB.")
    parser.add_argument("--json", dest="como_json", action="store_true",
                        help="Resumen final en JSON en la última línea, para "
                             "que lo lea un script en vez de una persona.")
    parser.add_argument("--quiet", action="store_true",
                        help="Solo fases y resumen; sin el detalle por imagen.")
    # --- reparto entre N tareas -------------------------------------------
    # Una corrida completa de ANTOLIN son 33m 06s en una sola tarea de 8 vCPU, y
    # el 90 % es trabajo carpeta a carpeta sin dependencias. Con estas tres
    # opciones el MISMO Job se lanza tres veces (split -> struct -> post) y las
    # etapas repartibles se abren en N tareas paralelas. Ver atom_core/sharding.
    #
    # Los defaults dejan el comportamiento intacto: `--etapa todo` sin shard es
    # exactamente lo que hacía el CLI antes, que es lo que usa la app de
    # escritorio y lo que espera cualquier script existente.
    parser.add_argument("--etapa", choices=sharding.ETAPAS, default="todo",
                        help="Parte del pipeline a ejecutar. `todo` (por "
                             "defecto) = las fases de siempre, de principio a "
                             "fin. `split` = separación RGB/térmica (repartible). "
                             "`struct` = estructura de carpetas (una sola tarea). "
                             "`post` = recorte, meta, rotación y TIF (repartible).")
    parser.add_argument("--shard-index", dest="shard_index", default=None,
                        help="Índice de esta tarea, de 0 a shard-count-1. Por "
                             "defecto, $CLOUD_RUN_TASK_INDEX.")
    parser.add_argument("--shard-count", dest="shard_count", default=None,
                        help="Número total de tareas entre las que se reparte la "
                             "etapa. Por defecto, $CLOUD_RUN_TASK_COUNT (1 fuera "
                             "de Cloud Run).")
    args = parser.parse_args(argv)

    # El shard explícito manda sobre el entorno: así se puede reproducir en local
    # exactamente la partición que le tocó a una tarea concreta del Job.
    if args.shard_index is None and args.shard_count is None:
        shard_index, shard_count = sharding.shard_desde_entorno()
    else:
        shard_index, shard_count = sharding.normalizar_shard(
            args.shard_index if args.shard_index is not None else 0,
            args.shard_count if args.shard_count is not None else 1)

    origen = Path(args.origen).expanduser().resolve()
    destino = Path(args.destino).expanduser().resolve()
    if not origen.is_dir():
        print(f"error: la carpeta de origen no existe: {origen}", file=sys.stderr)
        return 2
    if args.estadillo and not Path(args.estadillo).expanduser().is_file():
        print(f"error: el estadillo no existe: {args.estadillo}", file=sys.stderr)
        return 2
    destino.mkdir(parents=True, exist_ok=True)

    # Import aquí y no arriba: `atom_core.organize` arrastra el pipeline entero
    # (numpy, PIL, el SDK térmico). Si el usuario solo pidió `--help`, no tiene
    # sentido pagar varios segundos de imports para imprimirlo.
    from atom_core.organize import run_task

    params: dict = {"origen": str(origen), "destino": str(destino),
                    "estadillo": args.estadillo,
                    "etapa": args.etapa,
                    "shard_index": shard_index, "shard_count": shard_count}
    avanzado = {"convert_to_tif": False} if args.sin_tif else None

    # Solo se reporta a la Suite si hay secreto: sin él (un dev corriendo el
    # CLI en local) `reporter` queda `None` y todo lo de abajo es no-op.
    #
    # Fail-open SÍ, mudo NO. La telemetría no puede tumbar un pipeline de
    # minutos u horas, así que cualquier fallo suyo se traga; pero se traga
    # DICIÉNDOLO. El modo de fallo que motivó esto fue una imagen publicada sin
    # `run_reporter.py`: el Job corría verde y la Suite no veía el run, sin un
    # solo indicio de que faltaba nada. `_telemetria_motivo` es None mientras
    # todo va bien y guarda la razón en cuanto deja de ir.
    reporter = None
    telemetria_motivo: str | None = None
    n_imagenes = _contar_imagenes(origen)
    secreto = os.environ.get("ORGANIZER_INGEST_SECRET")
    en_cloud_run = bool(os.environ.get("CLOUD_RUN_EXECUTION"))
    if secreto:
        try:
            from atom_core.run_reporter import RunReporter
        except Exception as exc:  # noqa: BLE001 - imagen sin el modulo: avisar, no morir
            RunReporter = None
            telemetria_motivo = (
                f"no se pudo importar atom_core.run_reporter "
                f"({type(exc).__name__}: {exc}); imagen sin telemetria?")

        if RunReporter is not None:
            # Sin `base`: RunReporter ya resuelve el destino desde ATOM_SUITE_API_BASE.
            # Pasarlo aquí duplicaba esa lógica y con el nombre equivocado
            # (`ATOM_SUITE_API`, que no existe): apuntar el Job a dev seteando esa
            # variable se ignoraba en silencio y los latidos se iban a PROD.
            reporter = RunReporter(auth=None, secreto=secreto, tipo="pipeline")
            extra_inicio = {
                "execution_name": os.environ.get("CLOUD_RUN_EXECUTION"),
                "shard_count": shard_count,
                "shard_index": shard_index,
            }
            extra_inicio = {k: v for k, v in extra_inicio.items() if v is not None}
            reporter.iniciar(inspeccion=origen.name, etapa=args.etapa,
                              items_total=n_imagenes, **extra_inicio)
            # `iniciar` es fail-open por dentro: si la Suite no contestó, el run
            # no existe y todo lo demás sería no-op. Preguntarlo aquí es la
            # diferencia entre "no hay run" y "no sabemos que no hay run".
            if not reporter.activo():
                reporter = None
                telemetria_motivo = ("la Suite no acepto el alta del run "
                                     "(red, 401 o 5xx); no habra progreso en /organizer")
    elif en_cloud_run:
        # En local no tener secreto es lo normal y no merece ruido. Dentro de
        # Cloud Run sí: significa que la Suite lanzó el Job sin inyectar
        # ORGANIZER_INGEST_SECRET por containerOverrides.
        telemetria_motivo = "sin ORGANIZER_INGEST_SECRET en el entorno del Job"

    if telemetria_motivo:
        print(f"[telemetria] DESACTIVADA: {telemetria_motivo}",
              file=sys.stderr, flush=True)

    fases: list[dict] = []
    errores: list[str] = []
    arranque = time.monotonic()
    fase_desde = arranque

    def emit(kind: str, payload=None) -> None:
        nonlocal fase_desde
        if kind == "phase" and isinstance(payload, dict):
            ahora = time.monotonic()
            if fases:
                fases[-1]["segundos"] = round(ahora - fase_desde, 1)
            fase_desde = ahora
            nombre = payload.get("name", "?")
            fases.append({"nombre": nombre, "segundos": None})
            print(f"[fase {payload.get('index')}/{payload.get('total')}] {nombre}",
                  flush=True)
            if reporter is not None:
                index = payload.get("index")
                total = payload.get("total")
                fases_cerradas = sum(1 for f in fases if f["segundos"] is not None)
                eta = _calcular_eta(
                    etapa=args.etapa, n_imagenes=n_imagenes,
                    shard_count=shard_count,
                    transcurrido=ahora - arranque,
                    fases_cerradas=fases_cerradas, fases_total=total)
                reporter.fase(index=index, total=total, nombre=nombre,
                              eta_segundos=eta)
        elif kind == "error":
            errores.append(str(payload))
            print(f"[error] {payload}", file=sys.stderr, flush=True)
        elif kind == "done" and isinstance(payload, dict):
            # El pipeline NO aborta ante fallos por-imagen: los cuenta y sigue, y
            # `run_task` los agrega aquí. Sin esta rama el CLI solo miraba el canal
            # `error` (excepciones que se propagan) y salía 0 aunque hubiese fallado
            # el 100% de las imágenes: en v3.4.24 las 3.743 térmicas de ANTOLIN se
            # quedaron sin un solo TIFF y Cloud Run marcó la ejecución como ÉXITO.
            # Un vuelo con imágenes perdidas es un fallo del vuelo, no un aviso.
            n = int(payload.get("errors") or 0)
            if n:
                errores.append(
                    f"el pipeline terminó con {n} error(es) por imagen/carpeta "
                    f"(status={payload.get('status')}); revisa «Imágenes con error» "
                    f"en el resumen de arriba")
            print(f"[done] {payload}", flush=True)
        elif kind in ("stats", "summary", "plan"):
            if not args.quiet:
                print(f"[{kind}] {payload}", flush=True)
        elif kind == "log" and not args.quiet:
            print(payload, flush=True)

    print(f"origen   : {origen}")
    print(f"destino  : {destino}")
    print(f"estadillo: {args.estadillo or '(ninguno)'}")
    print(f"task     : {args.task}")
    # La etapa y el shard van en la cabecera porque sin ellos un log de una tarea
    # de Cloud Run es indistinguible del de otra: las N escriben lo mismo salvo
    # QUÉ carpetas les tocaron, y al diagnosticar un vuelo incompleto lo primero
    # que hace falta saber es si una tarea se quedó sin trabajo.
    print(f"etapa    : {args.etapa}")
    print(f"shard    : {shard_index + 1}/{shard_count}\n", flush=True)

    try:
        run_task(args.task, params, emit, avanzado)
    except Exception as exc:  # noqa: BLE001 - el CLI reporta, no propaga traza cruda
        errores.append(f"{type(exc).__name__}: {exc}")
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)

    total = time.monotonic() - arranque
    if fases and fases[-1]["segundos"] is None:
        fases[-1]["segundos"] = round(time.monotonic() - fase_desde, 1)

    print("\n" + "=" * 60)
    for f in fases:
        print(f"  {f['nombre']:<32} {_formatear(f['segundos'] or 0):>10}")
    print(f"  {'TOTAL':<32} {_formatear(total):>10}")
    if errores:
        print(f"\n  {len(errores)} error(es); el primero: {errores[0]}")

    if args.como_json:
        print(json.dumps({
            "ok": not errores, "segundos": round(total, 1), "fases": fases,
            "errores": errores, "origen": str(origen), "destino": str(destino),
            "etapa": args.etapa, "shard_index": shard_index,
            "shard_count": shard_count,
            "host": platform.node(), "cpus": os.cpu_count(),
            # Lo consume quien recoja la salida del Job: `false` aquí es la
            # prueba en frío de que este run no llegó a /organizer, y el motivo
            # evita tener que ir a bucear en los logs para saber por qué.
            "telemetria": reporter is not None,
            "telemetria_motivo": telemetria_motivo,
        }, ensure_ascii=False))

    if reporter is not None:
        try:
            reporter.fin(ok=not errores, error=errores[0] if errores else None)
        except Exception:  # noqa: BLE001 - la telemetría no puede tocar el exit code
            pass

    return 1 if errores else 0


if __name__ == "__main__":
    # Igual que en `app_webview.py`: el pipeline usa ProcessPoolExecutor y en
    # Windows el start method es `spawn`, así que el hijo re-ejecuta este mismo
    # fichero. Sin esto, el pool se rompe entero en cuanto hay recorte RGB.
    multiprocessing.freeze_support()
    sys.exit(main())
