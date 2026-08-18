"""
Emulación box64 del SDK térmico DJI en arquitecturas no-x86 (Raspberry Pi).

Contexto: el DJI Thermal SDK solo trae binarios x86-64. En aarch64 el pipeline
lanza `dji_irp_linux.py` bajo box64 con un Python x86-64 embebido, en vez del
intérprete actual. Estos tests fijan `external_tools.dji_linux_launcher` (el
lanzador+entorno) y `pipeline._dji_measure_to_raw_linux` (quien lo usa), sin
depender NUNCA de la arquitectura real de la máquina donde corran ni de tener
box64 instalado.
"""
import os
import shutil
import struct
import subprocess
import sys

import pytest

import external_tools


def _sin_box64_ni_runtime(monkeypatch, tmp_path):
    """Aísla ambos tests de lo que de verdad haya instalado en el host."""
    monkeypatch.setattr(external_tools, "x86_runtime_dir",
                         lambda: str(tmp_path / "x86-runtime"))
    monkeypatch.setattr(external_tools, "x86_python_path",
                         lambda: str(tmp_path / "x86-runtime" / "bin" / "python3"))
    monkeypatch.setattr(external_tools, "x86_support_libs_dir",
                         lambda: str(tmp_path / "x86-runtime" / "lib-x86"))


# --- dji_linux_launcher: x86-64 nativo, sin emulación -----------------------

def test_dji_linux_launcher_en_x86_64_no_emula(monkeypatch):
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "x86_64")
    lanzador, env = external_tools.dji_linux_launcher("/algun/lib/dir")
    assert lanzador == [sys.executable]
    assert env == {}


def test_dji_linux_launcher_en_amd64_no_emula(monkeypatch):
    """"amd64" es sinónimo de "x86_64" (Windows reporta la arquitectura así)."""
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "amd64")
    lanzador, env = external_tools.dji_linux_launcher("/algun/lib/dir")
    assert lanzador == [sys.executable]
    assert env == {}


# --- dji_linux_launcher: aarch64 con box64 y runtime disponibles -----------

def test_dji_linux_launcher_en_aarch64_con_box64_devuelve_lanzador_y_env(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "aarch64")
    _sin_box64_ni_runtime(monkeypatch, tmp_path)

    box64_path = tmp_path / "usr" / "bin" / "box64"
    box64_path.parent.mkdir(parents=True)
    box64_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(external_tools.shutil, "which",
                         lambda name: str(box64_path) if name == "box64" else None)

    py_x86 = tmp_path / "x86-runtime" / "bin" / "python3"
    py_x86.parent.mkdir(parents=True)
    py_x86.write_text("#!/bin/sh\n")

    lib_dir = str(tmp_path / "DJI_SDK")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    lanzador, env = external_tools.dji_linux_launcher(lib_dir)

    assert lanzador[0] == str(box64_path)
    assert lanzador[1] == external_tools.x86_python_path()
    assert env["BOX64_EMULATED_LIBS"] == "libgomp.so.1"
    ld_path = env["LD_LIBRARY_PATH"].split(os.pathsep)
    assert external_tools.x86_support_libs_dir() in ld_path
    assert lib_dir in ld_path


def test_dji_linux_launcher_conserva_ld_library_path_preexistente(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "aarch64")
    _sin_box64_ni_runtime(monkeypatch, tmp_path)

    box64_path = tmp_path / "usr" / "bin" / "box64"
    box64_path.parent.mkdir(parents=True)
    box64_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(external_tools.shutil, "which",
                         lambda name: str(box64_path) if name == "box64" else None)

    py_x86 = tmp_path / "x86-runtime" / "bin" / "python3"
    py_x86.parent.mkdir(parents=True)
    py_x86.write_text("#!/bin/sh\n")

    monkeypatch.setenv("LD_LIBRARY_PATH", "/ya/estaba/aqui")

    lib_dir = str(tmp_path / "DJI_SDK")
    _lanzador, env = external_tools.dji_linux_launcher(lib_dir)

    ld_path = env["LD_LIBRARY_PATH"].split(os.pathsep)
    assert "/ya/estaba/aqui" in ld_path, (
        "un LD_LIBRARY_PATH preexistente debe concatenarse, no pisarse")
    assert external_tools.x86_support_libs_dir() in ld_path
    assert lib_dir in ld_path


# --- dji_linux_launcher: aarch64 sin box64 y/o sin runtime -----------------

def test_dji_linux_launcher_en_aarch64_sin_box64_lanza_feature_unavailable(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "aarch64")
    _sin_box64_ni_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(external_tools.shutil, "which", lambda name: None)

    with pytest.raises(external_tools.FeatureUnavailableError) as excinfo:
        external_tools.dji_linux_launcher(str(tmp_path / "DJI_SDK"))

    assert "box64" in str(excinfo.value)


def test_dji_linux_launcher_en_aarch64_sin_runtime_instalado_lanza_feature_unavailable(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(external_tools.platform, "machine", lambda: "aarch64")
    _sin_box64_ni_runtime(monkeypatch, tmp_path)

    box64_path = tmp_path / "usr" / "bin" / "box64"
    box64_path.parent.mkdir(parents=True)
    box64_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(external_tools.shutil, "which",
                         lambda name: str(box64_path) if name == "box64" else None)
    # El intérprete x86-64 embebido NO existe en tmp_path -> falta el runtime.

    with pytest.raises(external_tools.FeatureUnavailableError) as excinfo:
        external_tools.dji_linux_launcher(str(tmp_path / "DJI_SDK"))

    assert external_tools.x86_runtime_dir() in str(excinfo.value)


# --- pipeline._dji_measure_to_raw_linux: usa el lanzador ------------------

def _make_raw(path, n_floats=10):
    with open(path, "wb") as f:
        f.write(struct.pack(f"{n_floats}f", *[float(i) for i in range(n_floats)]))


def test_dji_measure_to_raw_linux_usa_el_lanzador_y_pasa_env(
    tmp_path, logger, monkeypatch
):
    import pipeline

    lanzador_falso = ["/ruta/a/box64", "/ruta/a/python3-x86"]
    env_falso = {"BOX64_EMULATED_LIBS": "libgomp.so.1", "LD_LIBRARY_PATH": "/algo"}
    monkeypatch.setattr(
        pipeline.external_tools, "dji_linux_launcher",
        lambda lib_dir: (lanzador_falso, env_falso))

    image_path = str(tmp_path / "DJI_0001_T.JPG")
    raw_path = str(tmp_path / "DJI_0001_T.JPG.raw")
    lib_dir = str(tmp_path / "DJI_SDK")

    capturado = {}

    def fake_run(cmd, *args, **kwargs):
        capturado["cmd"] = cmd
        capturado["kwargs"] = kwargs
        _make_raw(raw_path)  # la escritura atómica ya habría terminado en real
        return subprocess.CompletedProcess(cmd, -11)  # rc no fiable (segfault teardown)

    monkeypatch.setattr(subprocess, "run", fake_run)

    obj = pipeline.SplitImages(logger)
    obj._dji_measure_to_raw_linux(image_path, raw_path, 70.0, 0.95, lib_dir)

    cmd = capturado["cmd"]
    assert cmd[:len(lanzador_falso)] == lanzador_falso, (
        "el comando debe empezar por el lanzador devuelto por dji_linux_launcher")

    env_usado = capturado["kwargs"].get("env")
    assert env_usado is not None, "con env_extra no vacío debe pasarse env= no-None"
    assert env_usado["BOX64_EMULATED_LIBS"] == "libgomp.so.1"
    assert env_usado["LD_LIBRARY_PATH"] == "/algo"
