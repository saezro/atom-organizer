"""% de recorte (`_CROP`) por modelo de dron y cámara RGB.

Fuente por defecto: la tabla versionada `config/recortes_modelos.csv`
(columnas `modelo,camara,porcentaje,nota`; `camara` = W o Z), que viaja dentro
de cada versión del instalador y de la imagen del job. Override opcional: la
sección `[percentage_by_models]` del Config.ini de usuario, donde `MODELO` es la
cámara W (wide / genérica) y `MODELO_Z` la zoom; si existe, GANA sobre la tabla.

El resultado se aplana al diccionario histórico `{CLAVE: %}` (`MODELO` /
`MODELO_Z`) que el resto del motor ya consume, junto con `{CLAVE: origen}`
("tabla" | "override") para dejar en el log de dónde salió cada %.
"""
from __future__ import annotations

import csv
import os

from atom_core.camara_rgb import normalizar_camara

NOMBRE_TABLA = "recortes_modelos.csv"
ORIGEN_TABLA = "tabla"
ORIGEN_OVERRIDE = "override"
_COLUMNAS = ("modelo", "camara", "porcentaje", "nota")


def ruta_tabla_empaquetada() -> str:
    from external_tools import resource_path  # import perezoso: external_tools importa este paquete
    return resource_path("config", NOMBRE_TABLA)


def parse_porcentaje(clave: str, valor) -> "int | float":
    """`"48.5"` -> 48.5; `"70"` -> 70 (int). Coma decimal admitida. Fuera de (0, 100] -> ValueError."""
    try:
        numero = float(str(valor).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{clave}: '{valor}' no es un número") from None
    if not 0 < numero <= 100:
        raise ValueError(f"{clave}: {valor} fuera de (0, 100]")
    return int(numero) if numero.is_integer() else numero


def clave_de(modelo: str, camara: str) -> str:
    modelo = str(modelo).strip().upper()
    return modelo + "_Z" if normalizar_camara(camara) == "Z" else modelo


def cargar_tabla(ruta: str | None = None) -> "dict[tuple[str, str], dict]":
    """`{(MODELO, CAMARA): {"porcentaje", "nota"}}`. Fichero ausente, cabecera distinta, camara/%
    inválidos o (modelo, cámara) repetido -> error con fichero y línea (nunca se ignora)."""
    ruta = ruta or ruta_tabla_empaquetada()
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"Falta la tabla de recortes {ruta}")
    tabla: dict = {}
    with open(ruta, "r", encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        if tuple(lector.fieldnames or ()) != _COLUMNAS:
            raise ValueError(f"{ruta}: columnas {lector.fieldnames}, se esperaban {list(_COLUMNAS)}")
        for fila in lector:
            if not any((v or "").strip() for v in fila.values()):
                continue
            donde = f"{ruta}:{lector.line_num}"
            modelo = (fila["modelo"] or "").strip().upper()
            if not modelo:
                raise ValueError(f"{donde}: modelo vacío")
            try:
                camara = normalizar_camara(fila["camara"])
                pct = parse_porcentaje(f"{modelo}/{camara}", fila["porcentaje"])
            except ValueError as e:
                raise ValueError(f"{donde}: {e}") from None
            if (modelo, camara) in tabla:
                raise ValueError(f"{donde}: {modelo} {camara} repetido")
            tabla[(modelo, camara)] = {"porcentaje": pct, "nota": (fila["nota"] or "").strip()}
    return tabla


def _par_de_clave(clave: str) -> "tuple[str, str]":
    clave = str(clave).strip().upper()
    return (clave[:-2], "Z") if clave.endswith("_Z") else (clave, "W")


def tabla_efectiva(overrides: dict | None = None, ruta_tabla: str | None = None) -> "list[dict]":
    """Tabla efectiva para mostrar/consumir: una fila por (modelo, cámara) con
    `{"modelo", "camara", "porcentaje", "origen", "nota"}`; el override gana a la tabla."""
    filas = {par: {"modelo": par[0], "camara": par[1], "porcentaje": d["porcentaje"],
                   "origen": ORIGEN_TABLA, "nota": d["nota"]}
             for par, d in cargar_tabla(ruta_tabla).items()}
    for clave, valor in (overrides or {}).items():
        par = _par_de_clave(clave)
        filas[par] = {"modelo": par[0], "camara": par[1], "porcentaje": valor,
                      "origen": ORIGEN_OVERRIDE, "nota": filas.get(par, {}).get("nota", "")}
    return sorted(filas.values(), key=lambda r: (r["modelo"], r["camara"]))


def porcentajes_efectivos(overrides: dict | None = None,
                          ruta_tabla: str | None = None) -> "tuple[dict, dict]":
    """`({CLAVE: %}, {CLAVE: origen})` con el formato histórico (`MODELO` = W, `MODELO_Z` = Z)."""
    filas = tabla_efectiva(overrides, ruta_tabla)
    return ({clave_de(r["modelo"], r["camara"]): r["porcentaje"] for r in filas},
            {clave_de(r["modelo"], r["camara"]): r["origen"] for r in filas})
