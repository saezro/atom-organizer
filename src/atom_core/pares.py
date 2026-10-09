"""Paridad T<->W por idx DJI (función pura; el cableado en el índice va aparte)."""
from __future__ import annotations

import os
import re

# `DJI_<ts>_<idx>_T|W` (tarjeta SD) y `<fecha>_<hora>_DJI_<idx>_T|W` (ya renombrada).
_RE_PAREJA_DJI = re.compile(
    r"^(?:DJI_(\d{8,})_(\d+)|(\d{8}_\d{6})_DJI_(\d+))_([TW])(?:_point\d+)?\.", re.IGNORECASE)


def paridad_tw(rutas) -> "tuple[list[str], list[str]]":
    """Devuelve (idx con T sin W, idx con W sin T) por (timestamp, idx) DJI.
    Ignora nombres que no sean `DJI_<ts>_<idx>_T|W` (p. ej. `_Z`). Se llama por
    vuelo: la pareja se busca dentro de la lista recibida."""
    t, w = set(), set()
    for ruta in rutas:
        m = _RE_PAREJA_DJI.match(os.path.basename(str(ruta)))
        if m:
            clave = (m.group(1) or m.group(3), m.group(2) or m.group(4))
            (t if m.group(5).upper() == "T" else w).add(clave)
    return ([i for _, i in sorted(t - w)], [i for _, i in sorted(w - t)])
