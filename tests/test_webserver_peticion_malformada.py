import socket
import threading

import pytest
import urllib.request

from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor


class _ApiFalsa:
    def ping(self, who="?"):
        return {"ok": True, "msg": f"pong {who}"}


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><head><title>ATOM</title></head><body></body></html>",
        encoding="utf-8",
    )
    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_peticion_malformada_no_tumba_el_servidor(servidor):
    """Regresion: `end_headers` leia `self.path` a pelo.

    Una linea de peticion con version HTTP invalida (ej. `GET / GARBAGE`)
    hace que `BaseHTTPRequestHandler.parse_request` llame a `send_error`
    -que acaba en `end_headers`- ANTES de que `self.path` llegue a
    asignarse. Sin el `getattr(..., "")` de la regresion, eso revienta con
    `AttributeError` dentro del hilo de la peticion.
    """
    srv, base = servidor
    host, port = srv.server_address[0], srv.server_address[1]

    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall(b"GET / GARBAGE\r\n\r\n")
        # El servidor debe poder responder algo (típicamente 400) sin que el
        # hilo reviente en silencio y cierre la conexión de golpe.
        datos = s.recv(4096)
        # Sin version HTTP valida, `send_error` responde en "modo HTTP/0.9"
        # (sin linea de estado, solo el cuerpo): lo que importa aqui es que
        # SI llega algo -el hilo no crasheo en silencio antes de escribir- y
        # que sea el error 400 esperado, no una conexion cortada de golpe.
        assert datos
        assert b"400" in datos

    # El servidor sigue vivo: una peticion normal siguiente responde bien.
    req = urllib.request.Request(f"{base}/", method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
