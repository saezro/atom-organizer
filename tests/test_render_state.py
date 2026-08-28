"""Decide si la ventana arranca con GPU o software: JSON aparte de Config.ini."""
from __future__ import annotations

import json

import pytest

from atom_core import render_state


@pytest.fixture(autouse=True)
def _ruta_aislada(tmp_path, monkeypatch):
    """Redirige `ruta_estado()` a un fichero bajo `tmp_path`, sin tocar disco real."""
    destino = tmp_path / "config" / "render.json"
    monkeypatch.setattr(render_state, "ruta_estado", lambda: str(destino))
    return destino


# --- decidir() -------------------------------------------------------------


def test_decidir_auto_limpio_usa_gpu_y_marca_pendiente():
    estado = {"modo": "auto", "pendiente": False, "fallos": 0}
    usar_gpu, nuevo, motivo = render_state.decidir(estado)
    assert usar_gpu is True
    assert nuevo["pendiente"] is True
    assert nuevo["fallos"] == 0


def test_decidir_auto_con_pendiente_degrada_y_suma_fallo():
    estado = {"modo": "auto", "pendiente": True, "fallos": 0}
    usar_gpu, nuevo, motivo = render_state.decidir(estado)
    assert usar_gpu is False
    assert nuevo["pendiente"] is False
    assert nuevo["fallos"] == 1


def test_decidir_auto_con_fallos_previos_se_queda_en_software():
    estado = {"modo": "auto", "pendiente": False, "fallos": 1}
    usar_gpu, nuevo, motivo = render_state.decidir(estado)
    assert usar_gpu is False
    assert nuevo["pendiente"] is False
    assert nuevo["fallos"] == 1


def test_decidir_modo_gpu_fuerza_gpu_pese_a_pendiente_y_fallos():
    estado = {"modo": "gpu", "pendiente": True, "fallos": 3}
    usar_gpu, nuevo, motivo = render_state.decidir(estado)
    assert usar_gpu is True
    assert nuevo["pendiente"] is False
    assert nuevo["fallos"] == 0


def test_decidir_modo_software_siempre_false():
    estado = {"modo": "software", "pendiente": True, "fallos": 5}
    usar_gpu, nuevo, motivo = render_state.decidir(estado)
    assert usar_gpu is False
    assert nuevo["pendiente"] is False


# --- confirmar_render() / set_modo() ---------------------------------------


def test_confirmar_render_limpia_pendiente_y_fallos():
    estado = {"modo": "auto", "pendiente": True, "fallos": 2}
    nuevo = render_state.confirmar_render(estado)
    assert nuevo["pendiente"] is False
    assert nuevo["fallos"] == 0
    assert nuevo["modo"] == "auto"


def test_set_modo_cambia_modo_y_resetea_fallos_pendiente():
    estado = {"modo": "software", "pendiente": True, "fallos": 4}
    nuevo = render_state.set_modo(estado, "gpu")
    assert nuevo["modo"] == "gpu"
    assert nuevo["fallos"] == 0
    assert nuevo["pendiente"] is False


def test_set_modo_invalido_lanza_valueerror():
    estado = dict(render_state.ESTADO_INICIAL)
    with pytest.raises(ValueError):
        render_state.set_modo(estado, "turbo")


# --- leer() ------------------------------------------------------------


def test_leer_fichero_inexistente_devuelve_estado_inicial():
    assert render_state.leer() == render_state.ESTADO_INICIAL


def test_leer_json_corrupto_devuelve_estado_inicial(_ruta_aislada):
    _ruta_aislada.parent.mkdir(parents=True, exist_ok=True)
    _ruta_aislada.write_text("esto no es json {{{", encoding="utf-8")
    assert render_state.leer() == render_state.ESTADO_INICIAL


@pytest.mark.parametrize(
    "datos",
    [
        {"modo": "turbo", "pendiente": False, "fallos": 0},  # modo inválido
        {"modo": "auto", "pendiente": "no", "fallos": 0},  # pendiente no bool
        {"modo": "auto", "pendiente": False, "fallos": -1},  # fallos negativo
        {"modo": "auto", "pendiente": False, "fallos": "0"},  # fallos no int
        {"modo": "auto", "pendiente": False},  # falta 'fallos'
    ],
)
def test_leer_json_con_tipos_o_modo_invalido_devuelve_estado_inicial(_ruta_aislada, datos):
    _ruta_aislada.parent.mkdir(parents=True, exist_ok=True)
    _ruta_aislada.write_text(json.dumps(datos), encoding="utf-8")
    assert render_state.leer() == render_state.ESTADO_INICIAL


def test_leer_json_valido_lo_devuelve():
    estado = {"modo": "gpu", "pendiente": True, "fallos": 3}
    assert render_state.guardar(estado) is True
    assert render_state.leer() == estado


# --- guardar() ---------------------------------------------------------


def test_guardar_y_leer_round_trip():
    estado = {"modo": "software", "pendiente": False, "fallos": 1}
    assert render_state.guardar(estado) is True
    assert render_state.leer() == estado


def test_guardar_crea_directorio_inexistente(_ruta_aislada):
    assert not _ruta_aislada.parent.exists()
    ok = render_state.guardar({"modo": "auto", "pendiente": False, "fallos": 0})
    assert ok is True
    assert _ruta_aislada.exists()


def test_guardar_devuelve_false_si_ruta_inescribible(tmp_path, monkeypatch):
    bloqueador = tmp_path / "bloqueador"
    bloqueador.write_text("soy un fichero, no un directorio", encoding="utf-8")
    destino = bloqueador / "render.json"
    monkeypatch.setattr(render_state, "ruta_estado", lambda: str(destino))

    assert render_state.guardar({"modo": "auto", "pendiente": False, "fallos": 0}) is False


# --- ciclo completo de arranques ----------------------------------------


def test_ciclo_arranque_gpu_no_confirmado_degrada_a_software():
    # Arranque 1: estado inicial, decide GPU y persiste el marcador pendiente.
    estado = dict(render_state.ESTADO_INICIAL)
    usar_gpu, estado, motivo = render_state.decidir(estado)
    assert usar_gpu is True
    assert render_state.guardar(estado) is True

    # La ventana nunca llama a confirmar_render() (pantalla negra): no se toca el fichero.

    # Arranque 2: lee el pendiente sin confirmar, degrada a software y suma un fallo.
    estado = render_state.leer()
    usar_gpu, estado, motivo = render_state.decidir(estado)
    assert usar_gpu is False
    assert estado["fallos"] == 1
    assert render_state.guardar(estado) is True

    # Arranque 3: con fallos>=1 se queda en software de forma permanente.
    estado = render_state.leer()
    usar_gpu, estado, motivo = render_state.decidir(estado)
    assert usar_gpu is False
    assert estado["fallos"] == 1
