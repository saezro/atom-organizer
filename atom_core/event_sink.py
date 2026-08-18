"""Empuje de eventos Python -> JS, desacoplado del shell que los transporta.

Existe porque el Organizer corre en dos shells distintos: pywebview/Qt en
Windows (donde el transporte es `evaluate_js`) y un navegador contra el modo
`--server` en Raspberry Pi (donde es SSE). La clase `Api` no debe saber en
cual de los dos esta.
"""

from __future__ import annotations

import json
import queue
import threading


class EventSink:
    """Interfaz. `event` es el nombre del CustomEvent ('atom:progress'...)."""

    def dispatch(self, event: str, detail: dict) -> None:
        raise NotImplementedError

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        for d in details:
            self.dispatch(event, d)


class WebviewSink(EventSink):
    """Transporte historico: ejecuta el dispatchEvent dentro de la ventana."""

    def __init__(self, window) -> None:
        self._window = window

    def _run(self, js: str) -> None:
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass  # ventana cerrada a mitad de proceso

    def dispatch(self, event: str, detail: dict) -> None:
        self._run(f"window.dispatchEvent(new CustomEvent({json.dumps(event)},"
                  f"{{detail:{json.dumps(detail)}}}));")

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        if not details:
            return
        # UN solo viaje para N eventos: `evaluate_js` de Qt es sincrono y cada
        # llamada para el hilo del pipeline hasta que Chromium responde.
        self._run("".join(
            f"window.dispatchEvent(new CustomEvent({json.dumps(event)},"
            f"{{detail:{json.dumps(d)}}}));"
            for d in details
        ))


class QueueSink(EventSink):
    """Transporte del modo servidor: reparte a las colas de los suscriptores SSE.

    Cada respuesta SSE abierta es un suscriptor. Si nadie consume (navegador
    cerrado sin cerrar la conexion) se descarta lo mas viejo en vez de crecer
    sin limite: son eventos de progreso, el ultimo es el que importa.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def dispatch(self, event: str, detail: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            while True:
                try:
                    q.put_nowait((event, detail))
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
