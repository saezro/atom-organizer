import json
import threading
import urllib.error
import urllib.request

import pytest

from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor


class _ApiFalsa:
    def __init__(self):
        self.llamadas = []

    def ping(self, who="?"):
        self.llamadas.append(("ping", who))
        return {"ok": True, "msg": f"pong {who}"}


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html>ATOM</html>", encoding="utf-8")
    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(base, metodo, headers, cuerpo_bytes):
    req = urllib.request.Request(
        f"{base}/api/{metodo}",
        data=cuerpo_bytes,
        headers=headers,
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=5)


def test_content_type_text_plain_da_415_y_no_ejecuta(servidor):
    _, api, base = servidor
    body = json.dumps({"args": ["rebeca"]}).encode()
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "ping", {"Content-Type": "text/plain"}, body)
    assert exc.value.code == 415
    assert api.llamadas == []


def test_sin_content_type_da_415_y_no_ejecuta(servidor):
    _, api, base = servidor
    body = json.dumps({"args": ["rebeca"]}).encode()
    req = urllib.request.Request(f"{base}/api/ping", data=body, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 415
    assert api.llamadas == []


def test_content_type_con_charset_es_200(servidor):
    _, _, base = servidor
    body = json.dumps({"args": ["rebeca"]}).encode()
    headers = {"Content-Type": "application/json; charset=utf-8"}
    with _post(base, "ping", headers, body) as r:
        assert r.status == 200


def test_origin_ajeno_da_403_y_no_ejecuta(servidor):
    _, api, base = servidor
    body = json.dumps({"args": ["rebeca"]}).encode()
    headers = {"Content-Type": "application/json", "Origin": "https://evil.example"}
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "ping", headers, body)
    assert exc.value.code == 403
    assert api.llamadas == []


def test_origin_localhost_es_200(servidor):
    _, _, base = servidor
    body = json.dumps({"args": ["rebeca"]}).encode()
    headers = {"Content-Type": "application/json", "Origin": "http://localhost:5173"}
    with _post(base, "ping", headers, body) as r:
        assert r.status == 200


def test_sin_origin_es_200(servidor):
    _, _, base = servidor
    body = json.dumps({"args": ["rebeca"]}).encode()
    headers = {"Content-Type": "application/json"}
    with _post(base, "ping", headers, body) as r:
        assert r.status == 200


def test_content_length_por_encima_del_limite_da_413(servidor):
    _, _, base = servidor
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(10 * 1024 * 1024 + 1),
    }
    req = urllib.request.Request(
        f"{base}/api/ping",
        data=b"{}",
        headers=headers,
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 413


def test_ping_con_querystring_es_200(servidor):
    _, _, base = servidor
    body = json.dumps({"args": []}).encode()
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        f"{base}/api/ping?x=1",
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


@pytest.mark.parametrize("largo", ["no-soy-un-numero", "-1"])
def test_content_length_invalido_es_400(servidor, largo):
    # Llega del cliente sin filtrar: un valor no numerico reventaba el handler
    # sin responder, y uno negativo hacia `rfile.read(-1)` (leer hasta EOF).
    _, api, base = servidor
    headers = {"Content-Type": "application/json", "Content-Length": largo}
    req = urllib.request.Request(
        f"{base}/api/ping",
        data=b"{}",
        headers=headers,
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400
    assert api.llamadas == []
