"""Puente Python<->Rust (``atom_rs``): flags por fase y carga estricta del módulo.

Flags (se leen del entorno en CADA llamada, sin cachear estado):
  - ``ATOM_RUST=1``            activa todas las fases de ``FASES``.
  - ``ATOM_RUST_FASES=a,b``    activa solo esas fases (lista separada por comas).
Sin ninguno de los dos, el módulo NO se importa jamás: el comportamiento es el
de Python puro. Con un flag activo, que falte ``atom_rs``, que su ABI no
coincida o que no exponga los símbolos de una fase activa (wheel vieja) es un
error (``RustNoDisponible``), nunca un retroceso silencioso a Python.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Optional

FASES: tuple[str, ...] = ("termica", "indice", "rgb", "subida")

# Debe coincidir con ``ABI`` en rust/atom_rs/src/lib.rs.
# 2 = fase ``termica`` (T1): una wheel ABI 1 no trae ``termica_procesar``.
# 3 = sonda libjpeg-turbo (P).
# 4 = fase ``subida`` (T6): una wheel ABI 3 no trae ``subir_lote``.
ABI_ESPERADA: int = 4

# Símbolos que ``atom_rs`` debe exponer para cada fase activa. Se validan en
# ``modulo()`` (arranque de ``organize.run_task`` y cada ``exigir``), así una
# wheel sin la función de una fase activa aborta al arrancar, no a mitad.
SIMBOLOS_POR_FASE: dict[str, tuple[str, ...]] = {
    "termica": ("termica_procesar",),
    "indice": (),
    "rgb": (),
    "subida": ("subir_lote",),
}

_VERDADEROS = {"1", "true", "yes", "on", "si", "sí"}


class RustNoDisponible(RuntimeError):
    """Flag Rust activo pero ``atom_rs`` no se puede usar."""


def _fases_activas() -> tuple[str, ...]:
    crudo = os.environ.get("ATOM_RUST_FASES", "").strip()
    if crudo:
        pedidas = [f.strip().lower() for f in crudo.split(",") if f.strip()]
        for f in pedidas:
            if f not in FASES:
                raise ValueError(
                    f"ATOM_RUST_FASES: fase desconocida {f!r}. Válidas: {', '.join(FASES)}."
                )
        return tuple(f for f in FASES if f in pedidas)
    if os.environ.get("ATOM_RUST", "").strip().lower() in _VERDADEROS:
        return FASES
    return ()


def fases_activas() -> tuple[str, ...]:
    """Fases con Rust activo según el entorno actual (vacío = flag off)."""
    return _fases_activas()


def fase_activa(fase: str) -> bool:
    if fase not in FASES:
        raise ValueError(f"Fase Rust desconocida: {fase!r}. Válidas: {', '.join(FASES)}.")
    return fase in _fases_activas()


def modulo(fases: Optional[tuple[str, ...]] = None) -> types.ModuleType:
    """Importa ``atom_rs`` y valida su ABI y los símbolos de ``fases`` (por
    defecto, las activas en el entorno). Falla siempre con ``RustNoDisponible``."""
    try:
        m = importlib.import_module("atom_rs")
    except Exception as e:  # ImportError, módulo None en sys.modules, DLL rota...
        raise RustNoDisponible(
            f"No se pudo importar atom_rs ({type(e).__name__}: {e}). "
            f"Intérprete: {sys.executable}. Instala la wheel atom_rs o desactiva ATOM_RUST."
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
        fases = _fases_activas()
    faltan = [f"{f}: {s}" for f in fases for s in SIMBOLOS_POR_FASE.get(f, ())
              if not hasattr(m, s)]
    if faltan:
        raise RustNoDisponible(
            f"atom_rs no expone los símbolos de las fases activas ({', '.join(faltan)}). "
            f"Wheel anterior a esas fases: reinstálala o quítalas de ATOM_RUST_FASES. "
            f"Intérprete: {sys.executable}."
        )
    return m


def exigir(fase: str) -> Optional[types.ModuleType]:
    """None solo si la fase NO está activa; activa y sin módulo válido -> raise."""
    if not fase_activa(fase):
        return None
    return modulo((fase,))
