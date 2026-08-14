"""Lectura ligera del estadillo para el modal previo al procesado.

Extrae la info básica de vuelo que el pipeline ya consume (mismo formato CSV
`;` y el mismo mapeo de columnas ES/EN de `utils.Utils.get_nombres_columnas`),
SIN arrastrar Qt ni el pipeline: solo pandas + utils. Se llama on-demand desde
el bridge cuando el usuario elige un estadillo, antes de disparar la corrida.

`read_estadillo_info(path)` devuelve un dict JSON-serializable con:
  empresa, trabajo, fecha, pilotos[], drones[], num_vuelos,
  vuelos[{pb, vuelo, inicio, final}], hora_inicio, hora_final
o `{"error": "<motivo>"}` si no se puede leer.

--- Fusión de varios estadillos ---------------------------------------------

`--estadillo` (CLI) / `GenStructFolderConfig.estad` (pipeline) siguen siendo
UN string: no se toca esa forma porque atraviesa `atom_core/organize.py` y
`atom_core/phases.py`, fuera del alcance de este cambio. Para colar N rutas
por ese mismo hueco se empaquetan en un único string con `empaquetar_rutas` /
`desempaquetar_rutas`, usando `ESTADILLO_PATH_SEP` (un carácter que no
aparece en rutas reales) como separador. Un solo path, con o sin pasar por
`empaquetar_rutas`, se comporta exactamente igual que antes.

`combinar_estadillos(rutas)` lee y concatena N CSV/XLSX EN EL ORDEN dado (ese
orden es el que decide quién gana un solape de horario, ver pipeline.py),
alineando columnas POR NOMBRE (no por posición): dos variantes de cabecera
reales conviven en producción (33 columnas base, y esas mismas 33 + 2 extra
`Si_corresponde`/`No_corresponde` al final) e incluso coexisten dentro de una
misma planta, así que un estadillo que no trae una columna que otro sí queda
con NaN en esa columna para sus filas, no revienta la fusión. Solo se exige
que cada fichero traiga las columnas ESENCIALES que el pipeline necesita sí o
sí (PB, Vuelo, Fecha, Hora_de_inicio, Hora_final); si falta alguna,
`EstadilloHeaderError` indica fichero y columna. `detectar_colisiones_pb_vuelo`
localiza qué pares (PB, Vuelo) aparecen con fecha distinta tras la fusión: son
los únicos a los que `gen_folder_struct` les añade sufijo de fecha a la
carpeta; el resto conserva el nombre `PB<pb>_V<vuelo>` de siempre.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pandas as pd

from utils import OrganizerLogger, Utils

# Separador de rutas dentro del string único que viaja como "estadillo" por
# `GenStructFolderConfig.estad`. Un carácter de control (Unit Separator, no
# imprimible) no puede aparecer en un path real en ningún SO soportado, así
# que no hay ambigüedad posible con nombres de fichero que lo contengan.
ESTADILLO_PATH_SEP = "\x1f"

# Columna interna que se añade a cada fila del DataFrame fusionado con el
# fichero de origen, para trazabilidad. Antepuesta con "_" y en minúsculas
# para que no choque nunca con una cabecera real del estadillo (todas las
# columnas conocidas, ES o EN, van en mayúsculas iniciales, ver
# `Utils.get_nombres_columnas`).
COLUMNA_ORIGEN = "_estadillo_origen"


class EstadilloHeaderError(ValueError):
    """A un estadillo que se quiere fusionar le falta una columna ESENCIAL
    (la fusión en sí tolera columnas distintas entre ficheros; ver
    `combinar_estadillos`)."""

# Instancia mínima reutilizable solo para `get_nombres_columnas` (no usa el
# logger ni I/O; create_file_handler=False evita crear ficheros de log).
_UTILS = Utils.__new__(Utils)  # sin __init__: get_nombres_columnas no toca self

# Columnas (clave interna de `Utils.get_nombres_columnas`) sin las que
# `gen_folder_struct` no puede ubicar un vuelo: PB+Vuelo nombran la carpeta,
# Fecha+horas la desambiguan y detectan colisiones. El resto de columnas del
# estadillo (Tipologia, Si_corresponde/No_corresponde...) son opcionales para
# el pipeline y su ausencia en alguno de los N ficheros es normal.
_COLUMNAS_ESENCIALES = ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"]


def _validar_columnas_esenciales(df: pd.DataFrame, ruta: str) -> None:
    """Lanza `EstadilloHeaderError` si a `df` le falta alguna de
    `_COLUMNAS_ESENCIALES`, con el fichero y la(s) columna(s) en el mensaje."""
    mapeo = _UTILS.get_nombres_columnas(list(df.columns))
    faltantes = [mapeo[clave] for clave in _COLUMNAS_ESENCIALES if mapeo.get(clave) not in df.columns]
    if faltantes:
        raise EstadilloHeaderError(
            f"'{os.path.basename(ruta)}' no trae la(s) columna(s) esencial(es) {faltantes}: "
            f"sin ellas el pipeline no puede ubicar el vuelo (PB/Vuelo/Fecha/horas).")


def _read_dataframe(path: str) -> pd.DataFrame:
    """Lee el estadillo. Mirror del pipeline (CSV `;`) con fallback a Excel para
    que el modal informativo no reviente si el fichero es .xlsx/.xls."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path, sep=";")


