"""Prechequeo de una carpeta YA organizada, antes de subir el resultado al bucket.

SOLO LECTURA: no escribe nada en el destino ni crea `.organizado/`. Devuelve
`{ok, checks: [{id, titulo, estado, detalle}]}` con `estado` en
`ok|aviso|error|no_comprobable`. `ok` = ningun check en `error`.

Sin fallbacks silenciosos: una excepcion dentro de un check lo deja en `error`
con el mensaje, y los demas checks siguen. Si no hay manifiesto legible, el resto
sale `no_comprobable`.
"""
from __future__ import annotations

import csv
import datetime
import logging
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from atom_core import cierre, estadillo, subida_resultado, validacion_vuelos
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO, Manifiesto

logger = logging.getLogger(__name__)

MARGEN_VENTANA_S = 120
MAX_EJEMPLOS = 5
_EXTS_ESTADILLO = (".csv", ".xlsx", ".xls")
_RE_HORA_NOMBRE = re.compile(r"(?<!\d)(\d{8})_?(\d{6})(?!\d)")  # igual que exif.MetaLocation._ts_nombre
_RE_FECHA_FINAL = re.compile(r"^(.*)_(\d{8})$")

OK, AVISO, ERROR, NO_COMPROBABLE = "ok", "aviso", "error", "no_comprobable"

_TITULOS = {
    "manifiesto": "La carpeta está organizada y su registro se puede leer",
    "cierre": "Todas las imágenes están procesadas y existen en disco",
    "estadillo": "El estadillo se puede leer",
    "vuelos_exif": "Los vuelos del estadillo coinciden con las horas de las fotos",
    "giro": "El giro de las imágenes es coherente en cada vuelo",
    "colisiones": "Ninguna imagen pisa a otra con el mismo nombre",
    "sin_clasificar": "No hay ficheros sin clasificar ni imágenes sin ordenar",
    "csv_recuentos": "Las filas de los CSV coinciden con las imágenes de cada vuelo",
    "imagenes_en_ventana": "Cada imagen está dentro del horario de su vuelo",
    "tiff_crop": "Los TIFF y los recortes están completos",
    "horas_csv": "Las horas de los CSV son coherentes con su vuelo",
}


