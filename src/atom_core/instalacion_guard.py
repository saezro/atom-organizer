"""Guardas de arranque (solo Windows) frente a una instalación en curso o a medias.

Dos comprobaciones baratas que corren ANTES de importar Qt/pywebview:

  1. Setup en marcha: el instalador Inno crea el mutex `ATOM-Organizer-Setup`
     (`SetupMutex` del .iss) mientras instala. Si el usuario reabre la app a mitad
     de una actualización, ésta bloquea `_internal\\PySide6\\QtCore.pyd`, Inno falla
     con DeleteFile código 5 y el rollback deja exe nuevo + _internal viejo.
  2. Instalación mezclada: el build escribe `_internal/.build-version`. Si no
     coincide con la versión del exe, hay mezcla → pedir reinstalar.

Sin marker (dev, builds antiguos) no se hace nada. `ctypes` es inyectable para tests.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MUTEX_SETUP = "ATOM-Organizer-Setup"
MARKER = ".build-version"
SYNCHRONIZE = 0x00100000
MB_OK_ICONINFO = 0x40
MB_OK_ICONERROR = 0x10

MSG_ACTUALIZANDO = ("ATOM Organizer se está actualizando. Espera a que termine; "
                    "se abrirá solo.")


ERROR_ACCESS_DENIED = 5


def _kernel32(ctypes_mod):
    """kernel32 con prototipos fijados si es el ctypes real; si es un doble de test
    (sin WinDLL), el `windll.kernel32` que traiga."""
    if hasattr(ctypes_mod, "WinDLL"):
        from ctypes import wintypes
        k32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)
        k32.OpenMutexW.restype = wintypes.HANDLE
        k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        return k32
    return ctypes_mod.windll.kernel32


def setup_en_marcha(ctypes_mod=None, mutex: str = MUTEX_SETUP) -> bool:
    """True si el mutex del instalador existe (hay una instalación en curso).
    ACCESS_DENIED también cuenta como «existe» (lo creó otro contexto de seguridad)."""
    if ctypes_mod is None:
        import ctypes as ctypes_mod
    try:
        k32 = _kernel32(ctypes_mod)
        handle = k32.OpenMutexW(SYNCHRONIZE, False, mutex)
        if not handle:
            get_err = getattr(ctypes_mod, "get_last_error", None)
            return bool(get_err and get_err() == ERROR_ACCESS_DENIED)
        k32.CloseHandle(handle)
        return True
    except Exception:
        return False


def leer_marker(base: str | os.PathLike | None = None) -> str | None:
    """Versión escrita por el build en `_internal/.build-version`, o None."""
    if base is None:
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            return None
    try:
        txt = (Path(base) / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return txt or None


def mensaje_mezcla(programa: str, archivos: str) -> str:
    return (f"Instalación incompleta (programa {programa}, archivos {archivos}). "
            "Reinstala ATOM Organizer.")


def _aviso(ctypes_mod, texto: str, flags: int) -> None:
    try:
        ctypes_mod.windll.user32.MessageBoxW(None, texto, "ATOM Organizer", flags)
    except Exception:
        pass


ESPERA_MAX = 60.0
ESPERA_PASO = 0.5


def esperar_fin_setup(ctypes_mod, *, maximo: float = ESPERA_MAX, paso: float = ESPERA_PASO,
                      sleep=time.sleep) -> bool:
    """Sondea hasta que el mutex desaparece. True si desapareció a tiempo."""
    esperado = 0.0
    while setup_en_marcha(ctypes_mod):
        if esperado >= maximo:
            return False
        sleep(paso)
        esperado += paso
    return True


def comprobar_arranque(*, version_programa: str, base: str | os.PathLike | None = None,
                       ctypes_mod=None, es_windows: bool | None = None,
                       tras_actualizar: bool = False, sleep=time.sleep) -> bool:
    """True si hay que SALIR (ya se avisó al usuario). False si se puede seguir."""
    if es_windows is None:
        es_windows = os.name == "nt"
    if not es_windows:
        return False
    if ctypes_mod is None:
        import ctypes as ctypes_mod
    if setup_en_marcha(ctypes_mod):
        # Relanzado por el [Run] del propio Setup: éste sigue vivo unos instantes.
        if tras_actualizar and esperar_fin_setup(ctypes_mod, sleep=sleep):
            return comprobar_arranque(version_programa=version_programa, base=base,
                                      ctypes_mod=ctypes_mod, es_windows=es_windows)
        _aviso(ctypes_mod, MSG_ACTUALIZANDO, MB_OK_ICONINFO)
        return True
    marker = leer_marker(base)
    if marker is not None and marker != version_programa:
        _aviso(ctypes_mod, mensaje_mezcla(version_programa, marker), MB_OK_ICONERROR)
        return True
    return False
