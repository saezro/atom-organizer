"""Matriz de plataformas del backend térmico DJI.

El Organizer debe servir en Windows, Linux x86-64, ARM (Raspberry Pi, vía box64) y
Cloud Run (que es Linux x86-64 dentro de `Dockerfile.job`). Estos tests fijan que las
cuatro rutas se deciden en UN SOLO sitio —`external_tools`— y que nadie vuelve a
duplicar el test de SO por su cuenta: si alguien reintroduce un `sys.platform` local en
`pipeline`, el test de coherencia falla.

La emulación box64 en detalle está cubierta en `tests/test_runtime_x86.py`; aquí solo se
comprueba que ARM cae en la rama Linux y no en una tercera.
"""

import sys

import pytest

import external_tools
import pipeline


@pytest.fixture
def como_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def como_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def test_windows_usa_el_exe_nativo(como_windows):
    assert external_tools._current_os() == "win"
    assert external_tools.dji_bin_name() == "dji_irp.exe"
    assert external_tools.resolve_tool("dji_irp").endswith("dji_irp.exe")
    assert pipeline._is_windows() is True


def test_linux_x86_usa_la_libreria_por_ctypes(como_linux, monkeypatch):
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "x86_64")
    assert external_tools.dji_bin_name() == "libdirp.so"
    assert external_tools.resolve_tool("dji_irp").endswith("libdirp.so")
    assert pipeline._is_windows() is False
    # Sin nada que emular: el lanzador es el intérprete actual y el entorno tal cual.
    assert external_tools.dji_linux_launcher("/lib/dji") == ([sys.executable], {})


def test_cloudrun_es_la_misma_ruta_que_linux_x86(como_linux, monkeypatch):
    # El job de Cloud Run corre la imagen de Dockerfile.job: Linux x86-64. No debe
    # existir una cuarta rama propia; si aparece, este test lo destapa.
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "x86_64")
    assert external_tools.dji_bin_name() == "libdirp.so"
    assert external_tools.dji_linux_launcher("/lib/dji") == ([sys.executable], {})


def test_arm_cae_en_la_rama_linux_no_en_una_tercera(como_linux, monkeypatch):
    # El SDK de DJI solo existe para x86-64: en ARM se emula, pero el binario que se
    # busca sigue siendo libdirp.so y el pipeline sigue tomando la rama Linux.
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "aarch64")
    assert external_tools.is_x86_64() is False
    assert external_tools.dji_bin_name() == "libdirp.so"
    assert pipeline._is_windows() is False


@pytest.mark.parametrize("plataforma, esperado", [("win32", True), ("linux", False), ("darwin", False)])
def test_pipeline_no_duplica_el_test_de_so(monkeypatch, plataforma, esperado):
    # `pipeline._is_windows` tenía su propio `sys.platform.startswith("win")`. Ahora
    # delega: mover la decisión en external_tools debe mover también el pipeline.
    monkeypatch.setattr(sys, "platform", plataforma)
    assert pipeline._is_windows() is esperado
    assert pipeline._is_windows() is (external_tools._current_os() == "win")
