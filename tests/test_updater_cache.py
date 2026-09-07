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

    api.start_update_check(delay=0, intervalo=0)
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

    api.start_update_check(delay=0, intervalo=0)
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

    api.start_update_check(delay=0, intervalo=0)
    hilo_creado["t"].join(timeout=5)

    assert api.get_ultimo_update() is None


# --- v3.4.76: el chequeo se REPITE, y su resultado (incluido el error) se guarda ---
# Antes era un disparo único al arrancar: quien dejaba la app abierta toda la
# jornada no se enteraba nunca de una versión publicada esa misma mañana. Y si
# el chequeo fallaba, el error se perdía sin dejar rastro en ningún sitio.


def _capturar_hilo(monkeypatch):
    """Devuelve un dict que recibirá el Thread real creado por `start_update_check`."""
    hilo_creado = {}
    hilo_original = threading.Thread

    def _thread_capturado(*args, **kwargs):
        t = hilo_original(*args, **kwargs)
        hilo_creado["t"] = t
        return t

    monkeypatch.setattr(aw.threading, "Thread", _thread_capturado)
    return hilo_creado


def test_estado_update_sin_chequeo_previo_dice_pendiente():
    # No mentir diciendo "estás al día" cuando aún no se ha preguntado.
    assert aw.Api().estado_update() == {"pendiente": True}


def test_estado_update_guarda_tambien_el_fallo(monkeypatch):
    def _revienta(self):
        raise RuntimeError("sin conexión")

    monkeypatch.setattr(aw.Api, "check_update", _revienta)
    api = aw.Api()
    hilo = _capturar_hilo(monkeypatch)

    api.start_update_check(delay=0, intervalo=0)
    hilo["t"].join(timeout=5)

    estado = api.estado_update()
    assert estado["ok"] is False
    assert "sin conexión" in estado["error"]
    assert "cuando" in estado          # marca de tiempo del intento
    assert api.get_ultimo_update() is None   # un fallo no es un aviso de versión


def test_el_chequeo_se_repite_hasta_encontrar_version_nueva(monkeypatch):
    """La app abierta debe enterarse sin reiniciar: 1ª vuelta al día, 2ª ya hay
    versión nueva y entonces sí avisa."""
    respuestas = [
        {"ok": True, "update_available": False, "current": "3.4.75", "latest": "3.4.75"},
        {"ok": True, "update_available": True, "current": "3.4.75", "latest": "3.4.76"},
    ]
    monkeypatch.setattr(aw.Api, "check_update", lambda self: respuestas.pop(0))

    api = aw.Api()
    hilo = _capturar_hilo(monkeypatch)
    # Intervalo mínimo: solo interesa que dé una segunda vuelta, no cuánto espera.
    # Al agotarse `respuestas`, el pop lanza IndexError y el hilo daemon muere:
    # es el freno natural del bucle en el test, no un fallo.
    api.start_update_check(delay=0, intervalo=0.01)
    hilo["t"].join(timeout=5)

    detail = api.get_ultimo_update()
    assert detail is not None, "la 2ª vuelta debía avisar de la 3.4.76"
    assert detail["data"]["latest"] == "3.4.76"


def test_no_repite_el_aviso_de_la_misma_version(monkeypatch):
    """Re-empujar el mismo aviso cada media hora sería acoso: una vez por versión."""
    res = {"ok": True, "update_available": True, "current": "3.4.75", "latest": "3.4.76"}
    vueltas = {"n": 0}

    def _check(self):
        vueltas["n"] += 1
        if vueltas["n"] > 3:
            raise RuntimeError("fin del test")   # corta el bucle del hilo daemon
        return res

    monkeypatch.setattr(aw.Api, "check_update", _check)

    api = aw.Api()
    empujes = []
    monkeypatch.setattr(aw.Api, "_push_update", lambda self, d: empujes.append(d))
    hilo = _capturar_hilo(monkeypatch)

    api.start_update_check(delay=0, intervalo=0.01)
    hilo["t"].join(timeout=5)

    assert vueltas["n"] > 1, "el chequeo tenía que repetirse"
    assert len(empujes) == 1, f"un solo aviso por versión, hubo {len(empujes)}"
