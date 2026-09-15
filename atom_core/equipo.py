"""¿El dron del estadillo es el que hizo las fotos?

`Equipo_de_vuelo` es texto libre ('DJI M300', 'Mavic 2EA', 'AT-M2EA-01') y el
EXIF trae el modelo de cámara ('M4T', 'MAVIC2-ENTERPRISE-ADVANCED', o solo el
payload 'XT2' en drones de cámara intercambiable). Se comparan por FAMILIA.
Sin BD: el Organizer trabaja offline. Solo informa (aviso + columna del
Excel); nunca bloquea: un estadillo copiado de otro día no para la organización."""
from __future__ import annotations

import re

# Familia -> alias normalizados (ver `normalizar`). En el estadillo se buscan
# como SUBCADENA ('ATM2EA01' contiene 'M2EA'); en el EXIF, exactos.
FAMILIAS: dict[str, tuple[str, ...]] = {
    "M4T": ("MATRICE4T", "M4T"),
    "M30T": ("MATRICE30T", "M30T"),
    "M3T": ("MAVIC3T", "M3T"),
    "M2EA": ("MAVIC2ENTERPRISEADVANCED", "MAVIC2EA", "M2EA"),
    "M300": ("MATRICE300", "M300"),
    "M350": ("MATRICE350", "M350"),
    "M200": ("MATRICE200", "MATRICE210", "M200", "M210"),
}

# Cámaras payload: el EXIF no dice el dron, solo en cuáles puede ir montada.
PAYLOADS: dict[str, frozenset[str]] = {
    "XT2": frozenset({"M200", "M300"}),
    "ZH20T": frozenset({"M300", "M350"}),
    "H20T": frozenset({"M300", "M350"}),
}


def normalizar(texto) -> str:
    """Mayúsculas y solo `[A-Z0-9]`: el mismo dron se escribe 'Matrice 4T',
    'MATRICE-4T' o 'M4T\\x00' según quién o qué firmware."""
    return re.sub(r"[^A-Z0-9]", "", str(texto or "").upper())


def familias_estadillo(texto) -> set[str]:
    n = normalizar(texto)
    return {familia for familia, alias in FAMILIAS.items() if any(a in n for a in alias)}


def familias_exif(modelo) -> set[str]:
    n = normalizar(modelo)
    if n.startswith("DJI"):
        n = n[3:]
    if n in PAYLOADS:
        return set(PAYLOADS[n])
    return {familia for familia, alias in FAMILIAS.items() if n in alias}


def equipo_coincide(texto_estadillo, modelo_exif) -> bool | None:
    """`True`/`False` si se puede decidir; `None` si el estadillo no nombra
    un dron reconocible o el EXIF no trae un modelo conocido (no se avisa)."""
    del_estadillo = familias_estadillo(texto_estadillo)
    del_exif = familias_exif(modelo_exif)
    if not del_estadillo or not del_exif:
        return None
    return bool(del_estadillo & del_exif)


def texto_coincide(valor: bool | None) -> str | None:
    if valor is None:
        return None
    return "Sí" if valor else "No"
