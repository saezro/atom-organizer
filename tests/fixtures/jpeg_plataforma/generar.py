"""Imágenes RGB sintéticas y deterministas para la sonda de paridad libjpeg-turbo (Task P).

Sin datos de cliente. `python generar.py` imprime/regraba `huellas.json` con el sha256
de los píxeles de ENTRADA (no de los JPEG): sirve para comprobar que las tres
plataformas parten de los mismos bytes antes de comparar encoders.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent


def gradiente() -> tuple[int, int, bytes]:
    w, h = 4000, 3000
    x = np.arange(w, dtype=np.uint32)[None, :]
    y = np.arange(h, dtype=np.uint32)[:, None]
    a = np.empty((h, w, 3), dtype=np.uint8)
    a[..., 0] = (x * 255 // (w - 1)).astype(np.uint8)
    a[..., 1] = (y * 255 // (h - 1)).astype(np.uint8)
    a[..., 2] = ((x + y) * 255 // (w + h - 2)).astype(np.uint8)
    return w, h, a.tobytes()


def ruido() -> tuple[int, int, bytes]:
    w, h = 4001, 3001  # impares: ejercita el borde del 4:2:0
    rs = np.random.RandomState(4001)  # MT19937: estable entre versiones y plataformas
    return w, h, rs.randint(0, 256, size=(h, w, 3), dtype=np.uint8).tobytes()


def tablero() -> tuple[int, int, bytes]:
    w, h = 640, 512
    x = (np.arange(w)[None, :] // 16)
    y = (np.arange(h)[:, None] // 16)
    c = (((x + y) % 2) * 255).astype(np.uint8)
    a = np.stack([c, 255 - c, np.full_like(c, 128)], axis=-1)
    return w, h, np.ascontiguousarray(a).tobytes()


IMAGENES = {"gradiente": gradiente, "ruido": ruido, "tablero": tablero}


def huellas() -> dict[str, str]:
    return {n: hashlib.sha256(f()[2]).hexdigest() for n, f in IMAGENES.items()}


if __name__ == "__main__":
    (AQUI / "huellas.json").write_text(json.dumps(huellas(), indent=2) + "\n", encoding="utf-8")
    print((AQUI / "huellas.json").read_text())
