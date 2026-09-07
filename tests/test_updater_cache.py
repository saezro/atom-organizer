"""El chequeo automático de arranque (`start_update_check`, 3 s tras crear la
ventana) emite `atom:update` por el sink, pero `UpdateModal` no está montado
mientras el usuario no ha pasado el login: el evento se pierde y el aviso de
versión nueva nunca sale (ver `App.jsx`, que solo monta `<PantallaEntrada>`
mientras `!entrado`).

Fix: `Api` cachea el último aviso "hay versión nueva" en `_ultimo_update`, y
`get_ultimo_update()` lo expone para que el modal, al montarse tarde, lo
recupere por su cuenta.
"""
from __future__ import annotations

import threading

import app_webview as aw


def test_get_ultimo_update_sin_chequeo_previo_es_none():
    api = aw.Api()
    assert api.get_ultimo_update() is None


def test_start_update_check_cachea_el_detail_si_hay_version_nueva(monkeypatch):
    res = {
        "ok": True,
        "update_available": True,
        "current": "3.4.61",
        "latest": "3.4.62",
        "notes": "",
        "asset_url": "https://example/asset",
        "asset_size": 123,
        "can_install": True,
    }
    monkeypatch.setattr(aw.Api, "check_update", lambda self: res)

    api = aw.Api()
    # El worker de `start_update_check` es un hilo `daemon=True`: con delay=0
    # y sin nada más que hacer, `join()` sobre el propio Thread real basta
    # para esperar a que termine sin sondear con sleeps.
    hilo_creado = {}
    hilo_original = threading.Thread

    def _thread_capturado(*args, **kwargs):
        t = hilo_original(*args, **kwargs)
        hilo_creado["t"] = t
        return t

    monkeypatch.setattr(aw.threading, "Thread", _thread_capturado)

    api.start_update_check(delay=0)
    hilo_creado["t"].join(timeout=5)

    detail = api.get_ultimo_update()
    assert detail is not None
    assert detail["kind"] == "available"
    assert detail["data"] == res


def test_start_update_check_sin_version_nueva_no_cachea_nada(monkeypatch):
    res = {"ok": True, "update_available": False, "current": "3.4.61"}
    monkeypatch.setattr(aw.Api, "check_update", lambda self: res)

    api = aw.Api()
    hilo_creado = {}
    hilo_original = threading.Thread

    def _thread_capturado(*args, **kwargs):
        t = hilo_original(*args, **kwargs)
        hilo_creado["t"] = t
        return t

    monkeypatch.setattr(aw.threading, "Thread", _thread_capturado)

    api.start_update_check(delay=0)
    hilo_creado["t"].join(timeout=5)

    assert api.get_ultimo_update() is None


def test_start_update_check_error_de_red_no_rompe_ni_cachea(monkeypatch):
    def _revienta(self):
        raise RuntimeError("sin conexión")

    monkeypatch.setattr(aw.Api, "check_update", _revienta)

    api = aw.Api()
    hilo_creado = {}
    hilo_original = threading.Thread

    def _thread_capturado(*args, **kwargs):
        t = hilo_original(*args, **kwargs)
        hilo_creado["t"] = t
        return t

    monkeypatch.setattr(aw.threading, "Thread", _thread_capturado)

    api.start_update_check(delay=0)
    hilo_creado["t"].join(timeout=5)

    assert api.get_ultimo_update() is None
