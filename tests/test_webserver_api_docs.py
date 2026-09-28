import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from atom_core.webserver import (
    METODOS_EXPUESTOS,
    _RUTA_ESTADILLO_ESPERA,
    _RUTA_ESTADILLO_PING,
    _RUTA_ESTADILLO_RECIBIR,
    crear_servidor,
)
from atom_core.event_sink import QueueSink

_RE_IP = re.compile(r"\d+\.\d+\.\d+\.\d+")


class _ApiDocs:
    def ping(self, who="?"):
        return {"ok": True, "msg": f"pong {who}"}

    def estadillo_espera_estado(self):
        return {"esperando": False, "caducado": False}

    def _estadillo_recibir(self, vuelos):
        return {"ok": True, "resumen": {}}

    def _estadillo_registrar_evento(self, ip, tipo, detalle=""):
        pass

    def _cuenta_actual(self):
        return None

    def red_conexion(self):
        return {"ok": True, "tipo": "ninguna", "ssid": "", "senal": None, "ip": ""}


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head>ATOM UI</html>", encoding="utf-8")
    api = _ApiDocs()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base, ruta, headers=None):
    req = urllib.request.Request(f"{base}{ruta}", headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def test_docs_html_200_content_type(servidor):
    status, cuerpo, headers = _get(servidor, "/api/docs")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"<html" in cuerpo.lower()


def test_docs_json_parseable_con_los_10_endpoints(servidor):
    status, cuerpo, headers = _get(servidor, "/api/docs?format=json")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    spec = json.loads(cuerpo)
    rutas = {ep["ruta"] for ep in spec["endpoints"]}
    assert {_RUTA_ESTADILLO_PING, _RUTA_ESTADILLO_ESPERA, _RUTA_ESTADILLO_RECIBIR} <= rutas
    assert len(spec["endpoints"]) == 10


def test_docs_no_contiene_ninguna_ip(servidor):
    for ruta in ("/api/docs", "/api/docs?format=json"):
        _, cuerpo, _ = _get(servidor, ruta)
        texto = cuerpo.decode("utf-8")
        assert not _RE_IP.search(texto), f"{ruta} contiene una IP: {_RE_IP.search(texto)}"


def test_docs_contiene_organizer_local(servidor):
    for ruta in ("/api/docs", "/api/docs?format=json"):
        _, cuerpo, _ = _get(servidor, ruta)
        assert b"organizer.local" in cuerpo


def test_docs_html_tiene_boton_ejecutar_por_get(servidor):
    status, cuerpo, _ = _get(servidor, "/api/docs")
    texto = cuerpo.decode("utf-8")
    assert status == 200
    for ruta in (_RUTA_ESTADILLO_PING, _RUTA_ESTADILLO_ESPERA):
        assert f'data-ruta="{ruta}"' in texto
    assert texto.count('class="btn-ejecutar') >= 2


def test_docs_html_post_pide_confirm_antes_de_enviar(servidor):
    _, cuerpo, _ = _get(servidor, "/api/docs")
    texto = cuerpo.decode("utf-8")
    assert f'data-ruta="{_RUTA_ESTADILLO_RECIBIR}"' in texto
    assert "confirm(" in texto
    assert "btn-enviar" in texto


def test_docs_html_tiene_boton_copiar_curl(servidor):
    _, cuerpo, _ = _get(servidor, "/api/docs")
    texto = cuerpo.decode("utf-8")
    assert texto.count('class="btn-curl') == 10


def test_docs_json_no_cambia_con_los_botones(servidor):
    status, cuerpo, headers = _get(servidor, "/api/docs?format=json")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    spec = json.loads(cuerpo)
    assert len(spec["endpoints"]) == 10
    assert "btn-ejecutar" not in cuerpo.decode("utf-8")


def test_docs_html_respuesta_se_inserta_con_textcontent_no_html(servidor):
    """Sin XSS: el cuerpo de la respuesta se pinta con `textContent`, nunca
    `innerHTML`/`insertAdjacentHTML` con datos del servidor remoto."""
    _, cuerpo, _ = _get(servidor, "/api/docs")
    texto = cuerpo.decode("utf-8")
    assert "pre.textContent = cuerpo" in texto


def test_docs_no_menciona_endpoints_internos(servidor):
    """Ningun metodo interno del kiosco (`METODOS_EXPUESTOS`, alcanzables via
    `/api/<metodo>`) debe aparecer mencionado en la documentacion publica."""
    metodos_a_revisar = METODOS_EXPUESTOS - {"ping"}  # "ping" es ambiguo con el ping LAN documentado
    for ruta in ("/api/docs", "/api/docs?format=json"):
        _, cuerpo, _ = _get(servidor, ruta)
        texto = cuerpo.decode("utf-8")
        for metodo in metodos_a_revisar:
            assert metodo not in texto, f"{ruta} menciona el metodo interno '{metodo}'"
        # Ninguna ruta generica `/api/<metodo>` (la del kiosco) debe aparecer.
        assert "/api/pin_" not in texto
        assert "/api/cloud_" not in texto
        assert "/api/run_organize" not in texto
