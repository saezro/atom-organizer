"""Lectura directa (sqlite3, sin pasar por `Manifiesto`) de la tabla `vuelos` que deja
la fase de rotación en `<raiz>/.organizado/manifiesto.db`. Los tests de rotación la
usan en vez del antiguo `CSVs/_criterio/<vuelo>_Videofiles.csv`."""
import os
import sqlite3


def ruta_manifiesto(raiz) -> str:
    return os.path.join(str(raiz), ".organizado", "manifiesto.db")


def leer_vuelos(raiz) -> dict:
    """`{nombre_carpeta: fila_dict}` de `vuelos` (`PB<pb>_V<vuelo>[_<sufijo>]`).
    `{}` si no hay manifiesto."""
    ruta = ruta_manifiesto(raiz)
    if not os.path.isfile(ruta):
        return {}
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    try:
        filas = [dict(f) for f in con.execute("SELECT * FROM vuelos")]
    finally:
        con.close()
    resultado = {}
    for f in filas:
        nombre = f"PB{f['pb']}_V{f['vuelo']}" + (f"_{f['sufijo']}" if f["sufijo"] else "")
        resultado[nombre] = f
    return resultado


def angulo_de(raiz, nombre_vuelo):
    """`angulo` registrado para `nombre_vuelo`; falla si no hay fila."""
    vuelos = leer_vuelos(raiz)
    assert nombre_vuelo in vuelos, f"No hay fila en `vuelos` para {nombre_vuelo}: {sorted(vuelos)}"
    return vuelos[nombre_vuelo]["angulo"]


def csv_videofiles_existentes(raiz) -> list:
    return [os.path.join(d, f) for d, _, fs in os.walk(str(raiz)) for f in fs
            if f.endswith("_Videofiles.csv")]
