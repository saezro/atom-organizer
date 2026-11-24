"""Lectura segura de `cola_subidas`: ausente, OSError transitorio, JSON corrupto."""
from __future__ import annotations

from pathlib import Path

import pytest

from atom_core import cola_subidas as cs


def test_ausente_es_vacia(tmp_path):
    assert cs.pendientes(ruta=tmp_path / "c.json") == []


def test_oserror_persistente_lanza_y_no_pisa(tmp_path, monkeypatch):
    ruta = tmp_path / "c.json"
    cs.encolar(str(tmp_path), "p", 1, ruta=ruta)
    antes = ruta.read_text(encoding="utf-8")
    real = Path.read_text
    monkeypatch.setattr(cs, "_LECTURA_ESPERA_S", 0)

    def falla(self, *a, **k):
        raise PermissionError("x")
    monkeypatch.setattr(Path, "read_text", falla)
    with pytest.raises(cs.ColaIlegible):
        cs.encolar(str(tmp_path / "otra"), "q", 1, ruta=ruta)
    monkeypatch.setattr(Path, "read_text", real)
    assert ruta.read_text(encoding="utf-8") == antes


def test_corrupto_se_aparta(tmp_path):
    ruta = tmp_path / "c.json"
    ruta.write_text("{no", encoding="utf-8")
    cs.tomar_avisos()
    assert cs.pendientes(ruta=ruta) == []
    assert len(list(tmp_path.glob("c.json.corrupto-*"))) == 1
    assert cs.tomar_avisos()
