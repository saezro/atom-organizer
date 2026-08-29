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
import secrets
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# Allowlist explicita. NO se usa `hasattr` para decidir que es alcanzable: un
# metodo nuevo debe entrar aqui a mano y de forma consciente.
METODOS_EXPUESTOS = frozenset({
    "ping",
    "pick_folder", "pick_file", "list_dir", "default_dir",
    "folder_is_empty", "read_estadillo_info", "estadillos_detectar", "estadillos_detectar_start",
    "detect_suffixes", "detect_suffixes_start", "analisis_cancel", "analisis_reset",
    "read_config", "write_config",
    "render_estado", "render_confirmar", "render_set_modo",
    "app_version", "check_update", "download_update", "install_update",
    "start_update_check",
    "cloud_status", "cloud_verify", "cloud_login", "cloud_logout",
    "cloud_pair_start", "cloud_pair_poll",
    "cloud_inspecciones", "cloud_prepare", "cloud_prepare_start", "cloud_upload", "cloud_organizar",
    "cloud_cancel",
    "cloud_comprobar", "cloud_asegurar_estado", "cloud_pendientes", "cloud_drenar",
    "estadillo_validar", "estadillo_subir", "estadillo_existente",
    "run_organize", "run_task",
    "sistema_apagar",
    "red_listar", "red_conectar", "red_conexion",
    "red_ap_estado", "red_ap_activar", "red_ap_desactivar",
    "disco_estado",
    "pin_estado", "pin_fijar", "pin_verificar", "pin_cambiar",
})

# Subconjunto alcanzable por un cliente REMOTO (el movil por el hotspot). El
# token solo existe para que alguien elija la wifi desde un teclado decente: no
# tiene por que abrir `sistema_apagar`, `run_organize` ni el explorador de
# ficheros a quien pase por delante de la pantalla y lea el QR.
METODOS_REMOTOS = frozenset({
    "ping", "red_listar", "red_conectar", "red_ap_estado", "red_ap_desactivar",
})

# Origenes considerados same-origin/local para la validacion de CSRF en
# `do_POST`. El puerto es irrelevante, solo importa el host.
_ORIGENES_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Direcciones IP de origen que se consideran "locales": el Chromium del
# kiosco, que habla con el servidor via loopback. Estas quedan exentas de
# token porque ya corren en la propia maquina (no hay salto de red que
# falsificar).
_IPS_LOOPBACK = {"127.0.0.1", "::1"}

# Ningun metodo de la allowlist sube ficheros por HTTP (`cloud_upload` sube
# al bucket desde disco), asi que un body razonable basta de sobra.
_MAX_BODY = 10 * 1024 * 1024  # 10 MB

# Nombre con el que el hotspot de la Pi se anuncia a los moviles. El dnsmasq
# del AP resuelve *cualquier* dominio a la Pi, asi que las sondas de deteccion
# de portal cautivo (Android, iOS, Windows) caen aqui y se les contesta con un
# 302: el sistema operativo abre entonces la pagina solo, sin teclear la IP.
HOST_PORTAL = "organizer.atom"
_SONDAS_PORTAL_HOST_PROPIO = ("", HOST_PORTAL)


def _origen_permitido(origin: str, host: str = "") -> bool:
    hostname = urlsplit(origin).hostname
    if hostname in _ORIGENES_LOOPBACK:
        return True
    # El movil llega por el hotspot de la propia Pi: su Origin trae la IP/host
    # a la que se conecto, que es justo la que el cliente puso en `Host`. Se
    # acepta ese caso concreto en vez de abrir a cualquier origen.
    host_sin_puerto = (host or "").split(":", 1)[0]
    return bool(host_sin_puerto) and hostname == host_sin_puerto