class _ManifiestoSoloLectura(Manifiesto):
    """`Manifiesto` que nunca escribe: sin PRAGMA journal_mode ni checkpoint."""

    def _conexion(self) -> sqlite3.Connection:
        con = getattr(self._local, "conexion", None)
        if con is not None:
            return con
        ruta = Path(self.ruta_db)
        wal = ruta.with_name(ruta.name + "-wal")
        # Sin WAL pendiente, `immutable=1` no toca el disco (ni crea -shm/-wal). Con un WAL
        # con datos hay que leerlo para ver todo el manifiesto: `mode=ro` (o, si no puede,
        # la conexion normal de abajo) puede crear -shm; es inevitable y no es del destino util.
        try:
            hay_wal = wal.is_file() and wal.stat().st_size > 0
        except OSError:
            hay_wal = True
        modo = "mode=ro" if hay_wal else "immutable=1"
        try:
            con = sqlite3.connect(f"{ruta.as_uri()}?{modo}", uri=True, timeout=5.0,
                                  check_same_thread=False)
            con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.Error:
            # WAL sin -shm: mode=ro no puede abrirlo. Conexion normal, solo SELECT.
            con = sqlite3.connect(str(ruta), timeout=5.0, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        self._local.conexion = con
        with self._lock_conexiones:
            self._conexiones_abiertas.append(con)
        return con

    def cerrar(self) -> None:
        with self._lock_conexiones:
            conexiones, self._conexiones_abiertas = self._conexiones_abiertas, []
        for con in conexiones:
            try:
                con.close()
            except sqlite3.Error:
                pass
        self._local.conexion = None


def _res(estado: str, detalle: str) -> tuple[str, str]:
    return estado, detalle


def _ejemplos(items, n: int = MAX_EJEMPLOS) -> str:
    items = [str(i) for i in items]
    resto = len(items) - n
    txt = "; ".join(items[:n])
    return txt + (f" (y {resto} más)" if resto > 0 else "")


def _norm(valor) -> str:
    return str(valor if valor is not None else "").strip().casefold()


def _es_generales(pb) -> bool:
    return _norm(pb) in ("generales", "general", "generales_pb")


def _hora_nombre(nombre: str):
    m = _RE_HORA_NOMBRE.search(Path(str(nombre)).name)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _filas_asignadas(filas):
    return [f for f in filas
            if f["estado"] == "hecho" and not f["unassigned"] and f["pb"] and f["vuelo"]
            and not _es_generales(f["pb"])]


def _carpeta_estadillos(destino: Path) -> Path | None:
    for hijo in sorted(destino.iterdir()):
        if hijo.is_dir() and hijo.name.casefold() == "estadillos":
            return hijo
    return None


def _ventanas_estadillo(ctx: dict) -> dict:
    """`{(pb, vuelo): (inicio, fin, nombre)}` del estadillo; error claro si no hay."""
    vuelos = ctx.get("vuelos_estadillo")
    if vuelos is None:
        raise _NoComprobable("no hay un estadillo válido en ESTADILLOS/")
    out: dict = {}
    for v in vuelos:
        clave = (_norm(v.get("pb")), _norm(v.get("num_vuelo")))
        if clave in out:
            continue  # ids duplicados en el estadillo: gana el primero (como validacion_vuelos)
        ini, fin = validacion_vuelos._ventana_vuelo(v, MARGEN_VENTANA_S)
        out[clave] = (ini, fin, validacion_vuelos._nombre_vuelo(v))
    return out


class _NoComprobable(Exception):
    pass


# ---------------------------------------------------------------- checks

def _chk_manifiesto(ctx):
    destino = ctx["destino"]
    ruta = destino / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    if not destino.is_dir():
        return _res(ERROR, f"La carpeta no existe: {destino}")
    if not ruta.is_file():
        return _res(ERROR, "No hay registro del organizado (.organizado/manifiesto.db): "
                           "esta carpeta no la ha organizado el Organizer.")
    # No se usa `destino_organizado()`: abre con `mode=ro` y, sobre un manifiesto en WAL,
    # crea `-shm`/`-wal` en el destino. Aqui la misma regla se comprueba con la conexion
    # de solo lectura de este modulo.
    man = _ManifiestoSoloLectura(ruta)
    try:
        tablas = {f[0] for f in man._conexion().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "imagenes" not in tablas:
            man.cerrar()
            return _res(ERROR, "El registro del organizado está corrupto o no es del Organizer.")
        filas = man.todas()
    except sqlite3.Error as exc:
        man.cerrar()
        return _res(ERROR, f"No se puede leer el registro del organizado: {exc}")
    ctx["manifiesto"], ctx["filas"] = man, filas
    return _res(OK, f"{len(filas)} imagen(es) registradas.")


_RE_AUSENTE = re.compile(r"^(?P<origen>.*): 'hecho' en el manifiesto pero (?P<ruta>.*) no existe en disco\.$")


def _raiz_origen(filas) -> str:
    """Carpeta común de los `ruta_origen` del manifiesto ('' si no hay)."""
    import ntpath  # noqa: PLC0415
    import os  # noqa: PLC0415
    rutas = [str(f["ruta_origen"]) for f in filas if f["ruta_origen"]]
    if not rutas:
        return ""
    mod = ntpath if any("\\" in r or re.match(r"^[A-Za-z]:", r) for r in rutas) else os.path
    try:
        return mod.commonpath([mod.dirname(r) for r in rutas])
    except ValueError:
        return mod.dirname(rutas[0])


def _chk_cierre(ctx):
    import os  # noqa: PLC0415
    cfg = SimpleNamespace(input_folder="", convert_to_tif=False, cropping_rgb=False)
    problemas = cierre.verificar(ctx["manifiesto"], cfg, None)
    nota = " (Sin carpeta de origen: todo lo registrado cuenta como de esta tanda.)"
    # Lo que falta en la SALIDA organizada es error; que la carpeta de ORIGEN (tarjeta SD)
    # ya no esté montada NO lo es: se avisa una vez, sin listar miles de rutas.
    ausentes, otros = [], []
    for p in problemas:
        m = _RE_AUSENTE.match(p)
        (ausentes if m else otros).append(p)
    origen = _raiz_origen(ctx["filas"])
    origen_ausente = bool(origen) and not os.path.exists(origen)
    # Todo lo ausente de la SALIDA organizada es error (la ruta del manifiesto es de salida).
    errores = list(otros) + list(ausentes)
    aviso_origen = (f"Carpeta de origen no accesible ({origen}); se valida contra la salida organizada."
                    if origen_ausente else "")
    if errores:
        extra = f" Aviso: {aviso_origen}" if aviso_origen else ""
        return _res(ERROR, f"{len(errores)} problema(s): {_ejemplos(errores)}.{nota}{extra}")
    if origen_ausente:
        return _res(AVISO, aviso_origen)
    return _res(OK, "Sin pendientes, fallidas, duplicados ni ficheros ausentes." + nota)


def _chk_estadillo(ctx):
    carpeta = _carpeta_estadillos(ctx["destino"])
    ficheros = sorted(str(p) for p in carpeta.iterdir()
                      if p.is_file() and p.suffix.lower() in _EXTS_ESTADILLO
                      and not p.name.startswith(".")) if carpeta else []
    if not ficheros:
        return _res(NO_COMPROBABLE, "No hay ficheros de estadillo en ESTADILLOS/.")
    res = estadillo.validar_para_subida(ficheros)
    if not res["ok"]:
        return _res(ERROR, str(res["error"]))
    ctx["vuelos_estadillo"] = res["vuelos"]
    if res["filas_con_problemas"]:
        return _res(AVISO, f"{res['filas_con_problemas']} fila(s) del estadillo sin PB, vuelo, fecha "
                           f"u hora de inicio válidos (de {res['vuelos_detectados']}).")
    return _res(OK, f"{len(res['vuelos'])} vuelo(s) en {len(ficheros)} fichero(s).")


def _chk_vuelos_exif(ctx):
    vuelos = ctx.get("vuelos_estadillo")
    if vuelos is None:
        raise _NoComprobable("no hay un estadillo válido en ESTADILLOS/")
    tiempos = [f["timestamp_exif"] for f in _filas_asignadas(ctx["filas"]) if f["timestamp_exif"]]
    res = validacion_vuelos.validar(vuelos, tiempos, MARGEN_VENTANA_S)
    if res["avisos"]:
        return _res(AVISO, _ejemplos(res["avisos"], 10))
    return _res(OK, f"{len(res['vuelos'])} vuelo(s) con fotos en su horario.")


def _chk_giro(ctx):
    vuelos = ctx["manifiesto"].vuelos()
    if not vuelos:
        return _res(NO_COMPROBABLE, "El registro no guarda el consenso de giro por vuelo.")
    malos = [f"PB{pb} V{v}{'_' + s if s else ''}: {d['estado']}" + (f" ({d['motivo']})" if d.get("motivo") else "")
             for (pb, v, s), d in vuelos.items() if d.get("estado") != "ok"]
    if malos:
        return _res(AVISO, f"{len(malos)} vuelo(s) sin consenso de giro claro: {_ejemplos(malos)}.")
    return _res(OK, f"{len(vuelos)} vuelo(s) con giro claro.")


def _chk_colisiones(ctx):
    col = ctx["manifiesto"].colisiones_ruta_salida_original()
    if col:
        return _res(ERROR, f"{len(col)} nombre(s) de salida repetidos (una imagen pisa a otra): "
                           f"{_ejemplos(f'{c[0]} ({c[1]} imágenes)' for c in col)}.")
    return _res(OK, "Sin colisiones.")


def _chk_sin_clasificar(ctx):
    sel = subida_resultado.seleccionar(ctx["destino"], "")
    if sel.avisos:
        return _res(AVISO, _ejemplos(sel.avisos, 10))
    return _res(OK, "Todo está clasificado.")


def _carpetas_a_vuelo(filas) -> dict:
    """`{basename de carpeta de ruta_salida_original: (pb, vuelo)}` (sin adivinar: si una
    carpeta apunta a dos vuelos, queda marcada ambigua con None)."""
    out: dict = {}
    for f in _filas_asignadas(filas):
        carpeta = Path(str(f["ruta_salida_original"]).replace("\\", "/")).parent.name
        clave = (f["pb"], f["vuelo"])
        if out.setdefault(carpeta, clave) != clave:
            out[carpeta] = None
    return out


def _vuelo_de_csv(nombre: str, carpetas: dict):
    """(pb, vuelo) del CSV `<carpeta>[_<YYYYMMDD>]_(meta|location).csv`, o None."""
    stem = re.sub(r"_(meta|location)\.csv$", "", nombre, flags=re.IGNORECASE)
    if carpetas.get(stem):
        return carpetas[stem]
    m = _RE_FECHA_FINAL.match(stem)
    if m and carpetas.get(m.group(1)):
        return carpetas[m.group(1)]
    return None


def _csvs(destino: Path) -> list[Path]:
    carpeta = destino / "CSVs"
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.iterdir()
                  if p.is_file() and re.search(r"_(meta|location)\.csv$", p.name, re.IGNORECASE))


def _leer_filas_csv(ruta: Path) -> list[list[str]]:
    with open(ruta, newline="", encoding="utf-8-sig") as fh:
        return [r for r in csv.reader(fh) if any(c.strip() for c in r)]


def _chk_csv_recuentos(ctx):
    csvs = _csvs(ctx["destino"])
    if not csvs:
        return _res(ERROR, "No hay CSV de meta/location en CSVs/.")
    carpetas = _carpetas_a_vuelo(ctx["filas"])
    filas_csv: dict = {}
    sin_vuelo = []
    for p in csvs:
        clave = _vuelo_de_csv(p.name, carpetas)
        if clave is None:
            sin_vuelo.append(p.name)
            continue
        tipo = "meta" if p.name.lower().endswith("_meta.csv") else "location"
        acum = filas_csv.setdefault(clave, {"meta": 0, "location": 0})
        acum[tipo] += len(_leer_filas_csv(p))
    rec = cierre.recuentos_por_vuelo(ctx["filas"], filas_csv, convert_to_tif=False, cropping_rgb=False)
    errores = [f"{cierre.etiqueta_vuelo(pb, v)}: {r['motivo']}" for (pb, v), r in rec.items()
               if r["estado"] == "ERROR"]
    partes = []
    if sin_vuelo:
        partes.append(f"{len(sin_vuelo)} CSV no se pueden asignar a ningún vuelo: {_ejemplos(sin_vuelo)}")
    if errores:
        partes.append(f"{len(errores)} vuelo(s) con filas que no cuadran: {_ejemplos(errores)}")
    if partes:
        return _res(ERROR, ". ".join(partes) + ".")
    return _res(OK, f"{len(csvs)} CSV cuadran con las imágenes de {len(filas_csv)} vuelo(s).")


def _chk_imagenes_en_ventana(ctx):
    ventanas = _ventanas_estadillo(ctx)
    fuera, sin_vuelo, sin_hora, total = [], 0, 0, 0
    for f in _filas_asignadas(ctx["filas"]):
        t = validacion_vuelos._a_madrid_naive(f["timestamp_exif"])
        if t is None:
            sin_hora += 1
            continue
        v = ventanas.get((_norm(f["pb"]), _norm(f["vuelo"])))
        if v is None or v[0] is None:
            sin_vuelo += 1
            continue
        total += 1
        if not (v[0] <= t <= v[1]):
            fuera.append(f"{Path(f['ruta_salida_original']).name} ({t:%H:%M:%S}, ventana "
                         f"{v[0]:%H:%M:%S}-{v[1]:%H:%M:%S} de {v[2]})")
    partes = []
    if fuera:
        partes.append(f"{len(fuera)} imagen(es) fuera del horario de su vuelo (puede incluir las que "
                      f"heredan vuelo de su pareja): {_ejemplos(fuera)}")
    if sin_vuelo:
        partes.append(f"{sin_vuelo} imagen(es) de un vuelo que no está en el estadillo")
    if sin_hora:
        partes.append(f"{sin_hora} imagen(es) sin hora EXIF")
    if partes:
        return _res(AVISO, ". ".join(partes) + ".")
    return _res(OK, f"{total} imagen(es) dentro del horario de su vuelo.")


def _chk_tiff_crop(ctx):
    return _res(NO_COMPROBABLE, "La configuración del run (TIFF/recorte) no está guardada en el manifiesto.")


def _chk_horas_csv(ctx):
    ventanas = _ventanas_estadillo(ctx)
    csvs = _csvs(ctx["destino"])
    if not csvs:
        return _res(ERROR, "No hay CSV de meta/location en CSVs/.")
    carpetas = _carpetas_a_vuelo(ctx["filas"])
    errores, fuera, n = [], [], 0
    for p in csvs:
        clave = _vuelo_de_csv(p.name, carpetas)
        if clave is None:
            errores.append(f"{p.name}: no se puede asignar a ningún vuelo")
            continue
        v = ventanas.get((_norm(clave[0]), _norm(clave[1])))
        if v is None or v[0] is None:
            errores.append(f"{p.name}: su vuelo (PB{clave[0]} V{clave[1]}) no tiene horario en el estadillo")
            continue
        filas = _leer_filas_csv(p)
        sin_hora = 0
        for fila in filas:
            t = _hora_nombre(fila[0])
            if t is None:
                sin_hora += 1
                continue
            n += 1
            if not (v[0] <= t <= v[1]):
                fuera.append(f"{p.name}: {Path(fila[0]).name} ({t:%H:%M:%S}, ventana "
                             f"{v[0]:%H:%M:%S}-{v[1]:%H:%M:%S})")
        if sin_hora:
            errores.append(f"{p.name}: {sin_hora} fila(s) sin hora legible (el CSV no lleva cabecera; "
                           f"la hora se lee del nombre de la foto)")
    partes = []
    if errores:
        partes.append(_ejemplos(errores))
    if fuera:
        partes.append(f"{len(fuera)} imagen(es) de CSV fuera del horario de su vuelo: {_ejemplos(fuera)}")
    if errores:
        return _res(ERROR, ". ".join(partes) + ".")
    if fuera:
        return _res(AVISO, ". ".join(partes) + ".")
    return _res(OK, f"{n} hora(s) de {len(csvs)} CSV dentro de su vuelo.")


_ORDEN = (
    ("cierre", _chk_cierre), ("estadillo", _chk_estadillo), ("vuelos_exif", _chk_vuelos_exif),
    ("giro", _chk_giro), ("colisiones", _chk_colisiones), ("sin_clasificar", _chk_sin_clasificar),
    ("csv_recuentos", _chk_csv_recuentos), ("imagenes_en_ventana", _chk_imagenes_en_ventana),
    ("tiff_crop", _chk_tiff_crop), ("horas_csv", _chk_horas_csv),
)


def _ejecutar(id_: str, fn, ctx) -> dict:
    try:
        estado, detalle = fn(ctx)
    except _NoComprobable as exc:
        estado, detalle = NO_COMPROBABLE, str(exc)
    except Exception as exc:  # noqa: BLE001 - un check roto no se traga ni aborta los demás
        logger.warning("prechequeo %s: %s", id_, exc, exc_info=True)
        estado, detalle = ERROR, f"No se pudo comprobar: {type(exc).__name__}: {exc}"
    return {"id": id_, "titulo": _TITULOS[id_], "estado": estado, "detalle": detalle}


def prechequear(destino: str) -> dict:
    """Prechequeo de solo lectura de `destino` (carpeta organizada) antes de subirla."""
    ctx: dict = {"destino": Path(str(destino))}
    checks = [_ejecutar("manifiesto", _chk_manifiesto, ctx)]
    try:
        if checks[0]["estado"] != OK:
            for id_, _fn in _ORDEN:
                checks.append({"id": id_, "titulo": _TITULOS[id_], "estado": NO_COMPROBABLE,
                               "detalle": "No se puede comprobar sin el registro del organizado."})
        else:
            checks += [_ejecutar(id_, fn, ctx) for id_, fn in _ORDEN]
    finally:
        man = ctx.get("manifiesto")
        if man is not None:
            man.cerrar()
    return {"ok": not any(c["estado"] == ERROR for c in checks), "checks": checks}
