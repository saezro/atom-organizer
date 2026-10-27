"""Ámbito `resultado` del token de GCS en modo password. Todo con mocks: nunca red real."""
from __future__ import annotations

import json

import pytest

from atom_core import google_auth as ga
from tests.test_google_auth_password import PWD, FakeSuite, _resp  # noqa: F401
from tests.test_google_auth_password import auth, suite  # noqa: F401  (fixtures)

PREFIJO = "KL05/INSPECCIONES/TERMICA_MODULOS/2026/"


def _suite_resultado(suite, monkeypatch, expires_in=900):
    orig = suite.urlopen

    def urlopen(req, timeout=None):
        if req.full_url == ga.GCS_TOKEN_URI and json.loads(req.data).get("ambito") == "resultado":
            suite.peticiones.append(req)
            suite.n_gcs += 1
            return _resp({"ok": True, "access_token": f"res-{suite.n_gcs}", "expires_in": expires_in,
                          "prefix": PREFIJO, "bucket": "plantas_pv_nl"})
        return orig(req, timeout)

    monkeypatch.setattr(ga.urllib.request, "urlopen", urlopen)


def test_body_sin_planta_ni_prefix_y_prefijo_del_servidor(auth, suite, monkeypatch):
    _suite_resultado(suite, monkeypatch)
    auth.login_password("ana", PWD)
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-1"
    req = suite.de(ga.GCS_TOKEN_URI)[0]
    assert json.loads(req.data) == {"inspeccion_id": 9, "ambito": "resultado"}
    assert auth.prefijo_resultado(9) == PREFIJO
    assert len(suite.de(ga.GCS_TOKEN_URI)) == 1  # cacheado


def test_sin_inspeccion_falla_antes_de_llamar(auth, suite):
    auth.login_password("ana", PWD)
    with pytest.raises(ga.AuthError, match="id de la inspección"):
        auth.access_token(ambito="resultado")
    assert suite.de(ga.GCS_TOKEN_URI) == []


def test_cache_por_inspeccion(auth, suite, monkeypatch):
    _suite_resultado(suite, monkeypatch)
    auth.login_password("ana", PWD)
    auth.access_token(inspeccion_id=9, ambito="resultado")
    auth.access_token(inspeccion_id=10, ambito="resultado")
    assert len(suite.de(ga.GCS_TOKEN_URI)) == 2


def test_renueva_antes_de_caducar_con_margen_de_resultado(auth, suite, monkeypatch):
    """Token de 900 s: con el margen genérico (120 s) se reutilizaría hasta t=780;
    con el de resultado (300 s) hay que renovar a partir de t=600."""
    _suite_resultado(suite, monkeypatch, expires_in=900)
    reloj = {"t": 1_000_000.0}
    monkeypatch.setattr(ga.time, "time", lambda: reloj["t"])
    auth.login_password("ana", PWD)
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-1"
    reloj["t"] += 500
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-1"  # quedan 400 s
    reloj["t"] += 150  # t=650: quedan 250 s < 300 s
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-2"
    assert ga.RENOVAR_ANTES_RESULTADO_S == 300


def test_force_refresh_pide_token_nuevo(auth, suite, monkeypatch):
    _suite_resultado(suite, monkeypatch)
    auth.login_password("ana", PWD)
    auth.access_token(inspeccion_id=9, ambito="resultado")
    assert auth.access_token(inspeccion_id=9, ambito="resultado", force_refresh=True) == "res-2"


@pytest.mark.parametrize("codigo,cuerpo,texto", [
    (422, {"error": "inspeccion-sin-planta-o-anio"}, "no tiene planta o año"),
    (422, {"error": "inspeccion-sin-tipo"}, "no tiene tipo"),
    (422, {"error": "planta-nombre-invalido"}, "nombre de la planta"),
    (404, {"error": "inspeccion-no-encontrada"}, "no existe"),
    (403, {"error": "modulo-no-concedido"}, "Acceso denegado"),
    (400, {"error": "solo-sesion"}, "usuario y contraseña"),
])
def test_errores_de_la_suite_con_mensaje_legible(auth, suite, codigo, cuerpo, texto):
    auth.login_password("ana", PWD)
    suite.gcs_error = (codigo, cuerpo)
    with pytest.raises(ga.AuthError, match=texto):
        auth.access_token(inspeccion_id=9, ambito="resultado")


