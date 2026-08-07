"""Lectura ligera del estadillo para el modal previo al procesado.

Extrae la info básica de vuelo que el pipeline ya consume (mismo formato CSV
`;` y el mismo mapeo de columnas ES/EN de `utils.Utils.get_nombres_columnas`),
SIN arrastrar Qt ni el pipeline: solo pandas + utils. Se llama on-demand desde
el bridge cuando el usuario elige un estadillo, antes de disparar la corrida.

`read_estadillo_info(path)` devuelve un dict JSON-serializable con:
  empresa, trabajo, fecha, pilotos[], drones[], num_vuelos,
  vuelos[{pb, vuelo, inicio, final}], hora_inicio, hora_final
o `{"error": "<motivo>"}` si no se puede leer.
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

from utils import OrganizerLogger, Utils

# Instancia mínima reutilizable solo para `get_nombres_columnas` (no usa el
# logger ni I/O; create_file_handler=False evita crear ficheros de log).
_UTILS = Utils.__new__(Utils)  # sin __init__: get_nombres_columnas no toca self


def _read_dataframe(path: str) -> pd.DataFrame:
    """Lee el estadillo. Mirror del pipeline (CSV `;`) con fallback a Excel para
    que el modal informativo no reviente si el fichero es .xlsx/.xls."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path, sep=";")


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


def read_estadillo_info(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {"error": f"No existe el estadillo: {path}"}
    try:
        df = _read_dataframe(path)
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
