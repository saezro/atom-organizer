"""Tests del modo broker de `atom_core.google_auth` — la Raspberry Pi.

La Pi no tiene ni tendrá `client_id`/`client_secret` de Google: en vez de un
`refresh_token`, guarda un `device_token` opaco y le pide access_tokens a ATOM
Suite (`POST /api/organizer/token`). Lo que importa comprobar aquí no es que
"funcione": es que en modo broker el módulo **nunca** hable con Google — ni
para el canje, ni para el refresh, ni para revocar — porque no tiene con qué.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from atom_core import google_auth as ga


def _resp(body: dict) -> io.BytesIO:
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return _R(json.dumps(body).encode())


class FakeBroker:
    """`POST /api/organizer/token` de ATOM Suite, en memoria.

    Si algo de este módulo llamara a `oauth2.googleapis.com` en vez de aquí,
    esto no lo vería y el test que comprueba `peticiones` lo delataría.
    """

    def __init__(self, *, access_token="acceso-broker-1", expires_in=3600):
        self.access_token = access_token
        self.expires_in = expires_in
        self.peticiones: list = []
        self.error: int | None = None

    def urlopen(self, req, timeout=None):
        self.peticiones.append(req)
        if self.error is not None:
            raise urllib.error.HTTPError(
                req.full_url, self.error, "err", None,
                io.BytesIO(json.dumps({"error": "no autorizado"}).encode()))
        return _resp({"ok": True, "access_token": self.access_token,
                     "expires_in": self.expires_in})


@pytest.fixture
def broker(monkeypatch):
    fake = FakeBroker()
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    return fake


@pytest.fixture
def auth(tmp_path):
    return ga.GoogleAuth("", "", broker_only=True,
                         hosted_domain="aerotools.es",
                         store_path=tmp_path / "session.db")


# --------------------------------------------------------------------------
# Construcción
# --------------------------------------------------------------------------

def test_se_construye_sin_client_id_y_arranca_en_modo_broker(tmp_path):
    auth = ga.GoogleAuth("", "", broker_only=True, store_path=tmp_path / "s.db")
    assert auth.broker_only is True
    assert auth._modo == ga.MODO_BROKER


def test_sin_broker_only_el_client_id_vacio_sigue_siendo_error(tmp_path):
    """El escritorio no cambia: esto es programación defensiva de siempre."""
    with pytest.raises(ValueError):
        ga.GoogleAuth("", "secreto", store_path=tmp_path / "s.db")


# --------------------------------------------------------------------------
# login(): inalcanzable a propósito
# --------------------------------------------------------------------------

def test_login_en_broker_only_falla_rapido_sin_tocar_la_red(auth, broker):
    with pytest.raises(ga.AuthError, match="QR"):
        auth.login()
    assert broker.peticiones == []


# --------------------------------------------------------------------------
# access_token(): habla con la Suite, nunca con Google
# --------------------------------------------------------------------------

def test_access_token_pide_al_broker_con_bearer_y_nunca_a_google(auth, broker):
    auth.pair("device-token-abc", "piloto@aerotools.es")

    token = auth.access_token()

    assert token == "acceso-broker-1"
    assert len(broker.peticiones) == 1
    req = broker.peticiones[0]
    assert req.full_url == ga.BROKER_TOKEN_URI
    assert req.full_url.startswith(ga.SUITE_URL)
    assert "oauth2.googleapis.com" not in req.full_url
    assert req.get_header("Authorization") == "Bearer device-token-abc"


def test_un_401_del_broker_borra_la_sesion_y_levanta_autherror(auth, broker):
    auth.pair("device-token-abc", "piloto@aerotools.es")
    broker.error = 401

    with pytest.raises(ga.AuthError, match="autorizado"):
        auth.access_token()

    assert not auth.is_logged_in()
    # Y no queda nada que rescatar al reabrir la app.
    otra = ga.GoogleAuth("", "", broker_only=True, store_path=auth.store_path)
    assert not otra.is_logged_in()


# --------------------------------------------------------------------------
# logout(): no hay nada de Google que revocar
# --------------------------------------------------------------------------

def test_logout_en_broker_no_llama_a_revoke_uri(auth, broker):
    auth.pair("device-token-abc", "piloto@aerotools.es")

    auth.logout()

    assert not auth.is_logged_in()
    urls = [req.full_url for req in broker.peticiones]
    assert ga.REVOKE_URI not in urls


# --------------------------------------------------------------------------
# pair(): persiste modo='broker'
# --------------------------------------------------------------------------

def test_pair_persiste_modo_broker_y_session_store_lo_devuelve(auth):
    identidad = auth.pair("device-token-abc", "piloto@aerotools.es")

    assert identidad.email == "piloto@aerotools.es"
    assert auth.is_logged_in()

    sesion = auth._store.leer()
    assert sesion is not None
    assert sesion.modo == ga.MODO_BROKER
    assert sesion.refresh_token == "device-token-abc"

    # Y sobrevive a reabrir la app: la siguiente instancia debe arrancar ya en
    # modo broker sin que nadie vuelva a llamar a `pair()`.
    otra = ga.GoogleAuth("", "", broker_only=True, store_path=auth.store_path)
    assert otra.is_logged_in()
    assert otra._modo == ga.MODO_BROKER


def test_pair_propaga_picture_a_la_identidad_y_sobrevive_a_reabrir(auth):
    identidad = auth.pair("device-token-abc", "piloto@aerotools.es",
                          picture="https://lh3.googleusercontent.com/foo")

    assert identidad.picture == "https://lh3.googleusercontent.com/foo"
    assert auth.identity.picture == "https://lh3.googleusercontent.com/foo"

    # Se persiste: reabrir la app (otra instancia contra el mismo store) debe
    # devolver la misma foto sin que nadie vuelva a llamar a `pair()`.
    otra = ga.GoogleAuth("", "", broker_only=True, store_path=auth.store_path)
    assert otra.identity.picture == "https://lh3.googleusercontent.com/foo"


def test_pair_sin_picture_degrada_a_vacio_sin_romper(auth):
    """Una Suite vieja que no manda `picture` en el poll no debe reventar."""
    identidad = auth.pair("device-token-abc", "piloto@aerotools.es")

    assert identidad.picture == ""
    assert auth.is_logged_in()