def empaquetar_rutas(paths: list[str]) -> str:
    """N rutas -> un único string, para colar varios estadillos por el hueco
    de siempre (`--estadillo`, `GenStructFolderConfig.estad`). Con 1 sola
    ruta el resultado es esa ruta tal cual, sin separador: el caso de 1
    estadillo (99% de los usos) queda byte a byte igual que hoy."""
    limpias = [str(p).strip() for p in (paths or []) if str(p).strip()]
    return ESTADILLO_PATH_SEP.join(limpias)


def desempaquetar_rutas(path_estadillo: str) -> list[str]:
    """Inversa de `empaquetar_rutas`. Si `path_estadillo` no tiene el
    separador (el caso de siempre, un único path) devuelve `[path_estadillo]`
    tal cual."""
    if not path_estadillo:
        return []
    return [p.strip() for p in str(path_estadillo).split(ESTADILLO_PATH_SEP) if p.strip()]


def combinar_estadillos(rutas: list[str]) -> pd.DataFrame:
    """Lee y concatena N estadillos EN EL ORDEN dado.

    El orden importa: es el desempate de `gen_folder_struct` cuando dos
    vuelos solapan ("gana el primero"), así que aquí se preserva tal cual
    con `pd.concat(..., ignore_index=True)` en vez de reordenar por lo que
    sea (fecha, PB...).

    Cada fila se marca con su fichero de origen en `COLUMNA_ORIGEN`, para
    trazar de qué CSV vino tras la fusión.

    Las columnas se alinean POR NOMBRE, no por posición: hay dos variantes de
    cabecera reales en producción (una con `Si_corresponde`/`No_corresponde`
    al final, otra sin ellas) que a veces coexisten dentro de la misma
    planta. `pd.concat` ya alinea por etiqueta de columna y rellena con NaN
    la columna que un fichero no traiga, así que fusionar A+B no es un error:
    las filas que vienen del fichero sin esas columnas quedan con NaN ahí, y
    las que sí las traen conservan su valor.

    Lanza `EstadilloHeaderError` únicamente si a algún estadillo le falta una
    columna ESENCIAL (`_COLUMNAS_ESENCIALES`): sin PB/Vuelo/Fecha/horas el
    pipeline no puede ubicar el vuelo, y eso sí hay que cortarlo aquí con un
    mensaje claro en vez de dejar pasar filas fantasma.
    """
    rutas = [r for r in (rutas or []) if r]
    if not rutas:
        raise ValueError("combinar_estadillos: no se ha pasado ninguna ruta de estadillo")

    frames: list[pd.DataFrame] = []
    for ruta in rutas:
        df = _read_dataframe(ruta)
        _validar_columnas_esenciales(df, ruta)
        df = df.copy()
        df[COLUMNA_ORIGEN] = os.path.basename(ruta)
        frames.append(df)

    return pd.concat(frames, ignore_index=True)


