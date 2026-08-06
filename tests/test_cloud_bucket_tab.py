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

BRIDGE_METODOS = ["cloud_status", "cloud_verify", "cloud_login", "cloud_logout",
                  "cloud_prepare", "cloud_upload", "cloud_cancel"]


@pytest.mark.parametrize("metodo", BRIDGE_METODOS)
def test_el_metodo_existe_en_python(metodo):
    assert f"def {metodo}(" in _fuente("app_webview.py")


@pytest.mark.parametrize("metodo", BRIDGE_METODOS)
def test_el_bridge_js_llama_al_mismo_nombre(metodo):
    assert f"'{metodo}'" in _fuente(os.path.join("webui", "src", "bridge.js"))


def test_la_subida_reconcilia_el_destino_en_vez_de_bloquearse():
    """Subir sobre un destino con datos dejó de necesitar confirmación.

    Antes, encontrar objetos en el prefijo abortaba la subida y obligaba al
    operador a marcar «continuar subida» — una confirmación a ciegas, porque
    no se le decía qué había allí ni qué se iba a pisar. Ahora se identifica
    objeto a objeto lo que ya está y se descarta, así que relanzar la misma
    carpeta es seguro y barato, y no hay nada que forzar.
    """
    src = _fuente("app_webview.py")
    assert "listar_objetos_remotos" in src
    assert "if not force:" not in src


def test_la_ui_ya_no_pide_confirmar_para_continuar_una_subida():
    src = _fuente(os.path.join("webui", "src", "App.jsx"))
    assert "Continuar la subida en ese destino" not in src


def test_la_subida_exige_sesion_iniciada():
    assert "auth.is_logged_in()" in _fuente("app_webview.py")


def test_hay_pestana_de_bucket_en_la_ui():
    src = _fuente(os.path.join("webui", "src", "App.jsx"))
    assert "SUBIR AL BUCKET" in src
    assert "BucketScreen" in src


# --- Confirmación de la sesión ----------------------------------------------
# «Hay un token guardado» y «la sesión funciona» son cosas distintas, y la
# pantalla las enseñaba como una sola: con el token cacheado solo se veía
# «Cerrar sesión», sin decir de quién ni si servía.

def test_el_estado_no_finge_saber_si_la_sesion_vive():
    """`cloud_status` no puede hacer red (bloquearía el arranque), así que no
    debe afirmar que la sesión es válida: solo dice cuándo se comprobó."""
    src = _fuente("app_webview.py")
    assert "def cloud_status(" in src
    assert '"validada_en": auth.validada_en' in src


def test_comprobar_la_sesion_no_bloquea_el_bridge():
    """Un refresh con mala red tarda segundos; hacerlo síncrono congelaría la
    ventana igual que pasaba con el login."""
    src = _fuente("app_webview.py")
    inicio = src.index("def cloud_verify(")
    cuerpo = src[inicio:src.index("def cloud_logout(", inicio)]
    assert "threading.Thread" in cuerpo
    assert '"kind": "session"' in cuerpo


def test_la_ui_ensena_quien_esta_dentro_y_si_la_sesion_vale():
    src = _fuente(os.path.join("webui", "src", "App.jsx"))
    assert "cloudVerify" in src
    assert "Sesión activa y comprobada" in src
    assert "Volver a iniciar sesión" in src


def test_la_ui_avisa_cuando_el_almacen_no_se_puede_leer():
    """Perfil copiado a otro equipo: sin esto solo se vería un «sin iniciar
    sesión» que no encaja con lo que el operador recuerda."""
    assert "status?.aviso" in _fuente(os.path.join("webui", "src", "App.jsx"))
