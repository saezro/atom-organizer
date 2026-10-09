"""Paridad T<->W por idx DJI (función pura; el cableado en el índice va aparte)."""
from __future__ import annotations

import os
import re

# Clave única de pareja T/W: idx DJI (contador `_NNNN_`) + sufijo T|W, con `_pointN`
# opcional (H30T). Vale `DJI_<ts>_<idx>_T`, `<fecha>_<hora>_DJI_<idx>_W` y `..._T_point1`.
# El timestamp NO entra: T y W de un mismo disparo pueden diferir 1 s en el nombre.
_RE_CLAVE_PAREJA = re.compile(r"_(\d+)_([TW])(?:_point\d+)?$", re.IGNORECASE)


def clave_pareja(nombre: str) -> "tuple[int, str] | None":
    """`(idx, 'T'|'W')` del nombre (sin carpeta ni extensión) o None si no sigue el
    patrón. Función ÚNICA de clave: la usan la paridad del índice y el cierre
    (`exif.MetaLocation.emparejar_por_idx`). El idx se reinicia entre vuelos, así que
    solo tiene sentido dentro de la lista de UN vuelo."""
    m = _RE_CLAVE_PAREJA.search(os.path.splitext(os.path.basename(str(nombre)))[0])
    return (int(m.group(1)), m.group(2).upper()) if m else None


def paridad_tw(rutas) -> "tuple[list[str], list[str]]":
    """Devuelve (idx con T sin W, idx con W sin T) por idx DJI (`clave_pareja`).
    Ignora nombres sin patrón (p. ej. `_Z`). Se llama por vuelo: la pareja se busca
    dentro de la lista recibida. Los idx se devuelven con 4 dígitos mínimo."""
    t, w = set(), set()
    for ruta in rutas:
        c = clave_pareja(ruta)
        if c:
            (t if c[1] == "T" else w).add(c[0])
    return ([f"{i:04d}" for i in sorted(t - w)], [f"{i:04d}" for i in sorted(w - t)])
