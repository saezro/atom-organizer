"""Modo servidor: sirve la webui por HTTP en vez de meterla en una ventana Qt.

Existe para la Raspberry Pi (ARM64), donde PySide6/QtWebEngine no es viable.
Usa solo la stdlib a proposito: anadir dependencias es justo el problema que
este modo resuelve.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# Allowlist explicita. NO se usa `hasattr` para decidir que es alcanzable: un
# metodo nuevo debe entrar aqui a mano y de forma consciente.
METODOS_EXPUESTOS = frozenset({
    "ping",
    "pick_folder", "pick_file",
    # OJO: `list_dir` se anade en la Task 7, cuando el metodo exista. La
    # allowlist y los metodos reales de `Api` se validan con un test.
    "folder_is_empty", "read_estadillo_info", "detect_suffixes",
    "read_config", "write_config",
    "app_version", "check_update", "download_update", "install_update",
    "start_update_check",
    "cloud_status", "cloud_verify", "cloud_login", "cloud_logout",
    "cloud_inspecciones", "cloud_prepare", "cloud_upload", "cloud_cancel",
    "estadillo_validar", "estadillo_subir", "estadillo_existente",
    "run_organize", "run_task",
})


def _handler_factory(api, dist_dir: str, sink):
    class Handler(SimpleHTTPRequestHandler):
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
            if not self.path.startswith("/api/"):
                return self._json(404, {"error": "ruta desconocida"})
            metodo = self.path[len("/api/"):].strip("/")
            if metodo not in METODOS_EXPUESTOS:
                return self._json(404, {"error": f"metodo no expuesto: {metodo}"})
            try:
                largo = int(self.headers.get("Content-Length") or 0)
                cuerpo = json.loads(self.rfile.read(largo) or b"{}")
                args = cuerpo.get("args") or []
                resultado = getattr(api, metodo)(*args)
            except Exception as exc:  # noqa: BLE001 — el front necesita el motivo
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
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
