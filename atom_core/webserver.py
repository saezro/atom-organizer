"""Modo servidor: sirve la webui por HTTP en vez de meterla en una ventana Qt.

Existe para la Raspberry Pi para no arrastrar un segundo Chromium (el de
QtWebEngine, ~400 MB) en una maquina que ya trae el suyo con aceleracion
propia, y porque los dialogos nativos de Qt son inusables en una pantalla
tactil de 480x320.
Usa solo la stdlib a proposito: anadir dependencias es justo el problema que
este modo resuelve.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# Allowlist explicita. NO se usa `hasattr` para decidir que es alcanzable: un
# metodo nuevo debe entrar aqui a mano y de forma consciente.
METODOS_EXPUESTOS = frozenset({
    "ping",
    "pick_folder", "pick_file", "list_dir", "default_dir",
    "folder_is_empty", "read_estadillo_info", "detect_suffixes",
    "read_config", "write_config",
    "app_version", "check_update", "download_update", "install_update",
    "start_update_check",
    "cloud_status", "cloud_verify", "cloud_login", "cloud_logout",
    "cloud_inspecciones", "cloud_prepare", "cloud_upload", "cloud_cancel",
    "estadillo_validar", "estadillo_subir", "estadillo_existente",
    "run_organize", "run_task",
})

# Origenes considerados same-origin/local para la validacion de CSRF en
# `do_POST`. El puerto es irrelevante, solo importa el host.
_ORIGENES_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Ningun metodo de la allowlist sube ficheros por HTTP (`cloud_upload` sube
# al bucket desde disco), asi que un body razonable basta de sobra.
_MAX_BODY = 10 * 1024 * 1024  # 10 MB


def _origen_permitido(origin: str) -> bool:
    return urlsplit(origin).hostname in _ORIGENES_LOOPBACK


def _handler_factory(api, dist_dir: str, sink):
    class Handler(SimpleHTTPRequestHandler):
        timeout = 30  # no aplica al SSE, que es de larga duracion por diseno

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=dist_dir, **kw)

        def log_message(self, fmt, *args):
            pass  # el log de acceso por request no aporta nada aqui

        def _json(self, code: int, payload: dict) -> None:
            cuerpo = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            if self.path.rstrip("/") == "/events":
                return self._sse()
            return super().do_GET()

        def _sse(self) -> None:
            """Un solo stream para los tres canales de eventos.

            Sustituye a `evaluate_js`: el navegador no puede recibir un push que
            el shell le inyecte, asi que se invierte el sentido y es el cliente
            quien mantiene la conexion abierta.
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            cola = sink.subscribe()
            try:
                while True:
                    try:
                        evento, detalle = cola.get(timeout=15)
                    except queue.Empty:
                        # Comentario keep-alive: sin trafico, un proxy o el
                        # propio navegador cerrarian la conexion en silencio.
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    payload = (f"event: {evento}\n"
                               f"data: {json.dumps(detalle)}\n\n").encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # el navegador cerro la pestana
            finally:
                sink.unsubscribe(cola)

        def do_POST(self):
            ruta = self.path.split("?", 1)[0]
            if not ruta.startswith("/api/"):
                return self._json(404, {"error": "ruta desconocida"})
            metodo = ruta[len("/api/"):].strip("/")
            if metodo not in METODOS_EXPUESTOS:
                return self._json(404, {"error": f"metodo no expuesto: {metodo}"})

            # CSRF: sin esto, un <form enctype="text/plain"> en cualquier web
            # abierta en el Chromium de la Pi puede llamar a estos metodos sin
            # interaccion del usuario (simple request, sin preflight CORS).
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return self._json(415, {"error": "Content-Type debe ser application/json"})

            origin = self.headers.get("Origin")
            if origin and not _origen_permitido(origin):
                return self._json(403, {"error": "origen no permitido"})

            # Content-Length llega del cliente: puede no ser un numero, o ser
            # negativo (y `rfile.read(-1)` leeria hasta EOF, colgando el hilo
            # hasta el timeout). Se valida antes de usarlo.
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "Content-Length invalido"})
            if largo < 0:
                return self._json(400, {"error": "Content-Length invalido"})
            if largo > _MAX_BODY:
                return self._json(413, {"error": "cuerpo demasiado grande"})

            try:
                cuerpo = json.loads(self.rfile.read(largo) or b"{}")
                args = cuerpo.get("args") or []
                resultado = getattr(api, metodo)(*args)
            except Exception:  # noqa: BLE001 — no filtrar detalles internos al cliente
                traceback.print_exc()
                return self._json(500, {"error": "error interno al ejecutar el metodo"})
            return self._json(200, {"result": resultado})

    return Handler


def crear_servidor(api, dist_dir: str, host: str, port: int, sink) -> ThreadingHTTPServer:
    if not os.path.isdir(dist_dir):
        raise FileNotFoundError(f"No existe el build del front: {dist_dir}")
    mimetypes.add_type("application/javascript", ".js")
    servidor = ThreadingHTTPServer((host, port), _handler_factory(api, dist_dir, sink))
    servidor.daemon_threads = True
    return servidor


def servir(api, dist_dir: str, host: str, port: int, sink) -> None:
    servidor = crear_servidor(api, dist_dir, host, port, sink)
    print(f"[atom] UI en http://{host}:{servidor.server_address[1]}  (Ctrl-C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        servidor.shutdown()
