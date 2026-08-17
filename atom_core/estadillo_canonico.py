"""Ubicación canónica de los estadillos en el bucket.

Funciones puras: calculan rutas, nombres y manifest. No tocan red ni disco,
para que la ruta de un estadillo sea reproducible y testeable sin bucket.
"""

import base64
from datetime import datetime

from atom_core.cloud_config import prefijo_desde_carpeta

PREFIJO_ESTADILLOS = "PREPARACION/ESTADILLOS"
NOMBRE_MANIFEST = "manifest.json"
NOMBRE_NORMALIZADO = "estadillo.json"
CARPETA_ACTUAL = "actual"

VERSION_MANIFEST = 1
VERSION_NORMALIZADO = 1


def carpeta_subida(ahora: datetime) -> str:
    """Identificador de subida: fecha+hora UTC, ordenable alfabéticamente.

    No se usa el run_id de la Suite a propósito: lo genera el servidor y sin
    login no existe, así que la ruta no puede depender de él.
    """
    return ahora.strftime("%Y-%m-%dT%H%M%SZ")


def prefijo_planta(planta: str) -> str:
    return f"{prefijo_desde_carpeta(planta)}/{PREFIJO_ESTADILLOS}"


def nombre_objeto(orden: int, md5_hex: str, ext: str) -> str:
    sufijo = ext if ext.startswith(".") else f".{ext}"
    return f"{orden:02d}__{md5_hex[:8]}{sufijo}"


def md5_hex_desde_b64(md5_b64: str) -> str:
    return base64.b64decode(md5_b64).hex()


def construir_manifest(
    planta: str,
    subido_en: datetime,
    subido_por: str | None,
    ficheros: list[dict],
    validacion: dict,
) -> dict:
    """Manifest de una subida. Es la fuente de verdad del orden de prioridad.

    El nombre que puso el operario se conserva como metadato, nunca como
    identidad: la identidad es `objeto`.
    """
    return {
        "version": VERSION_MANIFEST,
        "planta": prefijo_desde_carpeta(planta),
        "subido_en": carpeta_subida(subido_en),
        "subido_por": subido_por,
        "ficheros": sorted(ficheros, key=lambda f: f["orden"]),
        "validacion": {
            "vuelos_detectados": validacion.get("vuelos_detectados", 0),
            "filas_con_problemas": validacion.get("filas_con_problemas", 0),
        },
    }


def construir_normalizado(vuelos: list[dict]) -> dict:
    """Formato estable del estadillo, independiente de si el crudo es xlsx o csv."""
    return {"version": VERSION_NORMALIZADO, "vuelos": vuelos}
