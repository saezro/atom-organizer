"""Estadillos en modo password (token por `{planta_id, ambito}`), rechazo de
sobrescritura con token sin borrado y `acceso_modulos` del login. Todo con
mocks: nunca red real."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app_webview as aw
from atom_core import cloud_upload as cu
from atom_core import google_auth as ga
from tests.test_google_auth_password import PWD, FakeSuite, _resp  # noqa: F401
from tests.test_google_auth_password import auth, suite  # noqa: F401  (fixtures)


# -- google_auth: token por planta ------------------------------------------
def _suite_estadillos(suite, monkeypatch):
    orig = suite.urlopen

    def urlopen(req, timeout=None):
        if req.full_url == ga.GCS_TOKEN_URI and json.loads(req.data).get("ambito"):
            suite.peticiones.append(req)
            suite.n_gcs += 1
            return _resp({"ok": True, "access_token": f"est-{suite.n_gcs}",
                          "expires_in": 3600, "prefix": "PLANTA_X/ESTADILLOS/"})
        return orig(req, timeout)

    monkeypatch.setattr(ga.urllib.request, "urlopen", urlopen)


def test_token_estadillos_body_y_prefijo_del_servidor(auth, suite, monkeypatch):
    _suite_estadillos(suite, monkeypatch)
    auth.login_password("ana", PWD)
    assert auth.access_token(planta_id=42, inspeccion_id=9, ambito="estadillos") == "est-1"
    req = suite.de(ga.GCS_TOKEN_URI)[0]
    assert json.loads(req.data) == {"planta_id": 42, "inspeccion_id": 9,
                                    "ambito": "estadillos"}
    assert auth.prefijo_estadillos(42, 9) == "PLANTA_X/ESTADILLOS/"
    assert len(suite.de(ga.GCS_TOKEN_URI)) == 1  # cacheado


def test_token_estadillos_sin_planta_id_falla(auth, suite):
    auth.login_password("ana", PWD)
    with pytest.raises(ga.AuthError, match="id de la planta"):
        auth.access_token(ambito="estadillos")
    assert suite.de(ga.GCS_TOKEN_URI) == []


def test_token_estadillos_403_error_visible(auth, suite, monkeypatch):
    auth.login_password("ana", PWD)
    suite.gcs_error = (403, {"error": "sin_modulo_estadillos"})
    with pytest.raises(ga.AuthError, match="Acceso denegado"):
        auth.access_token(planta_id=1, inspeccion_id=2, ambito="estadillos")


def test_token_estadillos_respuesta_no_json_error_visible(auth, suite, monkeypatch):
    auth.login_password("ana", PWD)
    orig = suite.urlopen

    class _Html:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>502 Bad Gateway</html>"

    def urlopen(req, timeout=None):
        if req.full_url == ga.GCS_TOKEN_URI:
            return _Html()
        return orig(req, timeout)

    monkeypatch.setattr(ga.urllib.request, "urlopen", urlopen)
    with pytest.raises(ga.AuthError, match="respuesta no válida"):
        auth.access_token(planta_id=42, inspeccion_id=9, ambito="estadillos")


def test_token_estadillos_sin_inspeccion_id_falla(auth, suite):
    auth.login_password("ana", PWD)
    with pytest.raises(ga.AuthError, match="id de la inspección"):
        auth.access_token(planta_id=1, ambito="estadillos")
    assert suite.de(ga.GCS_TOKEN_URI) == []


def test_login_guarda_acceso_modulos(auth, suite, monkeypatch, tmp_path):
    orig = suite.urlopen

    def urlopen(req, timeout=None):
        r = orig(req, timeout)
        if req.full_url == ga.LOGIN_URI:
            d = json.loads(r.read())
            d["acceso_modulos"] = {"organizer": False, "estadillos": True}
            return _resp(d)
        return r

    monkeypatch.setattr(ga.urllib.request, "urlopen", urlopen)
    assert auth.acceso_modulos is None
    auth.login_password("ana", PWD)
    assert auth.acceso_modulos == {"organizer": False, "estadillos": True}
    otra = ga.GoogleAuth("cid", "sec", store_path=auth.store_path)
    assert otra.acceso_modulos == {"organizer": False, "estadillos": True}
    auth.logout()
    assert auth.acceso_modulos is None


# -- cloud_upload.token_gcs / provider --------------------------------------
class AuthPwd:
    es_password = True
    sesion_token = "s"

    def __init__(self):
        self.llamadas = []

    def access_token(self, *, force_refresh=False, prefix=None, inspeccion_id=None,
                     planta_id=None, ambito=None):
        self.llamadas.append((prefix, inspeccion_id, planta_id, ambito))
        return "tok"


def test_provider_estadillos_pasa_planta_inspeccion_y_ambito():
    a = AuthPwd()
    p = cu.GcsOAuthProvider("b", a, inspeccion_id=4, planta_id=9, ambito="estadillos")
    assert p.headers() == {"Authorization": "Bearer tok"}
    assert a.llamadas == [(None, 4, 9, "estadillos")]


# -- reconciliar: sobrescribir en modo password ------------------------------
def _plan(tmp_path, n=1):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 10)
    plan = cu.build_plan(tmp_path, "P/sub")
    return plan


def test_reconciliar_sobrescribir_falla_visible_sin_permiso(tmp_path):
    plan = _plan(tmp_path)
    remotos = {it.remote: cu.RemoteObject(it.remote, it.size + 1) for it in plan.items}
    with pytest.raises(cu.SobrescrituraNoPermitida, match="no permite borrar"):
        cu.reconciliar(plan, remotos, permitir_sobrescribir=False)


def test_reconciliar_sin_sobrescritura_no_falla_sin_permiso(tmp_path):
    plan = _plan(tmp_path)
    pend, hechos = cu.reconciliar(plan, {}, permitir_sobrescribir=False)
    assert len(pend) == len(plan.items) and not hechos


def test_reconciliar_google_sigue_sobrescribiendo(tmp_path):
    plan = _plan(tmp_path)
    remotos = {it.remote: cu.RemoteObject(it.remote, it.size + 1) for it in plan.items}
    pend, _ = cu.reconciliar(plan, remotos)
    assert all(it.sobrescribir for it in pend)


def test_upload_plan_password_aborta_antes_de_subir(tmp_path, monkeypatch):
    plan = _plan(tmp_path)

    class Prov(cu.UrlProvider):
        solo_crear = True

        def listar_remotos(self, prefix):
            return {it.remote: cu.RemoteObject(it.remote, it.size + 1)
                    for it in plan.items}

        def upload_url(self, *a, **k):  # pragma: no cover
            raise AssertionError("no debe subirse nada")

    with pytest.raises(cu.SobrescrituraNoPermitida):
        cu.upload_plan(plan, Prov(), manifest=cu.Manifest(tmp_path / "m.json"))


# -- app_webview: estadillos en password ------------------------------------
class _AuthApi:
    es_password = True

    def __init__(self):
        self.pedidos = []

    def is_logged_in(self):
        return True

    def prefijo_estadillos(self, planta_id, inspeccion_id=None):
        self.pedidos.append((planta_id, inspeccion_id))
        return "RAIZ_SERVIDOR/ESTADILLOS/"


def test_estadillos_acceso_password_usa_planta_id_y_prefijo_servidor():
    api = aw.Api()
    auth = _AuthApi()
    api._plantas_inspeccion = {"E--P--2026--RGB": 77}
    api._ids_inspeccion = {"E--P--2026--RGB": 901}
    raiz, kw = api._estadillos_acceso("E--P--2026--RGB", auth)
    assert raiz == "RAIZ_SERVIDOR/ESTADILLOS"
    assert kw == {"planta_id": 77, "inspeccion_id": 901, "ambito": "estadillos"}
    assert auth.pedidos == [(77, 901)]


def test_estadillos_acceso_password_sin_inspeccion_id_error_visible():
    api = aw.Api()
    api._plantas_inspeccion = {"E--P--2026--RGB": 77}
    auth = _AuthApi()
    with pytest.raises(ga.AuthError, match="id de la inspección"):
        api._estadillos_acceso("E--P--2026--RGB", auth)
    assert auth.pedidos == []


def test_estadillos_acceso_password_sin_planta_id_error_visible():
    api = aw.Api()
    with pytest.raises(ga.AuthError, match="id de la planta"):
        api._estadillos_acceso("E--P--2026--RGB", _AuthApi())


def test_estadillos_acceso_google_sin_cambios():
    api = aw.Api()

    class G:
        es_password = False

    raiz, kw = api._estadillos_acceso("MI_PLANTA", G())
    assert raiz.endswith("/ESTADILLOS") and kw == {"token_prefix": raiz + "/"}


def test_estadillo_existente_password_pasa_ambito(monkeypatch):
    api = aw.Api()
    auth = _AuthApi()
    api._plantas_inspeccion = {"K": 5}
    api._ids_inspeccion = {"K": 31}
    monkeypatch.setattr(api, "_get_auth", lambda: auth)
    vistos = {}

    def fake(bucket, prefix, a, **kw):
        vistos.update(prefix=prefix, **kw)
        return 1

    monkeypatch.setattr(cu, "objetos_en_prefijo", fake)
    assert api.estadillo_existente("K") == {"existe": True, "error": None}
    assert vistos == {"prefix": "RAIZ_SERVIDOR/ESTADILLOS/actual/",
                      "planta_id": 5, "inspeccion_id": 31, "ambito": "estadillos"}
