"""Refresco de token deduplicado (`GcsOAuthProvider.recover_auth`). El circuit breaker
de 403 y los reintentos viven ahora en Rust (`subir_lote`). Todo con mocks: nunca red real."""
from __future__ import annotations

import threading

import pytest

from atom_core import cloud_upload as cu
from atom_core.google_auth import RateLimitAuthError


class _Auth:
    es_password = False
    es_google = False

    def __init__(self, fallo=None):
        self.llamadas = 0
        self.fallo = fallo
        self._lock = threading.Lock()

    def access_token(self, **kw):
        with self._lock:
            self.llamadas += 1
        if self.fallo and kw.get("force_refresh"):
            raise self.fallo
        return "tok"


def test_refresco_deduplicado_entre_hilos():
    auth = _Auth()
    prov = cu.GcsOAuthProvider("b", auth)
    hilos = [threading.Thread(target=prov.recover_auth) for _ in range(16)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert auth.llamadas == 1


def test_refresco_tras_429_espera_y_refresca(monkeypatch):
    auth = _Auth(fallo=RateLimitAuthError("429", retry_after=5))
    prov = cu.GcsOAuthProvider("b", auth)
    with pytest.raises(RateLimitAuthError):
        prov.recover_auth()
    assert prov._sin_pedir_hasta - cu.time.monotonic() > 50  # >= 60 s
    # En cooldown el hilo espera y refresca de verdad (no devuelve True con
    # el token caducado).
    esperas = []
    monkeypatch.setattr(cu.time, "sleep", lambda s: esperas.append(s))
    auth.fallo = None
    assert prov.recover_auth() is True
    assert esperas and esperas[0] > 50
    assert auth.llamadas == 2


def test_reutiliza_solo_si_el_token_es_mas_nuevo():
    auth = _Auth()
    prov = cu.GcsOAuthProvider("b", auth)
    prov.headers()                 # este hilo usa el token de la generación 0
    otro = threading.Thread(target=prov.recover_auth)
    otro.start()
    otro.join()                    # otro hilo refresca: generación 1
    n = auth.llamadas
    assert prov.recover_auth() is True   # token más nuevo que el suyo: reutiliza
    assert auth.llamadas == n
    prov.headers()                 # ahora usa la generación 1 y da 403
    n = auth.llamadas
    assert prov.recover_auth() is True
    assert auth.llamadas == n + 1  # no hay más nuevo: refresca de verdad


def test_refresco_fallido_no_se_repite_en_serie():
    auth = _Auth(fallo=RuntimeError("boom"))
    prov = cu.GcsOAuthProvider("b", auth)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            prov.recover_auth()
    assert auth.llamadas == 1
