"""resolve_target: URL servida a QtWebEngine según modo dev/prod.

En prod usa file:// con cache-busting por versión (#v=<version>) para que el
perfil de QtWebEngine no siga sirviendo el index.html cacheado de una versión
anterior tras una actualización in-place.
"""

import urllib.parse
from pathlib import Path

import pytest

import app_webview
from app_webview import DEV_URL, DIST_INDEX, resolve_target


def test_dev_devuelve_dev_url():
    assert resolve_target(dev=True) == DEV_URL


def test_prod_sin_build_sale_por_system_exit(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: False)
    with pytest.raises(SystemExit):
        resolve_target(dev=False)


def test_prod_con_build_devuelve_file_uri_con_version(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(app_webview, "_app_version_for_title", lambda: "3.4.71")

    destino = resolve_target(dev=False)

    assert destino.startswith("file://")
    assert destino.endswith("#v=3.4.71")
    assert DIST_INDEX.resolve().as_uri() in destino


def test_prod_version_desconocida_se_urlencodea(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(app_webview, "_app_version_for_title", lambda: "?")

    destino = resolve_target(dev=False)

    assert destino.endswith(f"#v={urllib.parse.quote('?')}")
