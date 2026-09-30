"""Parche global para que subprocess no abra consolas negras en Windows.

exiftool.exe y dji_irp.exe (invocados desde ~30 puntos en pipeline.py,
utils.py, rjpeg_a_tiff.py y app_webview.py) abren una ventana de consola
sobre la GUI en cada llamada si no se les pasa CREATE_NO_WINDOW. En vez de
tocar cada call-site uno a uno, se parchea `subprocess.Popen.__init__` para
inyectar ese flag siempre que el llamante no haya pedido ya una consola
nueva explícita (CREATE_NEW_CONSOLE). Es un parche deliberado y global.
"""
from __future__ import annotations

import os
import subprocess

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010

_parchado = False


def aplicar() -> None:
    """Aplica el parche una sola vez. No-op en no-Windows o si ya se aplicó."""
    global _parchado
    if _parchado:
        return
    if os.name != "nt":
        return

    _init_original = subprocess.Popen.__init__

    def _init_parcheado(self, *args, **kwargs):
        creationflags = kwargs.get("creationflags", 0)
        if not (creationflags & _CREATE_NEW_CONSOLE):
            kwargs["creationflags"] = creationflags | _CREATE_NO_WINDOW
        return _init_original(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init_parcheado
    _parchado = True
