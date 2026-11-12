"""Paridad T<->W por idx DJI (función pura; el cableado en el índice va aparte)."""
from __future__ import annotations

import os
import re

# Clave única de pareja T/W: idx DJI (contador `_NNNN_`) + sufijo T|W|V (V = RGB Mavic 3T, equivale a W y se normaliza a 'W'), con `_pointN`
# opcional (H30T). Vale `DJI_<ts>_<idx>_T`, `<fecha>_<hora>_DJI_<idx>_W` y `..._T_point1`.
# El timestamp NO entra: T y W de un mismo disparo pueden diferir 1 s en el nombre.
_RE_CLAVE_PAREJA = re.compile(r"_(\d+)_([TWVZ])(?:_point\d+)?$", re.IGNORECASE)


def clave_pareja(nombre: str, rgb: str = "W") -> "tuple[int, str] | None":
    """`(idx, 'T'|'W')` del nombre (sin carpeta ni extensión) o None si no sigue el
    patrón. Función ÚNICA de clave: la usan la paridad del índice y el cierre
    (`exif.MetaLocation.emparejar_por_idx`). El idx se reinicia entre vuelos, así que
    solo tiene sentido dentro de la lista de UN vuelo.

    `rgb` = cámara RGB que forma pareja con la T: "W" (por defecto; la `_Z` NO tiene
    clave, como siempre) o "Z" (tanda H30T en modo `camara_rgb=Z`: la `_Z` se
    normaliza a 'W' y la `_W`/`_V` no tienen clave). Así los llamadores no cambian."""
    m = _RE_CLAVE_PAREJA.search(os.path.splitext(os.path.basename(str(nombre)))[0])
    if not m:
        return None
    suf = m.group(2).upper()
    if suf == "Z":
        return (int(m.group(1)), "W") if rgb == "Z" else None
    if rgb == "Z" and suf in ("W", "V"):
        return None
    return (int(m.group(1)), "W" if suf == "V" else suf)


def paridad_tw(rutas, rgb: str = "W") -> "tuple[list[str], list[str]]":
    """Devuelve (idx con T sin W, idx con W sin T) por idx DJI (`clave_pareja`).
    Ignora nombres sin patrón (p. ej. `_Z`, salvo `rgb="Z"`: tanda en modo Z, la `_Z` es
    la RGB que forma pareja y la `_W` se ignora; ver `clave_pareja`). Se llama por vuelo: la pareja se busca
    dentro de la lista recibida. Los idx se devuelven con 4 dígitos mínimo."""
    t, w = set(), set()
    for ruta in rutas:
        c = clave_pareja(ruta, rgb)
        if c:
            (t if c[1] == "T" else w).add(c[0])
    return ([f"{i:04d}" for i in sorted(t - w)], [f"{i:04d}" for i in sorted(w - t)])


def idx_toma(nombre: str) -> "int | None":
    """idx DJI de la toma (`_NNNN_`) para CUALQUIER sufijo T/W/V/Z, a diferencia de
    `clave_pareja` (que deja fuera la `_Z`). Sirve para mantener juntas las fotos de una
    misma toma (T+W+Z). None si el nombre no sigue el patrón."""
    m = _RE_CLAVE_PAREJA.search(os.path.splitext(os.path.basename(str(nombre)))[0])
    return int(m.group(1)) if m else None
