import json
import queue

from atom_core.event_sink import WebviewSink, QueueSink


class _FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, js):
        self.scripts.append(js)


class _FakeEvent:
    """Imita `window.events.loaded` de pywebview: `+=` registra un callback."""

    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def fire(self, *args):
        for h in list(self._handlers):
            h(*args)


class _FakeEvents:
    def __init__(self):
        self.loaded = _FakeEvent()


class _FakeWindowConLoaded(_FakeWindow):
    """Como `_FakeWindow` pero con `events.loaded`, para probar la carrera
    del arranque (incidente 3.4.102): ningún `evaluate_js` antes de "loaded".
    """

    def __init__(self):
        super().__init__()
        self.events = _FakeEvents()


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


def test_webview_sink_encola_antes_de_loaded_sin_llamar_evaluate_js():
    # Incidente 3.4.102: un hilo de fondo (comprobación de credencial) podía
    # empujar un evento ANTES de que la ventana terminara de cargar y
    # `evaluate_js` se quedaba colgado hasta tumbar el arranque.
    win = _FakeWindowConLoaded()
    sink = WebviewSink(win)
    sink.dispatch("atom:cloud", {"kind": "session", "ok": True})
    assert win.scripts == []


def test_webview_sink_drena_en_orden_tras_loaded():
    win = _FakeWindowConLoaded()
    sink = WebviewSink(win)
    sink.dispatch("atom:cloud", {"kind": "session", "n": 1})
    sink.dispatch("atom:cloud", {"kind": "session", "n": 2})
    assert win.scripts == []

    win.events.loaded.fire()

    assert len(win.scripts) == 2
    assert json.dumps({"kind": "session", "n": 1}) in win.scripts[0]
    assert json.dumps({"kind": "session", "n": 2}) in win.scripts[1]

    # Tras "loaded", los siguientes se entregan directos, sin volver a encolar.
    sink.dispatch("atom:cloud", {"kind": "session", "n": 3})
    assert len(win.scripts) == 3
