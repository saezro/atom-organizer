import json
import queue

from atom_core.event_sink import WebviewSink, QueueSink


class _FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, js):
        self.scripts.append(js)


def test_webview_sink_emite_customevent_con_el_detalle():
    win = _FakeWindow()
    sink = WebviewSink(win)
    sink.dispatch("atom:cloud", {"kind": "log", "text": "hola"})
    assert len(win.scripts) == 1
    js = win.scripts[0]
    assert "atom:cloud" in js
    assert json.dumps({"kind": "log", "text": "hola"}) in js


def test_webview_sink_agrupa_varios_en_un_solo_evaluate_js():
    # El batching existe porque evaluate_js de Qt es SINCRONO y bloquea el
    # hilo del pipeline. Un solo viaje para N eventos es el punto entero.
    win = _FakeWindow()
    sink = WebviewSink(win)
    sink.dispatch_many("atom:progress", [{"kind": "log", "text": "a"},
                                         {"kind": "log", "text": "b"}])
    assert len(win.scripts) == 1
    assert win.scripts[0].count("dispatchEvent") == 2


def test_webview_sink_traga_la_excepcion_si_la_ventana_murio():
    class _Muerta:
        def evaluate_js(self, js):
            raise RuntimeError("ventana cerrada")

    WebviewSink(_Muerta()).dispatch("atom:update", {"kind": "error"})  # no revienta


def test_queue_sink_entrega_a_cada_suscriptor():
    sink = QueueSink()
    a = sink.subscribe()
    b = sink.subscribe()
    sink.dispatch("atom:cloud", {"kind": "done", "ok": True})
    assert a.get_nowait() == ("atom:cloud", {"kind": "done", "ok": True})
    assert b.get_nowait() == ("atom:cloud", {"kind": "done", "ok": True})


def test_queue_sink_descarta_lo_viejo_si_nadie_consume():
    # Un navegador cerrado no debe hacer crecer la memoria sin limite.
    sink = QueueSink(maxsize=2)
    q = sink.subscribe()
    for i in range(5):
        sink.dispatch("atom:progress", {"kind": "progress", "value": i})
    assert q.qsize() == 2
    assert q.get_nowait()[1]["value"] == 3  # se quedan los 2 ultimos


def test_queue_sink_unsubscribe_deja_de_recibir():
    sink = QueueSink()
    q = sink.subscribe()
    sink.unsubscribe(q)
    sink.dispatch("atom:cloud", {"kind": "log"})
    assert q.empty()
