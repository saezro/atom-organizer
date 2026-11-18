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


def desfase_por_carpeta(termicas, rgbs, max_segundos: float = 5.0) -> "dict[str, int]":
    """Desfase `idx_W - idx_T` por carpeta (DJI incrementa el contador por FICHERO:
    `DJI_0695_W` <-> `DJI_0696_T`, desfase 1). `termicas` y `rgbs` = iterables de
    `(carpeta, idx, timestamp|None)`. Por carpeta: moda de `idx_W - idx_T` entre T/W con
    timestamps a <= `max_segundos` (empate = sin evidencia); si no hay, el d de
    (0, 1, -1, 2, -2) que más T empareje con una W en `idx + d`. Solo carpetas con T."""
    from collections import Counter, defaultdict
    ts_t, ts_w = defaultdict(list), defaultdict(list)
    idx_t, idx_w = defaultdict(set), defaultdict(set)
    for car, idx, ts in termicas:
        idx_t[car].add(idx)
        if ts is not None:
            ts_t[car].append((idx, ts))
    for car, idx, ts in rgbs:
        idx_w[car].add(idx)
        if ts is not None:
            ts_w[car].append((idx, ts))
    out: "dict[str, int]" = {}
    for car, tset in idx_t.items():
        cuenta = Counter(iw - it for it, tt in ts_t.get(car, ())
                         for iw, tw in ts_w.get(car, ())
                         if abs((tw - tt).total_seconds()) <= max_segundos)
        top = cuenta.most_common(2)
        if top and (len(top) == 1 or top[0][1] > top[1][1]):
            out[car] = top[0][0]
            continue
        wset = idx_w.get(car, set())
        mejor, d_ok = -1, 0
        for d in (0, 1, -1, 2, -2):
            n = sum(1 for k in tset if (k + d) in wset)
            if n > mejor:
                mejor, d_ok = n, d
        out[car] = d_ok
    return out
