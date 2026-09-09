"""Tests de atom_core.sin_consola: parche de CREATE_NO_WINDOW en subprocess.Popen."""
from __future__ import annotations

import subprocess

import pytest

from atom_core import sin_consola


@pytest.fixture(autouse=True)
def _reset_parche(monkeypatch):
    """Cada test parte de estado limpio: sin parche aplicado y Popen original."""
    original_init = subprocess.Popen.__init__
    monkeypatch.setattr(sin_consola, "_parchado", False)
    yield
    subprocess.Popen.__init__ = original_init
    sin_consola._parchado = False


def test_no_windows_no_hace_nada(monkeypatch):
    monkeypatch.setattr(sin_consola.os, "name", "posix")
    original_init = subprocess.Popen.__init__

    sin_consola.aplicar()

    assert subprocess.Popen.__init__ is original_init
    assert sin_consola._parchado is False


def test_windows_anade_create_no_window(monkeypatch):
    monkeypatch.setattr(sin_consola.os, "name", "nt")
    capturado = {}

    def _init_falso(self, *args, **kwargs):
        capturado.update(kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", _init_falso)

    sin_consola.aplicar()
    subprocess.Popen(["fake.exe"])

    assert capturado.get("creationflags") == sin_consola._CREATE_NO_WINDOW


def test_windows_combina_con_creationflags_existente(monkeypatch):
    monkeypatch.setattr(sin_consola.os, "name", "nt")
    capturado = {}

    def _init_falso(self, *args, **kwargs):
        capturado.update(kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", _init_falso)

    sin_consola.aplicar()
    detached = 0x00000008
    subprocess.Popen(["fake.exe"], creationflags=detached | sin_consola._CREATE_NO_WINDOW)

    assert capturado.get("creationflags") == (detached | sin_consola._CREATE_NO_WINDOW)


def test_windows_no_pisa_create_new_console(monkeypatch):
    monkeypatch.setattr(sin_consola.os, "name", "nt")
    capturado = {}

    def _init_falso(self, *args, **kwargs):
        capturado.update(kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", _init_falso)

    sin_consola.aplicar()
    subprocess.Popen(["fake.exe"], creationflags=sin_consola._CREATE_NEW_CONSOLE)

    assert capturado.get("creationflags") == sin_consola._CREATE_NEW_CONSOLE


def test_idempotente(monkeypatch):
    monkeypatch.setattr(sin_consola.os, "name", "nt")
    capturado = {}

    def _init_falso(self, *args, **kwargs):
        capturado.update(kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", _init_falso)

    sin_consola.aplicar()
    sin_consola.aplicar()  # segunda llamada no debe encadenar el parche

    subprocess.Popen(["fake.exe"])

    assert capturado.get("creationflags") == sin_consola._CREATE_NO_WINDOW
