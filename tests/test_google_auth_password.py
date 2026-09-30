"""Tests del modo password de `atom_core.google_auth`.

Usuario/contraseña contra la Suite (`/api/auth/login`), `sesion` opaca que
caduca a diario, y tokens de GCS por prefijo (`/api/organizer/gcs-token`).
"""
from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from atom_core import google_auth as ga

PWD = "clave-super-secreta-42"


def _resp(body: dict) -> io.BytesIO:
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    return _R(json.dumps(body).encode())


class FakeSuite:
    """Login, gcs-token y logout de ATOM Suite, en memoria."""

    def __init__(self):
        self.peticiones: list = []
        self.login_error: tuple[int, dict] | None = None
        self.gcs_error: tuple[int, dict] | None = None
        self.expires_at = time.time() + 3600
        self.n_gcs = 0

    def urlopen(self, req, timeout=None):
        self.peticiones.append(req)
        url = req.full_url
        if url == ga.LOGIN_URI:
            if self.login_error:
                self._raise(req, *self.login_error)
            return _resp({"sesion": "sesion-opaca-1", "expires_at": self.expires_at,
                          "usuario": {"id": 7, "nombre": "Ana Piloto",
                                      "email": "ana@ejemplo.com"},
                          "esAtom": False})
        if url == ga.GCS_TOKEN_URI:
            if self.gcs_error:
                self._raise(req, *self.gcs_error)
            self.n_gcs += 1
            return _resp({"ok": True, "access_token": f"gcs-{self.n_gcs}",
                          "expires_in": 3600, "prefix": "x"})
        if url == ga.LOGOUT_URI:
            return _resp({"ok": True})
        raise AssertionError(f"URL inesperada: {url}")

    @staticmethod
    def _raise(req, code, body):
        raise urllib.error.HTTPError(req.full_url, code, "err", None,
                                     io.BytesIO(json.dumps(body).encode()))

    def de(self, url):
        return [r for r in self.peticiones if r.full_url == url]


@pytest.fixture
def suite(monkeypatch):
    fake = FakeSuite()
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    return fake


@pytest.fixture
def auth(tmp_path):
    return ga.GoogleAuth("cid", "sec", store_path=tmp_path / "session.db")


def test_login_ok_y_persiste_al_reabrir(auth, suite):
    ident = auth.login_password("ana", PWD)
    assert ident.email == "ana@ejemplo.com" and ident.nombre == "Ana Piloto"
    assert auth._modo == ga.MODO_PASSWORD
    assert auth.sesion_token == "sesion-opaca-1"
    assert auth.device_token is None

    otra = ga.GoogleAuth("cid", "sec", store_path=auth.store_path)
    assert otra.is_logged_in() and otra._modo == ga.MODO_PASSWORD
    assert otra.sesion_token == "sesion-opaca-1"
    assert otra.identity.email == "ana@ejemplo.com"


def test_login_usuario_vs_email(auth, suite):
    auth.login_password("ana", PWD)
    auth.login_password("ana@ejemplo.com", PWD)
    b1, b2 = (json.loads(r.data) for r in suite.de(ga.LOGIN_URI))
    assert b1 == {"usuario": "ana", "password": PWD, "app": "organizer"}
    assert b2 == {"email": "ana@ejemplo.com", "password": PWD, "app": "organizer"}


@pytest.mark.parametrize("code,body,texto", [
    (401, {"error": "credenciales-invalidas"}, "Usuario o contraseña incorrectos"),
    (403, {"error": "sin_modulo_organizer"}, "no tiene acceso al Organizer"),
    (429, {"error": "rate"}, "Demasiados intentos"),
])
def test_errores_de_login(auth, suite, code, body, texto):
    suite.login_error = (code, body)
    with pytest.raises(ga.AuthError, match=texto) as ei:
        auth.login_password("ana", PWD)
    assert PWD not in str(ei.value)
    assert not auth.is_logged_in()