def _es_ip(host: str) -> bool:
    """Basta con distinguir "10.42.0.1" de "organizer.atom"; no es validacion."""
    return bool(host) and all(c.isdigit() or c == "." for c in host)


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

        def _es_local(self) -> bool:
            return self.client_address[0] in _IPS_LOOPBACK

        def _token_valido(self) -> bool:
            """Autenticacion para clientes no-loopback (p.ej. el movil por el
            hotspot de la Pi). Si el AP no esta activo `_ap_token` esta vacio
            y por tanto NINGUN remoto pasa, aunque mande un token vacio.
            `compare_digest` evita filtrar el token por timing.
            """
            esperado = getattr(api, "_ap_token", "") or ""
            if not esperado:
                return False
            recibido = self.headers.get("X-Atom-Token") or ""
            if not recibido:
                qs = parse_qs(urlsplit(self.path).query)
                recibido = (qs.get("t") or [""])[0]
            return secrets.compare_digest(recibido, esperado)

        def _destino_portal(self, ruta: str) -> str:
            """URL a la que redirigir a un cliente del hotspot, o "" si no toca.

            Dos casos, y solo cuando el AP esta levantado (`_ap_token`):
            - Host ajeno (`connectivitycheck.gstatic.com`, `captive.apple.com`,
              `msftconnecttest.com`...): es una sonda de portal cautivo. Se
              responde 302 a la pagina del Organizer y el movil la abre solo.
            - Host propio pero raiz sin `?t=`: alguien tecleo la direccion a
              mano. Se le devuelve al MISMO host con el token puesto, para no
              depender de que su DNS resuelva `organizer.atom`.
            Nunca se redirigen los assets: romperia la carga del bundle.
            """
            token = getattr(api, "_ap_token", "") or ""
            if not token or self._es_local():
                return ""
            hostport = self.headers.get("Host") or ""
            host = hostport.split(":")[0].lower()
            propio = host in _SONDAS_PORTAL_HOST_PROPIO or _es_ip(host)
            if not propio:
                return f"http://{HOST_PORTAL}/?t={token}"
            if ruta:
                return ""
            if parse_qs(urlsplit(self.path).query).get("t"):
                return ""
            return f"http://{hostport or HOST_PORTAL}/?t={token}"

        def _autenticado(self) -> bool:
            # El Chromium del kiosco habla por loopback: no hay salto de red
            # que un atacante pueda interceptar, asi que no necesita token.
            return self._es_local() or self._token_valido()

        def do_GET(self):
            # Los ficheros estaticos (bundle, css, iconos) se sirven SIN token:
            # el navegador del movil no puede poner cabeceras al pedir un
            # <script src>, y arrastrar el `?t=` a cada asset es imposible.
            # No es un agujero: el bundle es publico por naturaleza y toda
            # accion real pasa por `do_POST`, que si exige token.
            ruta = self.path.split("?", 1)[0].rstrip("/")
            destino = self._destino_portal(ruta)
            if destino:
                self.send_response(302)
                self.send_header("Location", destino)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if ruta == "/events":
                # El SSE si va autenticado: filtra progreso del pipeline y
                # nombres de carpetas. EventSource no admite cabeceras, asi
                # que el token viaja en el query (`/events?t=...`).
                if not self._autenticado():
                    return self.send_error(403)
                return self._sse()
            if ruta in ("", "/index.html"):
                return self._servir_index()
            return super().do_GET()

        def _servir_index(self) -> None:
            """Sirve `index.html` con la marca del modo servidor inyectada.

            La UI necesita saber, YA en el primer render, si es el kiosco de la
            Pi o la app de escritorio, y no se puede deducir del entorno: desde
            pywebview 6 el shell de escritorio tambien sirve el bundle por
            `http://127.0.0.1` (arranca su servidor interno en cuanto la URL es
            local, `webview/__init__.py`), asi que ni el protocolo ni la
            ausencia de `window.pywebview` distinguen los dos casos. La unica
            senal fiable es positiva y la da quien sirve: este servidor, que
            solo corre en modo `--server`.
            """
            try:
                cuerpo = (Path(self.directory) / "index.html").read_bytes()
            except OSError:
                return self.send_error(404)
            marca = b"<script>window.__ATOM_SERVIDOR__ = true</script>"
            # Antes de cualquier <script> del bundle: el modulo `bridge.js` la
            # lee al importarse.
            if b"<head>" in cuerpo:
                cuerpo = cuerpo.replace(b"<head>", b"<head>" + marca, 1)
            else:
                cuerpo = marca + cuerpo
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def end_headers(self):
            # El HTML no se cachea NUNCA. `index.html` es el unico fichero con
            # nombre fijo: si Chromium se lo queda (el kiosco arranca con un
            # perfil persistente), sigue pidiendo el bundle viejo aunque el
            # `dist` ya este actualizado, y un `systemctl restart` no lo
            # arregla — hace falta Ctrl+Shift+R a mano en la Pi. Los assets si
            # se cachean: llevan hash en el nombre, cambiarlos cambia la URL.
            ruta = self.path.split("?", 1)[0]
            # `/events` ya manda su propio `Cache-Control`; no lo dupliques.
            if ruta != "/events" and (
                ruta.endswith("/") or ruta.endswith(".html")
                or "." not in ruta.rsplit("/", 1)[-1]
            ):
                self.send_header("Cache-Control", "no-store, must-revalidate")
            super().end_headers()

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

            # Token: mismo criterio que en do_GET. Va antes que nada porque
            # `sistema_apagar` y compania no deben ni evaluarse sin esto.
            if not self._autenticado():
                return self._json(403, {"ok": False, "error": "no autorizado"})

            # Y aunque el token sea valido, el remoto solo alcanza lo suyo.
            if not self._es_local() and metodo not in METODOS_REMOTOS:
                return self._json(403, {"ok": False, "error": "metodo no disponible en remoto"})

            # CSRF: sin esto, un <form enctype="text/plain"> en cualquier web
            # abierta en el Chromium de la Pi puede llamar a estos metodos sin
            # interaccion del usuario (simple request, sin preflight CORS).
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return self._json(415, {"error": "Content-Type debe ser application/json"})

            origin = self.headers.get("Origin")
            if origin and not _origen_permitido(origin, self.headers.get("Host") or ""):
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
