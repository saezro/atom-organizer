"""Puente Python<->Rust (``atom_rs``): carga estricta del módulo.

Rust es el único camino: no hay flags ni fases opcionales. Que falte
``atom_rs``, que su ABI no coincida o que no exponga los símbolos de una fase
(wheel vieja) es un error visible (``RustNoDisponible``), nunca un retroceso
silencioso a Python.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Optional

FASES: tuple[str, ...] = ("termica", "indice", "rgb", "subida", "io")

# Debe coincidir con ``ABI`` en rust/atom_rs/src/lib.rs.
# 2 = fase ``termica`` (T1): una wheel ABI 1 no trae ``termica_procesar``.
# 3 = sonda libjpeg-turbo (P).
# 4 = fase ``subida`` (T6): una wheel ABI 3 no trae ``subir_lote``.
# 5 = fase ``indice`` (T2): una wheel ABI 4 no trae ``parsear_cabecera``.
# 6 = fase ``rgb`` (T3): una wheel ABI 5 no trae ``rgb_procesar``.
# 7 = fase ``io`` (4a): una wheel ABI 6 no trae ``leer_completo``/``ErrorFila``.
# 8 = solo Rust: ``rgb_procesar`` (caja i64, L/CMYK, TIFF/PNG, decode tolerante) y
#     ``parsear_cabecera`` (firma nueva, ``necesita_completo``/``formato``).
ABI_ESPERADA: int = 8

# Símbolos que ``atom_rs`` debe exponer para cada fase activa. Se validan en
# ``modulo()`` (arranque de ``organize.run_task`` y cada ``exigir``), así una
# wheel sin la función de una fase activa aborta al arrancar, no a mitad.
SIMBOLOS_POR_FASE: dict[str, tuple[str, ...]] = {
    "termica": ("termica_procesar",),
    "indice": ("parsear_cabecera",),
    "rgb": ("rgb_procesar",),
    "subida": ("subir_lote",),
    "io": ("leer_completo", "jpeg_integro_por_cola"),
}

class RustNoDisponible(RuntimeError):
    """``atom_rs`` no se puede usar (falta, ABI distinta o faltan símbolos)."""


def modulo(fases: Optional[tuple[str, ...]] = None) -> types.ModuleType:
    """Importa ``atom_rs`` y valida su ABI y los símbolos de ``fases`` (por
    defecto, todas las de ``FASES``). Falla siempre con ``RustNoDisponible``."""
    try:
        m = importlib.import_module("atom_rs")
    except Exception as e:  # ImportError, módulo None en sys.modules, DLL rota...
        raise RustNoDisponible(
            f"No se pudo importar atom_rs ({type(e).__name__}: {e}). "
            f"Intérprete: {sys.executable}. Instala la wheel atom_rs."
        ) from e
    try:
        abi = m.version_abi()
    except Exception as e:
        raise RustNoDisponible(
            f"atom_rs no expone version_abi() ({type(e).__name__}: {e}). "
            f"Intérprete: {sys.executable}."
        ) from e
    if abi != ABI_ESPERADA:
        raise RustNoDisponible(
            f"ABI de atom_rs incompatible: módulo={abi}, esperada={ABI_ESPERADA}. "
            f"Reinstala la wheel de esta versión. Intérprete: {sys.executable}."
        )
    if fases is None:
        fases = FASES
    for f in fases:
        if f not in SIMBOLOS_POR_FASE:
            raise ValueError(f"Fase Rust desconocida: {f!r}. Válidas: {', '.join(FASES)}.")
    faltan = [f"{f}: {s}" for f in fases for s in SIMBOLOS_POR_FASE[f]
              if not hasattr(m, s)]
    if faltan:
        raise RustNoDisponible(
            f"atom_rs no expone los símbolos requeridos ({', '.join(faltan)}). "
            f"Wheel anterior: reinstálala. Intérprete: {sys.executable}."
        )
    return m


def exigir(fase: str) -> types.ModuleType:
    """Devuelve ``atom_rs`` validado (ABI + símbolos de ``fase``) o lanza
    ``RustNoDisponible``. Nunca devuelve None."""
    return modulo((fase,))


class PanicRust(RuntimeError):
    """Un ``panic`` de Rust dentro de una fila, convertido en excepción visible
    (``ErrorFila.tipo == "panic"``). Nunca se reintenta por Python."""


def _es_error_fila(exc: BaseException) -> bool:
    # Por nombre: no se importa ``atom_rs`` aquí.
    return type(exc).__name__ == "ErrorFila" and hasattr(exc, "tipo")


def traducir_error(exc: BaseException) -> BaseException:
    """``atom_rs.ErrorFila`` -> la excepción que habría lanzado el camino Python
    (mismo tipo y mismo ``str``). Cualquier otra excepción se devuelve tal cual.
    Las devueltas conservan ``__reduce__``: el pool de procesos las serializa."""
    if not _es_error_fila(exc):
        return exc
    tipo = exc.tipo
    mensaje = str(exc.args[0]) if exc.args else ""
    ruta = getattr(exc, "ruta", None)
    if tipo == "lectura_incompleta":
        from atom_core.lectura_segura import LecturaIncompleta
        return LecturaIncompleta(ruta, exc.leidos, exc.esperados, exc.intentos)
    if tipo == "fichero_cambiado":
        from atom_core.lectura_segura import FicheroCambiado
        return FicheroCambiado(ruta, exc.actual, exc.indexado)
    if tipo == "jpeg_truncado_origen":
        # Mismo texto que `apply._leer_origen_verificado` (cola a ceros/sin EOI).
        return OSError("JPEG truncado en origen (cola a ceros/sin EOI): " + str(ruta))
    if tipo == "xmp_grande":
        from atom_core.apply import XmpDemasiadoGrandeError
        return XmpDemasiadoGrandeError(mensaje)
    if tipo == "io":
        winerror = getattr(exc, "winerror", None)
        if winerror and sys.platform == "win32":
            # Código Win32 (no errno): OSError(None, msg, ruta, winerror) deja que
            # Python elija FileNotFoundError/PermissionError como en el camino Python.
            return OSError(None, mensaje, ruta, winerror)
        errno_ = getattr(exc, "errno", None)
        if errno_:
            # OSError(errno, strerror, filename) elige la subclase (FileNotFoundError...)
            # y el `str` "[Errno 2] No such file or directory: '...'" como Python.
            return OSError(errno_, os.strerror(errno_), ruta)
        return OSError(mensaje)
    if tipo == "verificacion":
        return OSError(mensaje)
    if tipo == "panic":
        return PanicRust(mensaje)
    return RuntimeError(f"atom_rs.ErrorFila de tipo desconocido {tipo!r}: {mensaje}")
