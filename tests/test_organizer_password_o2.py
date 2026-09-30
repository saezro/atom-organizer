"""Modo password en los consumidores: cabeceras a la Suite, prefix a GCS y
`cloud_login_password`. Todo con mocks: nunca red real."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

import app_webview as aw
from atom_core import cloud_upload as cu
from atom_core import google_auth as ga
from atom_core import inspecciones as ins
from atom_core import run_reporter as rr


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AuthPwd:
    es_password = True
    sesion_token = "sesion-1"

    def __init__(self):
        self.llamadas = []

    def access_token(self, *, force_refresh=False, prefix=None, inspeccion_id=None):
        self.llamadas.append((force_refresh, prefix, inspeccion_id))
        if not prefix:
            raise ga.AuthError("Falta el prefijo para pedir acceso a GCS")
        return f"gcs-{prefix}"

    def id_token(self):
        raise AssertionError("no debe pedirse id_token en modo password")


class AuthGoogle:
    es_password = False

    def __init__(self):
        self.llamadas = []

    def access_token(self, *, force_refresh=False):
        self.llamadas.append(force_refresh)
        return "gtok"

    def id_token(self):
        return "idtok"


# -- cabeceras a la Suite ---------------------------------------------------
def _capturar(monkeypatch, mod, payload):
    cap = {}

    def urlopen(req, timeout=None):
        cap["auth"] = req.get_header("Authorization")
        return _Resp(json.dumps(payload).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    return cap


def test_catalogo_modo_password_usa_sesion(monkeypatch):
    cap = _capturar(monkeypatch, ins, {"inspecciones": []})
    ins.descargar_catalogo_api(AuthPwd())
    assert cap["auth"] == "Bearer sesion-1"


def test_catalogo_modo_google_sigue_con_id_token(monkeypatch):
    cap = _capturar(monkeypatch, ins, {"inspecciones": []})
    ins.descargar_catalogo_api(AuthGoogle())
    assert cap["auth"] == "Bearer idtok"


def test_catalogo_password_sesion_caducada_error_explicito(monkeypatch):
    a = AuthPwd()
    a.sesion_token = None
    _capturar(monkeypatch, ins, {"inspecciones": []})
    with pytest.raises(ga.AuthError):
        ins.descargar_catalogo_api(a)


def test_reporter_modo_password_usa_sesion(monkeypatch):
    cap = _capturar(monkeypatch, rr, {"ok": True})
    r = rr.RunReporter(AuthPwd())
    assert r._peticion("POST", "/api/organizer/runs", {}) == {"ok": True}
    assert cap["auth"] == "Bearer sesion-1"


def test_reporter_modo_google_sigue_con_id_token(monkeypatch):
    cap = _capturar(monkeypatch, rr, {"ok": True})
    rr.RunReporter(AuthGoogle())._peticion("POST", "/api/organizer/runs", {})
    assert cap["auth"] == "Bearer idtok"


# -- prefix a GCS -----------------------------------------------------------
def test_provider_pasa_prefix_e_inspeccion_en_password():
    a = AuthPwd()
    p = cu.GcsOAuthProvider("b", a, prefix="E--P--2026--RGB/", inspeccion_id=5)
    assert p.headers() == {"Authorization": "Bearer gcs-E--P--2026--RGB/"}
    p.recover_auth()
    assert a.llamadas == [(False, "E--P--2026--RGB/", 5), (True, "E--P--2026--RGB/", 5)]


def test_provider_password_sin_prefix_falla_explicito():
    p = cu.GcsOAuthProvider("b", AuthPwd())
    with pytest.raises(ga.AuthError):
        p.headers()


def test_provider_google_no_recibe_prefix():
    a = AuthGoogle()
    p = cu.GcsOAuthProvider("b", a, prefix="X/", inspeccion_id=1)
    assert p.headers() == {"Authorization": "Bearer gtok"}
    p.recover_auth()
    assert a.llamadas == [False, True]


def test_listar_objetos_usa_token_prefix(monkeypatch):
    cap = _capturar(monkeypatch, cu, {"items": []})
    cu.listar_objetos_remotos("b", "E--P--2026--RGB/SUBIDAS/l1", AuthPwd(),
                              token_prefix="E--P--2026--RGB/", inspeccion_id=7)
    assert cap["auth"] == "Bearer gcs-E--P--2026--RGB/"


def test_listar_password_sin_inspeccion_id_falla_explicito(monkeypatch):
    _capturar(monkeypatch, cu, {"items": []})
    with pytest.raises(ga.AuthError, match="id de la inspección"):
        cu.listar_objetos_remotos("b", "E--P--2026--RGB/x", AuthPwd(),
                                  token_prefix="E--P--2026--RGB/")


def test_objetos_en_prefijo_y_descargar_propagan_inspeccion_id(tmp_path, monkeypatch):
    a = AuthPwd()
    _capturar(monkeypatch, cu, {"items": [{"name": "x"}]})
    assert cu.objetos_en_prefijo("b", "E--P--2026--RGB/", a, inspeccion_id=9) == 1
    with pytest.raises(ga.AuthError):
        cu.objetos_en_prefijo("b", "E--P--2026--RGB/", a)
    monkeypatch.setattr(cu.shutil, "copyfileobj", lambda r, f: None)
    cu.descargar_objeto("b", "E--P--2026--RGB/a", a, tmp_path / "a",
                        token_prefix="E--P--2026--RGB/", inspeccion_id=9)
    assert [c[2] for c in a.llamadas] == [9, 9]


def test_provider_listar_remotos_propaga_inspeccion_id(monkeypatch):
    a = AuthPwd()
    _capturar(monkeypatch, cu, {"items": []})
    p = cu.GcsOAuthProvider("b", a, prefix="E--P--2026--RGB/", inspeccion_id=5)
    assert p.listar_remotos("E--P--2026--RGB/l1") == {}
    assert a.llamadas == [(False, "E--P--2026--RGB/", 5)]


def _api_pwd(monkeypatch, auth):
    api = aw.Api.__new__(aw.Api)
    api._auth = auth
    api._ids_inspeccion = {}
    api._log_subida = lambda *a, **k: None
    return api


def test_verificar_lote_password_sin_id_no_traga_el_fallo(monkeypatch):
    api = _api_pwd(monkeypatch, None)
    with pytest.raises(ga.AuthError):
        api._verificar_lote_completo(type("P", (), {"items": []})(),
                                     "E--P--2026--RGB/SUBIDAS/l1", AuthPwd())


def test_verificar_lote_password_usa_id_de_catalogo(monkeypatch):
    api = _api_pwd(monkeypatch, None)
    api._ids_inspeccion["E--P--2026--RGB"] = 12
    _capturar(monkeypatch, cu, {"items": []})
    a = AuthPwd()
    fal, ver = api._verificar_lote_completo(type("P", (), {"items": []})(),
                                            "E--P--2026--RGB/SUBIDAS/l1", a)
    assert (fal, ver) == ([], True) and a.llamadas[0][2] == 12


def test_verificar_lote_google_sigue_informativo(monkeypatch):
    api = _api_pwd(monkeypatch, None)
    monkeypatch.setattr(cu, "listar_objetos_remotos",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("red")))
    assert api._verificar_lote_completo(type("P", (), {"items": []})(),
                                        "X/l", AuthGoogle()) == ([], False)


def test_inventario_password_sin_id_empuja_error(monkeypatch):
    import threading
    api = _api_pwd(monkeypatch, AuthPwd())
    api._inv_lock = threading.Lock()
    api._inv, api._inv_hilos = {}, set()
    auth = AuthPwd()
    auth.is_logged_in = lambda: True
    api._auth = auth
    empujes = []
    api._push_cloud = empujes.append
    hilos = []
    monkeypatch.setattr(aw.threading, "Thread", lambda target, daemon: type(
        "T", (), {"start": lambda self: hilos.append(target())})())
    api._inventario_precalentar("E--P--2026--RGB")
    assert empujes[0]["ok"] is False and "inspección" in empujes[0]["error"]


def test_descargar_password_sin_token_prefix_falla(tmp_path, monkeypatch):
    _capturar(monkeypatch, cu, {})
    with pytest.raises(ga.AuthError):
        cu.descargar_objeto("b", "P/ESTADILLOS/a.csv", AuthPwd(), tmp_path / "a.csv")


def test_prefijo_token_primer_segmento():
    assert aw.Api._prefijo_token("E--P--2026--RGB/SUBIDAS/l1") == "E--P--2026--RGB/"
    assert aw.Api._prefijo_token("E--P--2026--RGB") == "E--P--2026--RGB/"


# -- cloud_login_password ---------------------------------------------------
class _AuthLogin:
    broker_only = False

    def __init__(self, error=None):
        self.error = error

    def login_password(self, usuario, password):
        if self.error:
            raise self.error
        return ga.Identity(email="ana@ejemplo.com", domain="ejemplo.com",
                           nombre="Ana")


def _api(monkeypatch, auth):
    api = aw.Api()
    monkeypatch.setattr(api, "_get_auth", lambda **k: auth)
    monkeypatch.setattr(api, "_push_cloud", lambda d: None)
    return api


def test_cloud_login_password_ok(monkeypatch):
    r = _api(monkeypatch, _AuthLogin()).cloud_login_password("ana", "x")
    assert r == {"ok": True, "email": "ana@ejemplo.com", "nombre": "Ana",
                 "domain": "ejemplo.com"}


@pytest.mark.parametrize("msg", [
    "Usuario o contraseña incorrectos",
    "Tu cuenta no tiene acceso al Organizer",
    "Demasiados intentos, espera unos minutos",
])
def test_cloud_login_password_errores_legibles(monkeypatch, msg):
    r = _api(monkeypatch, _AuthLogin(ga.AuthError(msg))).cloud_login_password("a", "b")
    assert r == {"ok": False, "error": msg}


@pytest.mark.parametrize("code,body,msg", [
    (401, {"error": "credenciales-invalidas"}, "Usuario o contraseña incorrectos"),
    (403, {"error": "sin_modulo_organizer"}, "no tiene acceso al Organizer"),
    (429, {}, "Demasiados intentos"),
])
def test_cloud_login_password_mapea_http_real(monkeypatch, tmp_path, code, body, msg):
    """De punta a punta con GoogleAuth real y urlopen mockeado."""
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "e", None,
                                     io.BytesIO(json.dumps(body).encode()))

    monkeypatch.setattr(ga.urllib.request, "urlopen", urlopen)
    auth = ga.GoogleAuth("cid", "sec", store_path=tmp_path / "s.db")
    r = _api(monkeypatch, auth).cloud_login_password("ana", "pwd")
    assert r["ok"] is False and msg in r["error"]
    assert "pwd" not in r["error"]


def test_cloud_login_password_sin_cliente(monkeypatch):
    r = _api(monkeypatch, None).cloud_login_password("a", "b")
    assert r["ok"] is False and r["error"]


def test_cloud_login_password_en_whitelist():
    from atom_core import webserver
    assert "cloud_login_password" in webserver.METODOS_EXPUESTOS


# -- cliente OAuth ausente --------------------------------------------------
def _api_sin_cliente(monkeypatch, tmp_path, broker=False):
    from atom_core import cloud_config
    monkeypatch.setattr(cloud_config, "load_client", lambda root: None)
    monkeypatch.setattr(ga, "user_data_dir", lambda: tmp_path)
    api = aw.Api.__new__(aw.Api)
    api._auth = None
    api._broker = broker
    return api


def test_get_auth_sin_cliente_google_sigue_none(monkeypatch, tmp_path):
    api = _api_sin_cliente(monkeypatch, tmp_path)
    assert api._get_auth() is None
    r = api.cloud_login()
    assert r["started"] is False and r["reason"]


def test_login_password_sin_cliente_permitido(monkeypatch, tmp_path):
    api = _api_sin_cliente(monkeypatch, tmp_path)
    api._push_cloud = lambda d: None
    monkeypatch.setattr(ga.GoogleAuth, "login_password",
                        lambda self, u, p: (setattr(self, "_modo", ga.MODO_PASSWORD),
                                            ga.Identity(email="a@x.com", domain="x.com", nombre="A"))[1])
    r = api.cloud_login_password("a", "b")
    assert r["ok"] is True and r["email"] == "a@x.com"
    # con sesión password activa, el resto de la app ve el auth
    assert api._get_auth() is not None and api._get_auth().es_password


def test_login_password_broker_only_sigue_rechazado(monkeypatch, tmp_path):
    api = _api_sin_cliente(monkeypatch, tmp_path, broker=True)
    r = api.cloud_login_password("a", "b")
    assert r["ok"] is False and "QR" in r["error"]


def test_google_login_sin_cliente_oauth_falla(tmp_path):
    a = ga.GoogleAuth("", "", sin_cliente=True, store_path=tmp_path / "s.db")
    with pytest.raises(ga.AuthError):
        a.login()