def test_login_sin_red(auth, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("sin red")
    monkeypatch.setattr(ga.urllib.request, "urlopen", boom)
    with pytest.raises(ga.AuthError, match="Sin conexión con la Suite"):
        auth.login_password("ana", PWD)


def test_password_solo_en_body_del_login_y_no_en_disco(auth, suite):
    auth.login_password("ana", PWD)
    auth.access_token(prefix="a/b/")
    auth.logout()
    for req in suite.peticiones:
        resto = (req.full_url, str(req.headers), req.data if req.full_url != ga.LOGIN_URI else b"")
        assert PWD not in repr(resto)
    assert PWD.encode() in suite.de(ga.LOGIN_URI)[0].data
    for f in auth.store_path.parent.iterdir():
        if f.is_file():
            assert PWD.encode() not in f.read_bytes()
    auth.login_password("ana", PWD)
    assert PWD.encode() not in auth.store_path.read_bytes()


def test_access_token_pide_gcs_con_bearer_y_body(auth, suite):
    auth.login_password("ana", PWD)
    tok = auth.access_token(prefix="planta/vuelo1/", inspeccion_id=12)
    assert tok == "gcs-1"
    req = suite.de(ga.GCS_TOKEN_URI)[0]
    assert req.get_header("Authorization") == "Bearer sesion-opaca-1"
    assert json.loads(req.data) == {"inspeccion_id": 12, "prefix": "planta/vuelo1/"}
    auth.access_token(prefix="otro/")
    assert json.loads(suite.de(ga.GCS_TOKEN_URI)[1].data)["inspeccion_id"] is None


def test_cache_por_prefix(auth, suite):
    auth.login_password("ana", PWD)
    assert auth.access_token(prefix="a/") == "gcs-1"
    assert auth.access_token(prefix="a/") == "gcs-1"
    assert auth.access_token(prefix="b/") == "gcs-2"
    assert len(suite.de(ga.GCS_TOKEN_URI)) == 2
    assert auth.access_token(prefix="a/", force_refresh=True) == "gcs-3"


def test_falta_prefijo(auth, suite):
    auth.login_password("ana", PWD)
    with pytest.raises(ga.AuthError, match="Falta el prefijo"):
        auth.access_token()


def test_gcs_401_olvida_la_sesion(auth, suite):
    auth.login_password("ana", PWD)
    suite.gcs_error = (401, {"error": "x"})
    with pytest.raises(ga.AuthError, match="Sesión caducada"):
        auth.access_token(prefix="a/")
    assert not auth.is_logged_in()
    assert not ga.GoogleAuth("cid", "sec", store_path=auth.store_path).is_logged_in()


def test_gcs_403_no_olvida_la_sesion(auth, suite):
    auth.login_password("ana", PWD)
    suite.gcs_error = (403, {"error": "prefijo-no-permitido"})
    with pytest.raises(ga.AuthError, match="prefijo-no-permitido"):
        auth.access_token(prefix="a/")
    assert auth.is_logged_in() and auth.sesion_token


def test_sesion_caducada_al_reabrir(auth, suite):
    suite.expires_at = time.time() + 3600
    auth.login_password("ana", PWD)
    auth._store.meta_set(ga.META_SESION_EXPIRA, repr(time.time() - 1))
    otra = ga.GoogleAuth("cid", "sec", store_path=auth.store_path)
    assert not otra.is_logged_in() and otra.sesion_token is None
    assert otra._modo == ga.MODO_GOOGLE


def test_sesion_token_none_tras_expirar(auth, suite):
    auth.login_password("ana", PWD)
    auth._sesion_expira = time.time() - 1
    assert auth.sesion_token is None
    with pytest.raises(ga.AuthError, match="Sesión caducada"):
        auth.access_token(prefix="a/")


def test_perfil_password_sin_credencial(auth, suite):
    auth.login_password("ana", PWD)
    perfiles = auth._store.listar_perfiles()
    assert [p.email for p in perfiles] == ["ana@ejemplo.com"]
    assert perfiles[0].modo == ga.MODO_PASSWORD
    assert auth._store.activar_perfil("ana@ejemplo.com") is False


def test_id_token_lanza_error(auth, suite):
    auth.login_password("ana", PWD)
    with pytest.raises(ga.AuthError):
        auth.id_token()


def test_logout_password_llama_a_suite_y_olvida(auth, suite):
    auth.login_password("ana", PWD)
    auth.logout()
    req = suite.de(ga.LOGOUT_URI)[0]
    assert req.get_header("Authorization") == "Bearer sesion-opaca-1"
    assert not auth.is_logged_in()
    assert ga.REVOKE_URI not in [r.full_url for r in suite.peticiones]


def test_logout_password_sin_red_no_rompe(auth, suite, monkeypatch):
    auth.login_password("ana", PWD)

    def boom(req, timeout=None):
        raise urllib.error.URLError("sin red")
    monkeypatch.setattr(ga.urllib.request, "urlopen", boom)
    auth.logout()
    assert not auth.is_logged_in()


def test_olvidar_no_fuerza_password(auth, suite):
    auth.login_password("ana", PWD)
    auth._olvidar_local()
    assert auth._modo == ga.MODO_GOOGLE
    b = ga.GoogleAuth("", "", broker_only=True, store_path=auth.store_path.parent / "b.db")
    b.login_password("ana", PWD)
    b._olvidar_local()
    assert b._modo == ga.MODO_BROKER


def test_google_y_broker_sin_cambios(auth, tmp_path, monkeypatch):
    seen = []

    def fake(req, timeout=None):
        seen.append(req)
        if req.full_url == ga.BROKER_TOKEN_URI:
            return _resp({"ok": True, "access_token": "br", "expires_in": 3600})
        return _resp({"access_token": "goog", "expires_in": 3600})
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake)
    b = ga.GoogleAuth("", "", broker_only=True, store_path=tmp_path / "b.db")
    b.pair("dev-tok", "pi@ejemplo.com")
    assert b.access_token(prefix="ignorado/", inspeccion_id=3) == "br"
    assert b.device_token == "dev-tok" and b.sesion_token is None
    auth._refresh_token = "rt"
    assert auth.access_token(prefix="ignorado/") == "goog"
    assert seen[-1].full_url == ga.TOKEN_URI
