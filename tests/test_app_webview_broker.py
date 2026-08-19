"""`Api` en modo broker (Raspberry Pi) — que el modo sea ALCANZABLE.

Antes de esto, `Api._get_auth()` devolvía `None` en cuanto no había
`google_client.json`, y la Pi nunca tendrá ese fichero: `cloud_status`
contestaba `configured: false` y la UI ni ofrecía emparejar. Se comprueba que
`Api(broker=True)` sí construye una `GoogleAuth` broker_only, que `Api()` (el
escritorio, sin el flag) sigue sin tocarla, y que `cloud_status` expone
`pairing` para que la UI sepa qué pantalla enseñar.

`app_webview` no requiere `webview` para importarse (solo al abrir ventana de
verdad, en `_import_webview`), así que se importa directo como en
`test_cloud_bucket_tab.py`.
"""
from __future__ import annotations

import pytest

import app_webview as aw
from atom_core import google_auth as ga


@pytest.fixture(autouse=True)
def sin_cliente_oauth(monkeypatch):
    """El repo de test no lleva `google_client.json`: se fuerza igual, para no
    depender de que nadie deje uno suelto en el checkout."""
    from atom_core import cloud_config

    monkeypatch.setattr(cloud_config, "load_client", lambda base_dir=None: None)


def test_api_sin_broker_sigue_sin_auth_si_no_hay_cliente():
    """El escritorio (Windows) no cambia: sin `google_client.json`, `None`."""
    api = aw.Api()
    assert api._get_auth() is None


def test_api_broker_construye_una_instancia_broker_only(tmp_path, monkeypatch):
    # `user_data_dir` no se usa aquí: `_get_auth` no expone `store_path`, así
    # que basta con comprobar el tipo de auth resultante.
    api = aw.Api(broker=True)
    auth = api._get_auth()
    assert auth is not None
    assert isinstance(auth, ga.GoogleAuth)
    assert auth.broker_only is True


def test_get_auth_cachea_la_instancia_broker():
    api = aw.Api(broker=True)
    assert api._get_auth() is api._get_auth()


def test_cloud_status_expone_pairing_true_en_modo_broker():
    api = aw.Api(broker=True)
    estado = api.cloud_status()
    assert estado["configured"] is True
    assert estado["pairing"] is True


def test_cloud_status_sin_broker_no_ofrece_pairing():
    api = aw.Api()
    estado = api.cloud_status()
    assert estado["configured"] is False
    assert estado["pairing"] is False


def test_cloud_login_en_broker_no_intenta_abrir_navegador():
    api = aw.Api(broker=True)
    res = api.cloud_login()
    assert res["ok"] is False
    assert "QR" in res["error"] or "emparej" in res["error"]
