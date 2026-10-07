"""Subida del RESULTADO organizado (salida local) a `gs://plantas_pv_nl/...`.

Fase 1 de «el Organizer sube el resultado al bucket». Reutiliza `cloud_upload`
(XML resumable, MD5, 16 hilos, `ifGenerationMatch=0`); este módulo pone lo
específico: qué ficheros suben y en qué orden (urgencia / normal), qué se puede
sobrescribir, cómo se detectan conflictos y cuándo se dispara solo.

Las reglas de sobrescritura REPLICAN las de la Suite
(`lib/organizer-resultado.js::SUBPREFIJOS_SOBRESCRIBIBLES`): si cambian allí,
cambian aquí. El token que emite la Suite solo permite sobrescribir en `CSVs/`,
`ESTADILLOS/` e `INDICE_*`; el resto da 403, no «pisa en silencio».
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import cloud_upload as cu
from .manifiesto import NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO

logger = logging.getLogger(__name__)

BUCKET_RESULTADO = "plantas_pv_nl"
AMBITO_RESULTADO = "resultado"

MODO_URGENCIA = "urgencia"
MODO_NORMAL = "normal"
MODOS = (MODO_URGENCIA, MODO_NORMAL)

URGENTE = "urgente"
NORMAL = "normal"

NOMBRE_SIN_ORDENAR = "SIN_ORDENAR"
NOMBRE_MANIFIESTO_SUBIDA = "subida_resultado.json"

# Carpetas que nunca se suben ni cuentan como «sin clasificar».
_EXCLUIDAS = {NOMBRE_CARPETA_MANIFIESTO.lower(), "logs"}

# Prefijos (relativos a la salida) donde SE PUEDE sobrescribir. Igual que la Suite.
_SOBRESCRIBIBLES = ("CSVs/", "ESTADILLOS/", "INDICE_")


def es_sobrescribible(rel: str) -> bool:
    return rel.startswith(_SOBRESCRIBIBLES)


def clasificar(rel: str) -> str | None:
    """`URGENTE` / `NORMAL` / `None` para una ruta relativa POSIX de la salida.

    `None` = ni se sube ni se ignora en silencio: `seleccionar` lo cuenta como
    «sin clasificar» y lo avisa. Los `SIN_ORDENAR/` seleccionados por manifiesto
    NO pasan por aquí (los trata `seleccionar`).
    """
    partes = rel.split("/")
    top, nombre = partes[0], partes[-1]
    bajo = nombre.lower()
    if top.lower() in _EXCLUIDAS:
        return None
    if top == "TERMICA" and len(partes) >= 2:
        if bajo.endswith("_t.tiff"):
            return URGENTE
        if bajo.endswith("_t.jpg"):
            return NORMAL
    elif top == "RGB" and len(partes) >= 2:
        if bajo.endswith("_w_crop.jpg"):
            return URGENTE
        if bajo.endswith("_w.jpg"):
            return NORMAL
    elif top == "CSVs":
        if len(partes) == 2 and (bajo.endswith("_meta.csv") or bajo.endswith("_location.csv")):
            return URGENTE
        if len(partes) == 3 and partes[1] == "_criterio":
            return NORMAL
    elif top == "ESTADILLOS" and len(partes) == 2:
        return URGENTE
    elif len(partes) == 1 and nombre.startswith("INDICE_") and bajo.endswith(".xlsx"):
        return NORMAL
    return None


def sin_ordenar_ultima_ejecucion(destino: Path) -> tuple[set[str], list[str]]:
    """Rutas relativas (bajo `SIN_ORDENAR/`) de las imágenes que, según el estado
    ACTUAL del manifiesto (de cualquier ejecución), siguen sin asignar (original,
    `_CROP` y TIFF) y se escribieron bien. Una fila `hecho` conserva su
    `ejecucion_id` antiguo aunque siga sin asignar, por eso no se filtra por ejecución.
    El nombre se mantiene por compatibilidad con los consumidores (Task 4).

    Sin manifiesto o ilegible NO se inventa nada: se devuelve vacío + un aviso que
    la UI enseña (sin fallbacks silenciosos).
    """
    destino = Path(destino)
    ruta = destino / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    if not ruta.is_file():
        return set(), ["Sin manifiesto del organizado: no se pueden subir las imágenes sin asignar."]
    filas: list = []
    try:
        con = sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True, timeout=5.0)
        try:
            filas = con.execute(
                "SELECT ruta_salida_original, ruta_salida_crop, ruta_salida_tiff FROM imagenes "
                "WHERE unassigned = 1 AND estado = 'hecho'").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return set(), [f"No se pudo leer el manifiesto ({exc}): no se suben las imágenes sin asignar."]
    base = destino.resolve()
    rels: set[str] = set()
    for fila in filas:
        for ruta_s in fila:
            if not ruta_s:
                continue
            try:
                rel = Path(ruta_s).resolve().relative_to(base).as_posix()
            except (ValueError, OSError):
                continue  # fuera de la salida (gs://, otro disco): no es de esta carpeta
            if rel.split("/")[0] == NOMBRE_SIN_ORDENAR and (base / rel).is_file():
                rels.add(rel)
    if filas and not rels:
        return rels, ["El manifiesto tiene imágenes sin asignar pero ninguna está bajo esta carpeta "
                      "(¿se movió el destino o cambió de unidad?): no se suben."]
    return rels, []


@dataclass
class Seleccion:
    urgentes: list = field(default_factory=list)
    resto: list = field(default_factory=list)
    sin_clasificar: int = 0
    avisos: list = field(default_factory=list)


def seleccionar(destino, prefijo: str) -> Seleccion:
    """Recorre la salida con `build_plan` (sin filtro de extensiones) y reparte."""
    destino = Path(destino)
    plan = cu.build_plan(destino, prefijo, suffixes=())  # () = sin filtro; ya salta `.organizado`
    sin_ord, avisos = sin_ordenar_ultima_ejecucion(destino)
    sel = Seleccion(avisos=list(avisos))
    for item in plan.items:
        rel = item.local.relative_to(destino).as_posix()
        top = rel.split("/")[0]
        if rel in sin_ord:
            sel.resto.append(item)
            continue
        if top.lower() in _EXCLUIDAS or top == NOMBRE_SIN_ORDENAR:
            continue
        clase = clasificar(rel)
        if clase == URGENTE:
            sel.urgentes.append(item)
        elif clase == NORMAL:
            sel.resto.append(item)
        else:
            sel.sin_clasificar += 1
            logger.warning("resultado: sin clasificar %s", rel)
    if sel.sin_clasificar:
        sel.avisos.append(f"{sel.sin_clasificar} fichero(s) sin clasificar: no se suben (ver log).")
    return sel
