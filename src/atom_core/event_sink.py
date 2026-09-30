"""Empuje de eventos Python -> JS, desacoplado del shell que los transporta.

Existe porque el Organizer corre en dos shells distintos: pywebview/Qt en
Windows (donde el transporte es `evaluate_js`) y un navegador contra el modo
`--server` en Raspberry Pi (donde es SSE). La clase `Api` no debe saber en
cual de los dos esta.
"""

from __future__ import annotations

import json
import logging
import queue
import threading

logger = logging.getLogger(__name__)


class EventSink:
    """Interfaz. `event` es el nombre del CustomEvent ('atom:progress'...)."""

    def dispatch(self, event: str, detail: dict) -> None:
        raise NotImplementedError

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        for d in details:
            self.dispatch(event, d)


class WebviewSink(EventSink):
    """Transporte historico: ejecuta el dispatchEvent dentro de la ventana.

    `evaluate_js` antes de que la ventana termine de cargar (evento
    `window.events.loaded`) es lo que colgaba el arranque en 3.4.102: un hilo
    de fondo (comprobación de credencial al arrancar) podía disparar el
    primer push ANTES de que pywebview hubiera inicializado la ventana nativa
    (`webview.start()` aún no había corrido), y esa llamada se quedaba
    bloqueada hasta el timeout de `WebViewException: Main window failed to
    start`. Mientras no haya `loaded`, los eventos se encolan en orden y se
    drenan de golpe en cuanto llega — nunca se llama a `evaluate_js` antes.
    """

    def __init__(self, window) -> None:
        self._window = window
        self._lock = threading.Lock()
        self._cargado = False
        self._cola: list[str] = []
        try:
            window.events.loaded += self._on_loaded
        except Exception as exc:  # noqa: BLE001 — sin este hook, mejor entregar
            # directo (comportamiento previo) que quedarse mudo para siempre.
            logger.warning("no se pudo enganchar events.loaded, entrega directa (%s): %s",
                           type(exc).__name__, exc)
            self._cargado = True

    def _on_loaded(self, *_args) -> None:
        with self._lock:
            if self._cargado:
                return
            self._cargado = True
            pendientes = self._cola
            self._cola = []
        for js in pendientes:
            self._entregar(js)

    def _entregar(self, js: str) -> None:
        try:
            self._window.evaluate_js(js)
        except Exception as exc:  # noqa: BLE001 — perder un evento no puede tumbar el pipeline
            # Se traga a propósito (lo normal es la ventana cerrada a mitad de
            # proceso), pero NUNCA en silencio: si el transporte se rompe, el
            # front deja de recibir progreso y el modal se queda mudo en
            # "Preparando…" sin que quede rastro de por qué. Al log, que es lo
            # que se le pide al usuario cuando reporta un cuelgue.
            logger.warning("no se pudo entregar el evento a la ventana (%s): %s",
                           type(exc).__name__, exc)

    def _run(self, js: str) -> None:
        with self._lock:
            if not self._cargado:
                self._cola.append(js)
                return
        self._entregar(js)

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
