import json
import threading
import urllib.request

import pytest

from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor


class _ApiFalsa:
    def ping(self, who="?"):
        return {"ok": True}


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    sink = QueueSink()
    srv = crear_servidor(_ApiFalsa(), str(tmp_path), "127.0.0.1", 0, sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, sink, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_events_entrega_lo_que_se_empuja_por_el_sink(servidor):
    _, sink, base = servidor
    stream = urllib.request.urlopen(f"{base}/events", timeout=5)
    assert stream.headers["Content-Type"].startswith("text/event-stream")

    # Se empuja DESPUES de abrir el stream: la suscripcion ya esta viva.
    def empujar():
        import time
        time.sleep(0.3)
        sink.dispatch("atom:cloud", {"kind": "done", "ok": True, "uploaded": 7})

    threading.Thread(target=empujar, daemon=True).start()

    lineas = []
    for _ in range(6):
        linea = stream.readline().decode().strip()
        if linea:
            lineas.append(linea)
        if any(l.startswith("data:") for l in lineas):
            break
    stream.close()

    assert any(l == "event: atom:cloud" for l in lineas), lineas
    datos = [json.loads(l[len("data:"):]) for l in lineas if l.startswith("data:")]
    assert datos[0]["uploaded"] == 7
