"""Persistencia de geometría de ventana: JSON aparte de Config.ini."""
from __future__ import annotations

import json

import pytest

from atom_core import window_state


@pytest.fixture(autouse=True)
def _ruta_aislada(tmp_path, monkeypatch):
    """Redirige `ruta_estado()` a un fichero bajo `tmp_path`, sin tocar disco real."""
    destino = tmp_path / "config" / "ventana.json"
    monkeypatch.setattr(window_state, "ruta_estado", lambda: str(destino))
    return destino


def test_guardar_y_leer_round_trip():
    estado = {"ancho": 1200, "alto": 800, "x": 50, "y": 60, "maximizada": False}
    assert window_state.guardar(estado) is True
    assert window_state.leer() == estado


def test_leer_fichero_inexistente_devuelve_none():
    assert window_state.leer() is None


def test_leer_json_corrupto_devuelve_none(_ruta_aislada):
    _ruta_aislada.parent.mkdir(parents=True, exist_ok=True)
    _ruta_aislada.write_text("esto no es json {{{", encoding="utf-8")
    assert window_state.leer() is None


@pytest.mark.parametrize(
    "datos",
    [
        {"ancho": -100, "alto": 800, "x": None, "y": None, "maximizada": True},
        {"ancho": 1200, "alto": "800", "x": None, "y": None, "maximizada": True},
        {"ancho": 1200, "alto": 800, "x": None, "y": None},  # falta 'maximizada'
        {"ancho": 100, "alto": 100, "x": None, "y": None, "maximizada": True},  # < 400
    ],
)
def test_leer_valores_invalidos_devuelve_none(_ruta_aislada, datos):
    _ruta_aislada.parent.mkdir(parents=True, exist_ok=True)
    _ruta_aislada.write_text(json.dumps(datos), encoding="utf-8")
    assert window_state.leer() is None


def test_resized_y_moved_se_ignoran_mientras_maximizada():
    estado = window_state.EstadoVentana(ancho=1100, alto=760, x=10, y=20, maximizada=True)
    original = estado.snapshot()

    estado.on_resized(1920, 1080)
    estado.on_moved(0, 0)

    assert estado.snapshot() == original

    estado.on_restored()
    estado.on_resized(1300, 900)
    estado.on_moved(15, 25)

    assert estado.snapshot() == {
        "ancho": 1300,
        "alto": 900,
        "x": 15,
        "y": 25,
        "maximizada": False,
    }


def test_maximized_y_restored_actualizan_flag():
    estado = window_state.EstadoVentana(ancho=1100, alto=760, maximizada=False)
    estado.on_maximized()
    assert estado.snapshot()["maximizada"] is True
    estado.on_restored()
    assert estado.snapshot()["maximizada"] is False


def test_guardar_crea_directorio_inexistente(_ruta_aislada):
    assert not _ruta_aislada.parent.exists()
    ok = window_state.guardar(
        {"ancho": 1000, "alto": 700, "x": None, "y": None, "maximizada": True}
    )
    assert ok is True
    assert _ruta_aislada.exists()