def detectar_colisiones_pb_vuelo(df: pd.DataFrame, nombres_columnas: dict) -> dict:
    """Pares (PB, Vuelo) que aparecen más de una vez con FECHA distinta tras
    fusionar estadillos. Solo esos deben llevar sufijo de fecha en el nombre
    de carpeta; el resto conserva `PB<pb>_V<vuelo>` sin tocar.

    Devuelve `{(pb, vuelo): [fechas]}`; vacío si no hay ninguna colisión (el
    caso de 1 solo estadillo, o de N que no repiten PB+Vuelo)."""
    col_pb = nombres_columnas.get("PB")
    col_vuelo = nombres_columnas.get("Vuelo")
    col_fecha = nombres_columnas.get("Fecha")
    if not (col_pb in df.columns and col_vuelo in df.columns and col_fecha in df.columns):
        return {}

    fechas_por_par: dict[tuple, list] = {}
    for pb, vuelo, fecha in zip(df[col_pb], df[col_vuelo], df[col_fecha]):
        clave = (str(pb).strip(), str(vuelo).strip())
        vistas = fechas_por_par.setdefault(clave, [])
        fecha_s = str(fecha).strip()
        if fecha_s not in vistas:
            vistas.append(fecha_s)
    return {clave: fechas for clave, fechas in fechas_por_par.items() if len(fechas) > 1}


def sufijo_fecha(fecha: str) -> str:
    """`'2026:08:14'` -> `'20260814'`, para el sufijo de carpeta de un vuelo
    con PB+Vuelo colisionado entre estadillos. El pipeline parsea la fecha
    del estadillo como `%Y:%m:%d` (ver `Pipeline.ventana_horaria_vuelo`); si
    la celda no encaja en ese formato no se revienta -esto es solo un sufijo
    de desambiguación de carpeta, no una fecha que se vaya a re-parsear-, se
    sanea el texto tal cual dejando solo alfanuméricos."""
    t = str(fecha).strip()
    try:
        return datetime.strptime(t, "%Y:%m:%d").strftime("%Y%m%d")
    except ValueError:
        limpio = re.sub(r"[^0-9A-Za-z]+", "", t)
        return limpio or "SINFECHA"


def _col(df: pd.DataFrame, cols: dict, key: str):
    """Serie de la columna mapeada `key`, o None si no existe en el estadillo."""
    name = cols.get(key)
    if name and name in df.columns:
        return df[name]
    return None


def _uniques(series) -> list:
    """Valores únicos, en orden de aparición, sin nan/vacíos, como strings."""
    if series is None:
        return []
    out: list[str] = []
    for v in series.tolist():
        s = "" if v is None else str(v).strip()
        if s and s.lower() != "nan" and s not in out:
            out.append(s)
    return out


def _first(series) -> str:
    vals = _uniques(series)
    return vals[0] if vals else ""


# El pipeline parsea la fecha del estadillo como '%Y:%m:%d' (pipeline.py:1002),
# pero el modal es informativo y puede toparse con un .xlsx donde pandas ya la
# haya convertido, o con la variante europea. Se normaliza sólo para ORDENAR;
# lo que se muestra es siempre el texto original de la celda.
_FMT_FECHA = ("%Y:%m:%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d",
              "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S")


def _sortable(fecha: str) -> str:
    """'01/08/2026' -> '2026-08-01'. Si no se reconoce, devuelve el texto tal
    cual: ordenar mal es preferible a reventar el modal por una celda rara."""
    t = (fecha or "").strip()
    if not t:
        return ""
    for fmt in _FMT_FECHA:
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return t


# Formatos de fecha que puede traer la celda `Fecha` de un estadillo real: el
# formato EXIF con dos puntos (`2026:03:17`, el que escribe el dron) es el
# habitual, pero se toleran también guion y barra por si el CSV/XLSX ya viene
# normalizado o exportado a mano.
_FMT_FECHA_SUITE = ("%Y:%m:%d", "%Y-%m-%d", "%Y/%m/%d")


