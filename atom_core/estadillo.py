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
    inicio_s = _col(df, cols, "Hora_de_inicio")
    final_s = _col(df, cols, "Hora_final")

    vuelos = []
    inicios: list[str] = []
    finales: list[str] = []
    for i in range(len(df)):
        def cell(s):
            if s is None:
                return ""
            v = s.iloc[i]
            t = "" if v is None else str(v).strip()
            return "" if t.lower() == "nan" else t

        ini, fin = cell(inicio_s), cell(final_s)
        if ini:
            inicios.append(ini)
        if fin:
            finales.append(fin)
        vuelos.append({
            "pb": cell(pb_s),
            "vuelo": cell(vuelo_s),
            "inicio": ini,
            "final": fin,
        })

    return {
        "empresa": _first(_col(df, cols, "Empresa")),
        "trabajo": _first(_col(df, cols, "Trabajo")),
        "fecha": _first(_col(df, cols, "Fecha")),
        "pilotos": _uniques(_col(df, cols, "Piloto")),
        "drones": _uniques(_col(df, cols, "Equipo_de_vuelo")),
        "num_vuelos": len(df),
        "vuelos": vuelos,
        "hora_inicio": min(inicios) if inicios else "",
        "hora_final": max(finales) if finales else "",
    }
