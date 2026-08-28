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
        "<html><head><title>ATOM</title></head>"
        "<body><script src=\"/assets/index-abc123.js\"></script></body></html>",
        encoding="utf-8",
    )
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "x.js").write_text("console.log('hola');", encoding="utf-8")

    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base, ruta):
    req = urllib.request.Request(f"{base}{ruta}", method="GET")
    return urllib.request.urlopen(req, timeout=5)


def test_raiz_sirve_index_con_marca_antes_del_bundle(servidor):
    _, base = servidor
    with _get(base, "/") as r:
        assert r.status == 200
        assert r.headers.get("Content-Type", "").startswith("text/html")
        cuerpo = r.read().decode("utf-8")
    idx_marca = cuerpo.find("window.__ATOM_SERVIDOR__ = true")
    idx_script = cuerpo.find("<script src=")
    assert idx_marca != -1
    assert idx_script != -1
    assert idx_marca < idx_script


def test_index_html_explicito_sirve_index_con_marca_antes_del_bundle(servidor):
    _, base = servidor
    with _get(base, "/index.html") as r:
        assert r.status == 200
        assert r.headers.get("Content-Type", "").startswith("text/html")
        cuerpo = r.read().decode("utf-8")
    idx_marca = cuerpo.find("window.__ATOM_SERVIDOR__ = true")
    idx_script = cuerpo.find("<script src=")
    assert idx_marca != -1
    assert idx_script != -1
    assert idx_marca < idx_script


def test_asset_normal_se_sirve_tal_cual_sin_marca(servidor):
    _, base = servidor
    with _get(base, "/assets/x.js") as r:
        assert r.status == 200
        cuerpo = r.read().decode("utf-8")
    assert cuerpo == "console.log('hola');"
    assert "__ATOM_SERVIDOR__" not in cuerpo
