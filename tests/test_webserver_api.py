import json
import urllib.error
import urllib.request

import pytest

from atom_core.event_sink import QueueSink
from atom_core.webserver import METODOS_EXPUESTOS, crear_servidor


class _ApiFalsa:
    def __init__(self):
        self.llamadas = []

    def ping(self, who="?"):
        self.llamadas.append(("ping", who))
        return {"ok": True, "msg": f"pong {who}"}

    def cloud_status(self):
        return {"configured": True, "bucket": "datos_para_organizar"}

    def revienta(self):
        raise ValueError("fallo interno")

    def _privado(self):
        return "no deberia salir"


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html>ATOM</html>", encoding="utf-8")
    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    import threading
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(base, metodo, args):
    req = urllib.request.Request(
        f"{base}/api/{metodo}",
        data=json.dumps({"args": args}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_sirve_el_index(servidor):
    _, _, base = servidor
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        assert b"ATOM" in r.read()


def test_llama_a_un_metodo_de_la_api_con_argumentos(servidor):
    _, api, base = servidor
    assert _post(base, "ping", ["rebeca"])["result"]["msg"] == "pong rebeca"
    assert api.llamadas == [("ping", "rebeca")]


def test_metodo_sin_argumentos(servidor):
    _, _, base = servidor
    assert _post(base, "cloud_status", [])["result"]["bucket"] == "datos_para_organizar"


def test_metodo_privado_no_es_alcanzable(servidor):
    _, _, base = servidor
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "_privado", [])
    assert exc.value.code == 404


def test_metodo_fuera_de_la_allowlist_no_es_alcanzable(servidor):
    # Aunque exista en el objeto: la allowlist manda, no `hasattr`.
    _, _, base = servidor
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "revienta", [])
    assert exc.value.code == 404


def test_excepcion_del_metodo_se_devuelve_como_error_no_como_500_mudo(servidor, tmp_path):
    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        # `ping` con un argumento de mas: TypeError dentro del metodo
        req = urllib.request.Request(
            f"{base}/api/ping",
            data=json.dumps({"args": ["a", "b", "c"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        cuerpo = json.loads(exc.value.read())
        assert "error" in cuerpo
    finally:
        srv.shutdown()


def test_la_allowlist_cubre_los_metodos_reales_de_la_api():
    # Si alguien anade un metodo publico a `Api` y olvida exponerlo, que se vea.
    import app_webview
    publicos = {
        n for n in dir(app_webview.Api)
        if not n.startswith("_") and callable(getattr(app_webview.Api, n))
    } - {"bind_window", "bind_sink"}
    assert publicos == set(METODOS_EXPUESTOS), (
        f"faltan por exponer: {publicos - set(METODOS_EXPUESTOS)}; "
        f"sobran en la allowlist: {set(METODOS_EXPUESTOS) - publicos}"
    )


def test_index_no_se_cachea(servidor):
    # El HTML no lleva nombre con hash: si el navegador lo cachea, se queda
    # con el bundle viejo. Debe llevar siempre no-store.
    _, _, base = servidor
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        assert "no-store" in r.headers.get("Cache-Control", "")


def test_asset_con_extension_si_se_cachea(servidor, tmp_path):
    # Los assets llevan hash en el nombre: cambiar el fichero cambia la URL,
    # asi que si se pueden cachear (no llevan no-store).
    (tmp_path / "app.js").write_text("console.log('x')", encoding="utf-8")
    _, _, base = servidor
    with urllib.request.urlopen(f"{base}/app.js", timeout=5) as r:
        assert "no-store" not in r.headers.get("Cache-Control", "")