def _normalizar_fecha_suite(texto: str) -> str | None:
    """`'2026:03:17'` / `'2026-03-17'` / `'2026/03/17'` -> `'2026-03-17'`.

    `None` si la celda no encaja en ninguno de los formatos conocidos: la fila
    se descarta en `filas_para_suite` en vez de mandar una fecha inventada."""
    t = (texto or "").strip()
    if not t:
        return None
    for fmt in _FMT_FECHA_SUITE:
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _normalizar_hora_suite(texto: str) -> str | None:
    """`'08:30'` -> `'08:30:00'`; `'08:30:00'` se deja tal cual. `None` si la
    celda está vacía o no encaja en ninguno de los dos formatos."""
    t = (texto or "").strip()
    if not t:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(t, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return None


def filas_para_suite(df: pd.DataFrame, origen_por_fila: list | None = None) -> list[dict]:
    """`df` (el DataFrame combinado de `combinar_estadillos`) -> lista de dicts
    lista para el body de `POST /api/organizer/estadillo` (`RunReporter.estadillo`).

    Reutiliza el mismo mapeo de columnas (`Utils.get_nombres_columnas`) que el
    resto del módulo, así que ES/EN y las dos variantes de cabecera (con o sin
    `Si_corresponde`/`No_corresponde`) se resuelven igual que en
    `read_estadillo_info`.

    - `origen` por fila: si se pasa `origen_por_fila` (lista paralela a las
      filas de `df`) se usa esa; si no, y `df` trae `COLUMNA_ORIGEN` (la que
      añade `combinar_estadillos`), se usa esa; si no hay ninguna de las dos,
      `origen` queda `None`.
    - Filas sin PB, sin Vuelo, sin Fecha parseable o sin Hora_de_inicio
      parseable se descartan (el backend las descartaría igual).
    - Las horas se normalizan a `HH:MM:SS`; `Hora_final` queda `None` si la
      celda está vacía o no se puede parsear (es opcional en el contrato).
    """
    cols = _UTILS.get_nombres_columnas(list(df.columns.values))
    pb_s = _col(df, cols, "PB")
    vuelo_s = _col(df, cols, "Vuelo")
    fecha_s = _col(df, cols, "Fecha")
    inicio_s = _col(df, cols, "Hora_de_inicio")
    final_s = _col(df, cols, "Hora_final")
    piloto_s = _col(df, cols, "Piloto")
    equipo_s = _col(df, cols, "Equipo_de_vuelo")
    origen_s = df[COLUMNA_ORIGEN] if COLUMNA_ORIGEN in df.columns else None

    def cell(s, i):
        if s is None:
            return ""
        v = s.iloc[i]
        t = "" if v is None else str(v).strip()
        return "" if t.lower() == "nan" else t

    filas: list[dict] = []
    for i in range(len(df)):
        pb = cell(pb_s, i)
        num_vuelo = cell(vuelo_s, i)
        if not pb or not num_vuelo:
            continue

        fecha = _normalizar_fecha_suite(cell(fecha_s, i))
        if fecha is None:
            continue

        hora_inicio = _normalizar_hora_suite(cell(inicio_s, i))
        if hora_inicio is None:
            continue

        if origen_por_fila is not None:
            origen = origen_por_fila[i] if i < len(origen_por_fila) else None
        elif origen_s is not None:
            v = origen_s.iloc[i]
            origen = str(v).strip() if v is not None and str(v).strip() else None
        else:
            origen = None

        filas.append({
            "fecha": fecha,
            "piloto": cell(piloto_s, i) or None,
            "equipo_vuelo": cell(equipo_s, i) or None,
            "pb": pb,
            "num_vuelo": num_vuelo,
            "hora_inicio": hora_inicio,
            "hora_fin": _normalizar_hora_suite(cell(final_s, i)),
            "origen": origen,
        })

    return filas


def read_estadillo_info(path: str | list[str]) -> dict:
    """Resumen para el modal previo al procesado.

    `path` admite lo de siempre (un string con una sola ruta) y además:
    - una lista de rutas (`["a.csv", "b.csv"]`), o
    - un string con varias rutas empaquetadas por `empaquetar_rutas`.

    Con 1 sola ruta el resultado es idéntico al de siempre (un solo
    `_read_dataframe`, sin pasar por la fusión). Con N, se fusionan con
    `combinar_estadillos` y el resto de esta función -que ya trabaja sobre
    `df` en genérico- agrega la info de todas por igual, sin más cambios."""
    rutas = list(path) if isinstance(path, (list, tuple)) else desempaquetar_rutas(path)
    if not rutas:
        return {"error": f"No existe el estadillo: {path}"}
    faltantes = [r for r in rutas if not os.path.isfile(r)]
    if faltantes:
        return {"error": f"No existe el estadillo: {', '.join(faltantes)}"}
    try:
        df = _read_dataframe(rutas[0]) if len(rutas) == 1 else combinar_estadillos(rutas)
    except EstadilloHeaderError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — se reenvía al front
        return {"error": f"No se pudo leer el estadillo ({type(exc).__name__}): {exc}"}

    cols = _UTILS.get_nombres_columnas(list(df.columns.values))

    pb_s = _col(df, cols, "PB")
    vuelo_s = _col(df, cols, "Vuelo")
    fecha_s = _col(df, cols, "Fecha")
    inicio_s = _col(df, cols, "Hora_de_inicio")
    final_s = _col(df, cols, "Hora_final")

    vuelos = []
    marcas_ini: list[tuple] = []
    marcas_fin: list[tuple] = []
    for i in range(len(df)):
        def cell(s):
            if s is None:
                return ""
            v = s.iloc[i]
            t = "" if v is None else str(v).strip()
            return "" if t.lower() == "nan" else t

        fec, ini, fin = cell(fecha_s), cell(inicio_s), cell(final_s)
        if ini:
            marcas_ini.append((_sortable(fec), ini, fec))
        if fin:
            marcas_fin.append((_sortable(fec), fin, fec))
        vuelos.append({
            "pb": cell(pb_s),
            "vuelo": cell(vuelo_s),
            # Cada vuelo lleva SU fecha: el estadillo puede cubrir varios días
            # (una campaña con vuelos repartidos), y un rango horario suelto
            # sin día es ambiguo para el operador que revisa el resumen.
            "fecha": fec,
            "inicio": ini,
            "final": fin,
            # El pipeline construye la ventana [inicio, fin] con la MISMA fecha
            # de la fila (pipeline.py:1002-1003). Si el final es anterior al
            # inicio, el vuelo cruza medianoche, la ventana sale invertida y
            # NINGUNA imagen cae dentro: acaban todas en SIN_ORDENAR. Se marca
            # aquí para que se vea antes de procesar, no después.
            "cruza_medianoche": bool(ini and fin and fin < ini),
        })

    # Cronológicas, no en orden de aparición: las filas del estadillo no tienen
    # por qué venir ordenadas y "01/08, 03/08, 02/08" se lee como un error.
    fechas = sorted(_uniques(fecha_s), key=_sortable)
    ini_min = min(marcas_ini) if marcas_ini else None
    fin_max = max(marcas_fin) if marcas_fin else None

    return {
        "empresa": _first(_col(df, cols, "Empresa")),
        "trabajo": _first(_col(df, cols, "Trabajo")),
        # `fecha` se mantiene (la primera) por compatibilidad; `fechas` es la
        # lista completa y es lo que debe pintar la UI cuando hay más de una.
        "fecha": fechas[0] if fechas else "",
        "fechas": fechas,
        "pilotos": _uniques(_col(df, cols, "Piloto")),
        "drones": _uniques(_col(df, cols, "Equipo_de_vuelo")),
        "num_vuelos": len(df),
        "vuelos": vuelos,
        # Extremos reales de la campaña: se ordenan por (fecha, hora), no por
        # hora suelta. Con varios días, un `min()` sobre horas sin fecha daba
        # una franja que no se corresponde con ningún vuelo real.
        "hora_inicio": ini_min[1] if ini_min else "",
        "hora_final": fin_max[1] if fin_max else "",
        "fecha_inicio": ini_min[2] if ini_min else "",
        "fecha_final": fin_max[2] if fin_max else "",
    }
