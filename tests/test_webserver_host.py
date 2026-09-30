"""Anti DNS-rebinding: Host fuera de la lista blanca -> 403."""
import http.client
import threading

import pytest

from atom_core.event_sink import QueueSink
from atom_core.webserver import _host_permitido, _origen_permitido, crear_servidor


class _Api:
    def ping(self, who="?"):
        return {"ok": True}


@pytest.fixture
def srv(tmp_path):
    (tmp_path / "index.html").write_text("<html>ATOM</html>", encoding="utf-8")
    s = crear_servidor(_Api(), str(tmp_path), "127.0.0.1", 0, QueueSink())
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def _req(s, metodo, ruta, host, **h):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
    c.putrequest(metodo, ruta, skip_host=True)
    c.putheader("Host", host)
    for k, v in h.items():
        c.putheader(k.replace("_", "-"), v)
    c.putheader("Content-Length", "2" if metodo == "POST" else "0")
    c.endheaders(b"{}" if metodo == "POST" else b"")
    r = c.getresponse()
    r.read()
    return r.status


def test_get_host_ajeno_403(srv):
    assert _req(srv, "GET", "/", "evil.example.com") == 403
    assert _req(srv, "GET", "/", f"evil.example.com:{srv.server_address[1]}") == 403


def test_get_host_propio_ok(srv):
    p = srv.server_address[1]
    assert _req(srv, "GET", "/", f"127.0.0.1:{p}") == 200
    assert _req(srv, "GET", "/", f"localhost:{p}") == 200


def test_post_host_ajeno_403_aunque_origin_igual(srv):
    st = _req(srv, "POST", "/api/ping", "evil.example.com",
              Content_Type="application/json", Origin="http://evil.example.com")
    assert st == 403


def test_lista_blanca():
    for h in ("127.0.0.1:8000", "localhost", "[::1]:8000", "organizer.atom",
              "organizer.local", "10.42.0.1", "192.168.1.20:80"):
        assert _host_permitido(h), h
    for h in ("", "evil.com", "127.0.0.1.evil.com", "organizer.atom.evil.com"):
        assert not _host_permitido(h), h
    assert not _host_permitido("localhost:9999", 8000)


def test_origen_igual_host_no_basta():
    assert not _origen_permitido("http://evil.com", "evil.com")
    assert _origen_permitido("http://10.42.0.1", "10.42.0.1")
    assert _origen_permitido("http://organizer.atom", "organizer.atom")