def test_ambito_desconocido_sigue_rechazado(auth, suite):
    auth.login_password("ana", PWD)
    with pytest.raises(ga.AuthError, match="Ámbito de GCS no soportado"):
        auth.access_token(inspeccion_id=9, ambito="otro")


# ---- login Google: Bearer id_token ----------------------------------------------
def _como_google(auth, monkeypatch):
    import time as _t
    auth._modo = ga.MODO_GOOGLE
    auth._refresh_token = "rt"
    auth._id_token = "IDTOK"
    auth._access_token = "at"
    auth._expires_at = _t.time() + 3600
    return auth


def test_google_pide_token_resultado_con_bearer_id_token(auth, suite, monkeypatch):
    _suite_resultado(suite, monkeypatch)
    _como_google(auth, monkeypatch)
    assert auth.es_google and not auth.es_password
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-1"
    req = suite.de(ga.GCS_TOKEN_URI)[0]
    assert req.get_header("Authorization") == "Bearer IDTOK"
    assert auth.prefijo_resultado(9) == PREFIJO


def test_google_401_no_borra_la_sesion(auth, suite, monkeypatch):
    import io
    import urllib.error

    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"error":"solo-sesion"}'))
    monkeypatch.setattr(ga.urllib.request, "urlopen", urlopen)
    _como_google(auth, monkeypatch)
    with pytest.raises(ga.AuthError, match="login de Google"):
        auth.access_token(inspeccion_id=9, ambito="resultado")
    assert auth.is_logged_in() and auth.es_google


def test_broker_rechazado_para_resultado(auth):
    auth._modo = ga.MODO_BROKER
    auth._refresh_token = "dev"
    with pytest.raises(ga.AuthError, match="Pi"):
        auth.access_token(inspeccion_id=9, ambito="resultado")


def test_token_gcs_con_google_usa_gcs_token_de_la_suite_no_el_oauth_crudo(auth, suite, monkeypatch):
    from atom_core import cloud_upload as cu
    _suite_resultado(suite, monkeypatch)
    _como_google(auth, monkeypatch)
    tok = cu.token_gcs(auth, None, inspeccion_id=9, ambito="resultado")
    assert tok == "res-1" and tok != "at"
    assert suite.de(ga.GCS_TOKEN_URI)[0].get_header("Authorization") == "Bearer IDTOK"


def test_sanear_log_oculta_jwt_y_tokens_largos():
    t = ga._sanear_log("fallo eyJabc.def.ghi y " + "A" * 60)
    assert "eyJ" not in t and "AAAA" not in t


def test_forzar_manda_forzar_true_y_usa_clave_cache_distinta(auth, suite, monkeypatch):
    _suite_resultado(suite, monkeypatch)
    auth.login_password("ana", PWD)
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-1"
    assert auth.access_token(inspeccion_id=9, ambito="resultado", forzar=True) == "res-2"
    reqs = suite.de(ga.GCS_TOKEN_URI)
    assert json.loads(reqs[0].data) == {"inspeccion_id": 9, "ambito": "resultado"}
    assert json.loads(reqs[1].data) == {"inspeccion_id": 9, "ambito": "resultado", "forzar": True}
    assert json.loads(reqs[1].data)["forzar"] is True
    assert "@resultado:9" in auth._gcs_cache and "@resultado:9:forzar" in auth._gcs_cache
    # Cada uno se cachea por separado: no se reutiliza uno sin admin ni al revés.
    assert auth.access_token(inspeccion_id=9, ambito="resultado") == "res-1"
    assert auth.access_token(inspeccion_id=9, ambito="resultado", forzar=True) == "res-2"
    assert len(suite.de(ga.GCS_TOKEN_URI)) == 2
    assert auth.prefijo_resultado(9, forzar=True) == PREFIJO


def test_proveedor_resultado_propaga_forzar_a_la_peticion(auth, suite, monkeypatch):
    from atom_core import subida_resultado as sr
    _suite_resultado(suite, monkeypatch)
    auth.login_password("ana", PWD)
    prov = sr.ProveedorResultado(auth, 9, forzar=True)
    assert prov.prefijo_destino() == PREFIJO
    assert prov.headers()["Authorization"] == "Bearer res-1"
    assert json.loads(suite.de(ga.GCS_TOKEN_URI)[0].data)["forzar"] is True
    sin = sr.ProveedorResultado(auth, 9)
    sin.prefijo_destino()
    assert "forzar" not in json.loads(suite.de(ga.GCS_TOKEN_URI)[1].data)
