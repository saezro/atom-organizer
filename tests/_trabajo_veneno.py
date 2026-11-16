"""Trabajo de fila a nivel de módulo (picklable por referencia) para los tests de pool roto."""
from __future__ import annotations

import os

import pipeline
from atom_core import apply
from tests.test_apply_rgb_pool_roto import VENENO


def trabajo(fila, cfg):
    if VENENO in fila["ruta_origen"]:
        os._exit(1)
    return apply._escribir_salidas_de_fila(fila, cfg, pipeline)
