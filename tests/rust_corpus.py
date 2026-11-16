"""Localiza el corpus de pruebas para A/B Rust vs Python (solo banco, nunca CI).

Variable ``ATOM_RUST_CORPUS=<dir>``: carpeta de planta PRUEBA con >=60 térmicas
``*_T.JPG`` y >=150 RGB. Sin la variable, ``corpus()`` devuelve None y los tests
que lo necesiten deben saltarse (``pytest.skip``), no inventar rutas.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

VARIABLE = "ATOM_RUST_CORPUS"


def corpus() -> Optional[Path]:
    crudo = os.environ.get(VARIABLE, "").strip()
    if not crudo:
        return None
    p = Path(crudo)
    if not p.is_dir():
        raise FileNotFoundError(f"{VARIABLE}={crudo!r} no es una carpeta")
    return p
