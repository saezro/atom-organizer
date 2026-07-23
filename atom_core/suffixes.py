"""Autodetección del sufijo de separación RGB/térmica desde los nombres de
archivo de la carpeta origen, para que el operador NO tenga que escribirlo.

Los drones DJI nombran de forma consistente: cada disparo térmico termina en
`_T` (p.ej. `DJI_0001_T.JPG`) y las RGB en `_W` (gran angular) / `_Z` (zoom) /
`_V`, o sin sufijo en cámaras de un solo sensor. El pipeline separa por el
sufijo FINAL del nombre (`stem.endswith(sufijo)`, pipeline.py:1748/1755), así
que basta mirar la terminación de cada nombre.

`detect_suffixes(origen)` devuelve, con el MISMO criterio de imagen que
`utils.get_images_from_dir` (jpg/png/JPG), el sufijo térmico y de RGB
recomendados + el recuento de terminaciones encontradas. Sin Qt ni pipeline:
solo `os`. Se llama on-demand desde el bridge al elegir la carpeta origen.
"""
from __future__ import annotations

import os

# Terminaciones DJI conocidas (para clasificar lo detectado; NO es una lista
# cerrada — cualquier token `_XX` se cuenta igual).
_THERMAL = "_T"
_RGB_TOKENS = ("_W", "_Z", "_V")
_IMG_EXT = ("jpg", "png", "JPG")


def _stem_suffix(stem: str) -> str | None:
    """Token final tras el último `_` (incluido el `_`), o None si no hay `_`.
    `DJI_20240101_0001_T` -> `_T`; `DJI_0001_W` -> `_W`; `IMG1234` -> None."""
    i = stem.rfind("_")
    if i == -1:
        return None
    return stem[i:]


def detect_suffixes(origen: str, max_scan: int = 4000) -> dict:
    """Escanea `origen` (recursivo, tope `max_scan` imágenes) y recomienda el
    sufijo de separación.

    Devuelve dict JSON-serializable:
      {ok, thermal, rgb, tokens: {tok: n}, total, no_suffix, error?}

    Regla de recomendación:
      - Si hay archivos `_T`  -> thermal="_T", rgb="" (todo lo no-`_T` va a RGB;
        es la rama robusta del pipeline: thermal_sufix!="" y rgb_sufix=="").
      - Si NO hay `_T` pero sí tokens RGB conocidos -> thermal="", rgb=<esos>.
      - Si no hay terminaciones -> ok=False (que el operador lo ponga a mano).
    """
    if not origen or not os.path.isdir(origen):
        return {"ok": False, "error": f"No existe la carpeta: {origen}",
                "thermal": "", "rgb": "", "tokens": {}, "total": 0, "no_suffix": 0}

    tokens: dict[str, int] = {}
    total = 0
    no_suffix = 0
    for root, _dirs, files in os.walk(origen):
        for f in files:
            if not f.endswith(_IMG_EXT):
                continue
            total += 1
            stem = f.rsplit(".", 1)[0]
            tok = _stem_suffix(stem)
            if tok is None:
                no_suffix += 1
            else:
                tokens[tok] = tokens.get(tok, 0) + 1
            if total >= max_scan:
                break
        if total >= max_scan:
            break

    if total == 0:
        return {"ok": False, "error": "La carpeta no tiene imágenes (jpg/png).",
                "thermal": "", "rgb": "", "tokens": {}, "total": 0, "no_suffix": 0}

    thermal = _THERMAL if tokens.get(_THERMAL) else ""
    if thermal:
        rgb = ""  # catch-all: todo lo que no acabe en _T -> RGB
    else:
        rgb_found = [t for t in _RGB_TOKENS if tokens.get(t)]
        rgb = ",".join(rgb_found)

    return {
        "ok": bool(thermal or rgb),
        "thermal": thermal,
        "rgb": rgb,
        "tokens": tokens,
        "total": total,
        "no_suffix": no_suffix,
    }
