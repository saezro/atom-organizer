import pytest

import app_webview
from atom_core import cloud_upload


class _AuthFake:
    """Lo mínimo que `estadillo_existente` mira del auth: si hay sesión."""

    def __init__(self, logueado=True):
        self._logueado = logueado

    def is_logged_in(self):
        return self._logueado


@pytest.fixture
def api():
    return app_webview.Api()


@pytest.fixture
def api_con_sesion(api, monkeypatch):
    monkeypatch.setattr(api, "_get_auth", lambda: _AuthFake())
    return api


def test_existe_true_si_hay_objetos(api_con_sesion, monkeypatch):
    monkeypatch.setattr(cloud_upload, "objetos_en_prefijo", lambda *a, **k: 3)

    res = api_con_sesion.estadillo_existente("MI_PLANTA")

    assert res == {"existe": True, "error": None}


def test_existe_false_si_cero_objetos(api_con_sesion, monkeypatch):
    monkeypatch.setattr(cloud_upload, "objetos_en_prefijo", lambda *a, **k: 0)

    res = api_con_sesion.estadillo_existente("MI_PLANTA")

    assert res == {"existe": False, "error": None}


def test_falla_open_si_objetos_en_prefijo_lanza(api_con_sesion, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("sin red")

    monkeypatch.setattr(cloud_upload, "objetos_en_prefijo", _boom)

    res = api_con_sesion.estadillo_existente("MI_PLANTA")

    assert res["existe"] is False
    assert res["error"]


def test_falla_open_sin_login(api, monkeypatch):
    monkeypatch.setattr(api, "_get_auth", lambda: _AuthFake(logueado=False))

    res = api.estadillo_existente("MI_PLANTA")

    assert res["existe"] is False
    assert res["error"]


def test_construye_el_prefijo_bajo_actual(api_con_sesion, monkeypatch):
    llamadas = []

    def _fake(bucket, prefix, auth, **k):
        llamadas.append(prefix)
        return 0

    monkeypatch.setattr(cloud_upload, "objetos_en_prefijo", _fake)

    api_con_sesion.estadillo_existente("MI_PLANTA")

    assert len(llamadas) == 1
    assert llamadas[0].endswith("/ESTADILLOS/actual/")
