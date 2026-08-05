"""Pestaña «SUBIR AL BUCKET»: guarda anti-pisado y cableado del bridge.

`app_webview` importa `webview`, que no está en el entorno de tests (es la capa
de ventana, no lógica). Lo que se puede comprobar sin él —que el guard existe y
que el bridge JS y el Python se llaman igual— se comprueba sobre el fuente, que
es como ya se testea el resto del puente (`test_progress_stats.py`).
"""
from __future__ import annotations

import email.message
import io
import json
import os
import urllib.error

import pytest

from atom_core import cloud_upload as cu

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fuente(nombre: str) -> str:
    with open(os.path.join(REPO, nombre), encoding="utf-8") as fh:
        return fh.read()


# --- objetos_en_prefijo: la consulta que evita pisar un vuelo anterior -------

class _Resp(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.status = 200
        self.headers = email.message.Message()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Auth:
    def access_token(self, *, force_refresh: bool = False) -> str:
        return "tok"


def _falso_listado(items, capturado: dict):
    def urlopen(req, timeout=None):
        capturado["url"] = req.full_url
        capturado["auth"] = req.get_header("Authorization")
        return _Resp(json.dumps({"items": items}).encode())
    return urlopen


def test_prefijo_libre_devuelve_cero(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(cu.urllib.request, "urlopen", _falso_listado([], cap))
    assert cu.objetos_en_prefijo("datos_para_organizar", "ANTOLIN", _Auth()) == 0
    assert "prefix=ANTOLIN%2F" in cap["url"]
    assert cap["auth"] == "Bearer tok"


def test_prefijo_ocupado_lo_dice(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        _falso_listado([{"name": "ANTOLIN/DJI_0001.JPG"}], cap))
    assert cu.objetos_en_prefijo("datos_para_organizar", "ANTOLIN", _Auth()) == 1


def test_el_prefijo_consultado_termina_en_barra(monkeypatch):
    """Sin la barra, «ANTOLIN» casaría también con «ANTOLIN_2», y una carpeta
    nueva parecería ocupada por otra que no tiene nada que ver."""
    cap: dict = {}
    monkeypatch.setattr(cu.urllib.request, "urlopen", _falso_listado([], cap))
    cu.objetos_en_prefijo("b", "ANTOLIN", _Auth())
    assert "ANTOLIN%2F" in cap["url"]


def test_un_fallo_de_consulta_no_se_confunde_con_vacio(monkeypatch):
    """Si la consulta revienta hay que propagar: tragárselo y seguir subiría
    encima de datos existentes, que es justo lo que se quiere evitar."""
    def revienta(req, timeout=None):
        raise urllib.error.HTTPError("u", 403, "Forbidden", email.message.Message(), None)

    monkeypatch.setattr(cu.urllib.request, "urlopen", revienta)
    with pytest.raises(urllib.error.HTTPError):
        cu.objetos_en_prefijo("b", "ANTOLIN", _Auth())


# --- Cableado del bridge -----------------------------------------------------

BRIDGE_METODOS = ["cloud_status", "cloud_login", "cloud_logout",
                  "cloud_prepare", "cloud_upload", "cloud_cancel"]


@pytest.mark.parametrize("metodo", BRIDGE_METODOS)
def test_el_metodo_existe_en_python(metodo):
    assert f"def {metodo}(" in _fuente("app_webview.py")


@pytest.mark.parametrize("metodo", BRIDGE_METODOS)
def test_el_bridge_js_llama_al_mismo_nombre(metodo):
    assert f"'{metodo}'" in _fuente(os.path.join("webui", "src", "bridge.js"))


def test_la_subida_comprueba_el_destino_salvo_que_se_fuerce():
    src = _fuente("app_webview.py")
    assert "if not force:" in src
    assert "objetos_en_prefijo" in src


def test_la_subida_exige_sesion_iniciada():
    assert "auth.is_logged_in()" in _fuente("app_webview.py")


def test_hay_pestana_de_bucket_en_la_ui():
    src = _fuente(os.path.join("webui", "src", "App.jsx"))
    assert "SUBIR AL BUCKET" in src
    assert "BucketScreen" in src
