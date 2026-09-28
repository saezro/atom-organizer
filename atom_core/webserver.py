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
import logging
import mimetypes
import os
import queue
import secrets
import socket
import threading
import time
import traceback
from html import escape as _html_escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

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
    "start_update_check", "get_ultimo_update", "estado_update",
    "cloud_status", "cloud_verify", "cloud_login", "cloud_logout",
    "cloud_pair_start", "cloud_pair_poll",
    "cloud_inspecciones", "cloud_prepare", "cloud_prepare_start", "cloud_upload", "cloud_organizar",
    "cloud_cancel",
    "cloud_comprobar", "cloud_asegurar_estado", "cloud_pendientes", "cloud_drenar",
    "listar_perfiles", "activar_perfil", "borrar_perfil",
    "estadillo_validar", "estadillo_subir", "estadillo_existente", "estadillo_bajar_nube",
    "run_organize", "run_task",
    "sistema_apagar",
    "red_listar", "red_conectar", "red_conexion",
    "red_ap_estado", "red_ap_activar", "red_ap_desactivar",
    "disco_estado",
    "pin_estado", "pin_fijar", "pin_verificar", "pin_cambiar", "pin_telemetria",
    "logs_listar", "logs_leer", "logs_carpeta",
    "sesion_remota",
    "estadillo_espera_iniciar", "estadillo_espera_cancelar", "estadillo_espera_estado",
    "estadillo_espera_carpeta",
    "carpeta_trabajo_fijar",
})

# Rutas HTTP a medida (no pasan por el mecanismo generico `/api/<metodo>` de
# arriba): las consulta/llama la app "Estadillo Digital" de Christian desde
# CUALQUIER IP de la LAN, sin token -asi se decidio: el modo espera
# (`Api.estadillo_espera_*`) es el propio gate, no hay nada que proteger si
# nadie ha iniciado espera-, con CORS abierto porque el Electron de Christian
# llama desde su propio origen. Se gestionan aparte en `do_GET`/`do_POST`/
# `do_OPTIONS`; NADA MAS del servidor se relaja para ellas.
_RUTA_ESTADILLO_ESPERA = "/api/estadillo/espera"
_RUTA_ESTADILLO_RECIBIR = "/api/estadillo"
_RUTA_ESTADILLO_PING = "/api/estadillo/ping"
_RUTAS_LAN_ABIERTAS = frozenset({
    _RUTA_ESTADILLO_ESPERA, _RUTA_ESTADILLO_RECIBIR, _RUTA_ESTADILLO_PING,
})

# Documentacion legible de las 3 rutas anteriores para quien integra el
# "Estadillo Digital" desde un portatil (ver `atom_core/api_docs.py`). Es de
# solo lectura y no toca ningun estado del Organizer: se sirve igual de
# abierta que las rutas que documenta (sin token).
_RUTA_API_DOCS = "/api/docs"

# Limite de body para `POST /api/estadillo`: un estadillo de una jornada son
# unas pocas decenas de filas, JSON razonable son unos pocos KB. 1 MB da
# margen de sobra sin dejar que un cliente ajeno cuelgue el hilo leyendo.
_MAX_BODY_ESTADILLO = 1 * 1024 * 1024  # 1 MB

# Subconjunto alcanzable por un cliente REMOTO (el movil por el hotspot). El
# token solo existe para que alguien elija la wifi desde un teclado decente: no
# tiene por que abrir `sistema_apagar`, `run_organize` ni el explorador de
# ficheros a quien pase por delante de la pantalla y lea el QR.
METODOS_REMOTOS = frozenset({
    "ping", "red_listar", "red_conectar", "red_ap_estado", "red_ap_desactivar",
})

# Rutas HTTP a medida para el "panel de control remoto" del kiosco (movil/
# tablet en la LAN operando el Organizer sin tocar la pantalla de la Pi).
# Igual que las rutas de estadillo (`_RUTAS_LAN_ABIERTAS`), NO pasan por el
# mecanismo generico `/api/<metodo>` ni por el token del AP (`_token_valido`):
# se autentican con el PIN del kiosco por cabecera (`X-Atom-Pin`), verificado
# con la misma logica que usa `Api.pin_verificar` (`pin_kiosco.verificar`).
# El bloqueo por fallos es propio de estas rutas, va por IP y NO comparte
# estado con `ControlIntentos` (el bloqueo escalado del teclado tactil del
# kiosco): que alguien agote intentos por HTTP no debe dejar tambien ciego el
# pad fisico de la Pi.
_RUTA_CONTROL_DISCOS = "/api/control/discos"
_RUTA_CONTROL_CARPETAS = "/api/control/carpetas"
_RUTA_CONTROL_CARPETA = "/api/control/carpeta"
_RUTA_CONTROL_ORGANIZAR = "/api/control/organizar"
_RUTA_CONTROL_ESTADO = "/api/control/estado"
_RUTA_CONTROL_CANCELAR = "/api/control/cancelar"
_RUTA_CONTROL_LOGIN = "/api/control/login"
_RUTAS_CONTROL = frozenset({
    _RUTA_CONTROL_DISCOS, _RUTA_CONTROL_CARPETAS, _RUTA_CONTROL_CARPETA,
    _RUTA_CONTROL_ORGANIZAR, _RUTA_CONTROL_ESTADO, _RUTA_CONTROL_CANCELAR,
    _RUTA_CONTROL_LOGIN,
})

# Cabecera con el PIN del kiosco para las rutas `_RUTAS_CONTROL`.
_CABECERA_CONTROL_PIN = "X-Atom-Pin"

# 5 fallos seguidos desde una IP bloquean esa IP 60 s (fijo, sin escalado: a
# diferencia de `ControlIntentos`, esto protege una API remota, no un pad
# tactil que un humano teclea despacio).
_CONTROL_PIN_FALLOS_MAX = 5
_CONTROL_PIN_BLOQUEO_S = 60.0
_control_pin_lock = threading.Lock()
_control_pin_fallos: dict[str, int] = {}
_control_pin_bloqueada_hasta: dict[str, float] = {}


def _control_pin_espera_segundos(ip: str) -> float:
    with _control_pin_lock:
        hasta = _control_pin_bloqueada_hasta.get(ip, 0.0)
    restante = hasta - time.monotonic()
    return restante if restante > 0 else 0.0


def _control_pin_registrar_fallo(ip: str) -> None:
    with _control_pin_lock:
        n = _control_pin_fallos.get(ip, 0) + 1
        if n >= _CONTROL_PIN_FALLOS_MAX:
            _control_pin_fallos[ip] = 0
            _control_pin_bloqueada_hasta[ip] = time.monotonic() + _CONTROL_PIN_BLOQUEO_S
        else:
            _control_pin_fallos[ip] = n


def _control_pin_registrar_acierto(ip: str) -> None:
    with _control_pin_lock:
        _control_pin_fallos.pop(ip, None)
        _control_pin_bloqueada_hasta.pop(ip, None)


def _control_derivar_destino(carpeta: str) -> str:
    """Replica `derivarDestino` de `KioskScreen.jsx`: misma carpeta con el
    sufijo `_ORGANIZADO`, para que `POST /api/control/organizar` arranque con
    los mismos parametros por defecto que el boton "Organizar" del kiosco."""
    if not carpeta:
        return ""
    sin_barra_final = carpeta.rstrip("/\\")
    if not sin_barra_final:
        return ""
    return f"{sin_barra_final}_ORGANIZADO"


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

# Ruta que expone `estado_lan.py` combinado con datos del propio `api`
# (usuario, wifi): la consume el `fetch` de `_PAGINA_LAN_INFO`, sin token
# -son datos informativos de la máquina, nada que proteger- ni CORS -se
# pide desde la propia página, mismo origen-.
_RUTA_ESTADO_LAN = "/api/estado"

# Pagina que ve un equipo de la LAN al entrar a "/" (o "/red") sin ser el
# kiosco ni el flujo de wifi del hotspot (`_servir_lan_info`). Sin
# dependencias externas (ni build de `webui/`): un único HTML con CSS/JS
# inline. El logo lo sirve el propio directorio estatico del bundle
# (`/atom-logo.svg`, sin token, igual que cualquier otro asset). Los datos
# reales (usuario, wifi, batería, disco...) llegan por `fetch` a
# `_RUTA_ESTADO_LAN`, refrescado cada 5 s. El estado del modo espera del
# estadillo NO sale aquí: eso es solo del kiosco (`EsperaEstadillo.jsx`).
_PAGINA_LAN_INFO = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATOM Organizer</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; min-height: 100vh; display: flex; flex-direction: column;
    align-items: center; gap: 1rem;
    background: #0a0a0a; color: #f5f5f5;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    text-align: center; padding: 2rem; box-sizing: border-box;
  }
  img { width: 8rem; max-width: 40vw; margin-top: 1rem; }
  h1 { margin: 0; font-size: 1.5rem; font-weight: 600; }
  h1 .marca { color: #EE763C; }
  .dato { color: #a3a3a3; font-size: 0.9rem; margin: 0; }
  .tarjetas {
    display: flex; flex-direction: column; gap: 0.75rem;
    width: 100%; max-width: 28rem; margin-top: 0.5rem;
  }
  .tarjeta {
    text-align: left; padding: 1rem 1.25rem; border-radius: 0.75rem;
    border: 1px solid rgba(255, 255, 255, 0.12);
    background: rgba(255, 255, 255, 0.03);
    line-height: 1.5;
  }
  .tarjeta strong {
    display: block; font-size: 0.8rem; color: #a3a3a3;
    text-transform: uppercase; letter-spacing: 0.05rem; margin-bottom: 0.25rem;
  }
  .enlace-red {
    color: #EE763C; text-decoration: none; font-size: 0.9rem;
    border: 1px solid rgba(238, 118, 60, 0.4); border-radius: 0.5rem;
    padding: 0.5rem 1rem; margin: 0.5rem 0 1.5rem;
  }
</style>
</head>
<body>
  <img src="/atom-logo.svg" alt="ATOM Organizer">
  <h1><span class="marca">ATOM</span> ORGANIZER</h1>
  <p class="dato">v__VERSION__</p>
  <div class="tarjetas">
    <div class="tarjeta"><strong>Raspberry</strong><span id="raspberry">__HOSTNAME__</span></div>
    <div class="tarjeta"><strong>Usuario</strong><span id="usuario">&mdash;</span></div>
    <div class="tarjeta"><strong>Wifi</strong><span id="wifi">&mdash;</span></div>
    <div class="tarjeta"><strong>Bater&iacute;a</strong><span id="bateria">&mdash;</span></div>
    <div class="tarjeta"><strong>Disco</strong><span id="disco">&mdash;</span></div>
  </div>
  <a class="enlace-red" href="/red">Configuraci&oacute;n de red</a>
  <script>
    function texto(id, val) { document.getElementById(id).textContent = val; }
    async function actualizar() {
      try {
        var res = await fetch('__RUTA_ESTADO__', { cache: 'no-store' });
        var d = await res.json();
        texto('raspberry', (d.serial || '\\u2014') + ' \\u00b7 ' + (d.hostname || '\\u2014'));
        texto('usuario', d.usuario || '\\u2014');

        var wifi = d.wifi || {};
        var ips = (d.ips || []).map(function (i) { return i.ip; }).join(', ');
        var etiquetaWifi = wifi.tipo === 'wifi' ? (wifi.ssid || 'Wifi')
          : wifi.tipo === 'cable' ? 'Cable'
          : 'Sin conexi\\u00f3n';
        texto('wifi', etiquetaWifi + (ips ? ' \\u00b7 ' + ips : ''));

        if (d.bateria) {
          texto('bateria', d.bateria.porcentaje + '% \\u00b7 ' + (d.bateria.estado || ''));
        } else {
          texto('bateria', 'sin bater\\u00eda');
        }

        var sistema = d.disco_sistema
          ? 'Sistema: ' + d.disco_sistema.libre_gb + ' / ' + d.disco_sistema.total_gb + ' GB libres'
          : 'Sistema: \\u2014';
        var externos = d.discos_externos || [];
        var textoExternos = externos.length
          ? externos.map(function (e) {
              return e.nombre + ' (' + e.punto_montaje + '): ' + e.libre_gb + ' / ' + e.total_gb + ' GB';
            }).join('<br>')
          : 'Sin discos conectados';
        document.getElementById('disco').innerHTML = sistema + '<br>' + textoExternos;
      } catch (e) {
        texto('usuario', 'Sin conexi\\u00f3n con el Organizer.');
      }
    }
    actualizar();
    setInterval(actualizar, 5000);
  </script>
</body>
</html>
"""


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


def _estado_lan(api) -> dict:
    """Payload de `GET /api/estado`: datos reales de la máquina para quien
    entra a la Pi desde la LAN (ver `_PAGINA_LAN_INFO`). Cada campo va en su
    propio try/except -tolerante a fallos, nunca debe tumbar la respuesta-;
    ninguno hace subprocess lento (los que lo necesitan, como `red_conexion`,
    ya llevan su propio timeout corto)."""
    from atom_core import estado_lan, red_info, updater

    def _try(fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — informativo, nunca debe romper /api/estado
            return default

    version = _try(updater.current_version, "?")
    hostname = _try(red_info.hostname, "")
    serial = _try(estado_lan.serial_raspberry, "")
    usuario = _try(lambda: api._cuenta_actual(), None)
    conexion = _try(lambda: api.red_conexion(), {}) or {}
    if not isinstance(conexion, dict):
        conexion = {}
    ips = _try(red_info.ips_locales, [])

    return {
        "version": version,
        "hostname": hostname,
        "serial": serial,
        "usuario": usuario,
        "wifi": {
            "tipo": conexion.get("tipo") or "",
            "ssid": conexion.get("ssid") or "",
            "ip": conexion.get("ip") or "",
        },
        "ips": ips,
        "bateria": _try(estado_lan.bateria, None),
        "disco_sistema": _try(estado_lan.disco_sistema, None),
        "discos_externos": _try(estado_lan.discos_externos, []),
    }


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

        def _json_cors(self, code: int, payload: dict) -> None:
            """Como `_json`, pero con `Access-Control-Allow-Origin: *` para las
            rutas LAN abiertas (`_RUTAS_LAN_ABIERTAS`). Nunca usar fuera de
            ellas: es la unica respuesta de este servidor visible desde
            cualquier origen."""
            cuerpo = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _estadillo_espera_get(self) -> None:
            api._estadillo_registrar_evento(self.client_address[0], "consulta")
            estado = api.estadillo_espera_estado()
            # `carpeta_seleccionada`/`carpeta`: si el kiosco no tiene carpeta
            # elegida (o se borro del disco tras iniciar la espera), la app
            # de Christian lo ve aqui en vez de enterarse solo al mandar el
            # POST y que lo rechacen.
            if not estado.get("esperando"):
                caducado = bool(estado.get("caducado"))
                codigo = "espera_caducada" if caducado else "sin_espera_activa"
                motivo = (
                    "La espera de estadillo caducó. Inicia una espera nueva desde el kiosco."
                    if caducado else
                    "El kiosco no tiene ninguna espera de estadillo activa. Inícala desde el kiosco antes de enviar."
                )
                logger.warning("GET /api/estadillo/espera rechazado: codigo=%s ip=%s", codigo, self.client_address[0])
                return self._json_cors(409, {
                    "ok": False,
                    "codigo": codigo,
                    "motivo": motivo,
                    "esperando": False,
                    "caducado": caducado,
                    "carpeta_seleccionada": bool(estado.get("carpeta_seleccionada")),
                    "carpeta": estado.get("carpeta"),
                    "estadillo_en_carpeta": estado.get("estadillo_en_carpeta"),
                })
            payload = {
                "esperando": True,
                "caducado": False,
                "inspeccion": estado.get("inspeccion"),
                "fotos": estado.get("fotos"),
                "recibido": estado.get("recibido"),
                "caduca_en": estado.get("caduca_en"),
                "segundos_restantes": estado.get("segundos_restantes"),
                "red": estado.get("red"),
                "carpeta_seleccionada": bool(estado.get("carpeta_seleccionada")),
                "carpeta": estado.get("carpeta"),
                "estadillo_en_carpeta": estado.get("estadillo_en_carpeta"),
            }
            if estado.get("aviso"):
                payload["aviso"] = estado["aviso"]
            return self._json_cors(200, payload)

        def _estadillo_ping_get(self) -> None:
            """`{organizer:true, version, esperando}`, SIEMPRE 200: para que la
            app de Christian pueda escanear la subred (probar IP a IP, puerto
            80/8765) y reconocer cual es la Pi sin importar el modo espera."""
            from atom_core import updater

            api._estadillo_registrar_evento(self.client_address[0], "ping")
            try:
                version = updater.current_version()
            except Exception:  # noqa: BLE001 — informativo, nunca debe romper el ping
                version = "?"
            estado = api.estadillo_espera_estado()
            return self._json_cors(200, {
                "organizer": True,
                "version": version,
                "esperando": bool(estado.get("esperando")),
            })

        def _estadillo_recibir_post(self) -> None:
            ip = self.client_address[0]
            api._estadillo_registrar_evento(ip, "envio")

            def rechazar(http_code: int, codigo: str, motivo: str, payload: dict) -> None:
                respuesta = {"ok": False, **payload, "codigo": codigo, "motivo": motivo}
                api._estadillo_registrar_evento(ip, "rechazado", f"{motivo} ({codigo})")
                logger.warning("POST /api/estadillo rechazado: codigo=%s http=%s ip=%s", codigo, http_code, ip)
                return self._json_cors(http_code, respuesta)

            estado = api.estadillo_espera_estado()
            if not estado.get("esperando"):
                caducado = bool(estado.get("caducado"))
                codigo = "espera_caducada" if caducado else "sin_espera_activa"
                motivo = (
                    "La espera de estadillo caducó. Inicia una espera nueva desde el kiosco."
                    if caducado else
                    "El kiosco no tiene ninguna espera de estadillo activa. Inícala desde el kiosco antes de enviar."
                )
                return rechazar(409, codigo, motivo, {
                    "esperando": False,
                    "caducado": caducado,
                })
            if estado.get("recibido") and estado.get("segundos_restantes", 1) > 0:
                # Ya se recibio un estadillo valido para esta espera Y SIGUE
                # VIGENTE (no ha pasado `caduca_en`): el kiosco tiene que
                # reiniciarla (`estadillo_espera_iniciar`) antes de aceptar
                # otro POST. Si ya caduco, se trata como espera limpia (bug
                # 2026-09-23: un `recibido: True` fantasma bloqueaba con
                # este 409 para siempre, requeria reiniciar organizer-server).
                return rechazar(409, "estadillo_ya_recibido",
                                 "Ya se recibió el estadillo para esta espera; reinicia la espera desde el kiosco.", {
                    "esperando": True, "recibido": True,
                    "errores": ["Ya se recibió el estadillo; reinicia la espera desde el kiosco."],
                })
            # El estadillo se recibe SIEMPRE, aunque el kiosco aun no tenga
            # carpeta elegida: se guarda en `estadillos_recibidos_dir()` (ver
            # `_estadillo_recibir`) igual que en el flujo normal, y queda
            # asociado a esta espera. `estadillo_espera_carpeta` (llamado por
            # el kiosco al elegir carpeta) actualiza `estado["carpeta"]` a
            # posteriori, y `_mover_estadillo_espera_si_toca` lo mueve a esa
            # carpeta cuando arranca el procesado. Antes rechazabamos con 409
            # `sin_carpeta_seleccionada` y el estadillo se perdia.
            pendiente_carpeta = not estado.get("carpeta_seleccionada")

            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return rechazar(415, "content_type_invalido",
                                 "El Content-Type debe ser application/json.",
                                 {"errores": ["Content-Type debe ser application/json"]})

            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return rechazar(400, "content_length_invalido", "El encabezado Content-Length no es válido.",
                                 {"errores": ["Content-Length invalido"]})
            if largo < 0:
                return rechazar(400, "content_length_invalido", "El encabezado Content-Length no es válido.",
                                 {"errores": ["Content-Length invalido"]})
            if largo > _MAX_BODY_ESTADILLO:
                return rechazar(413, "cuerpo_demasiado_grande", "El cuerpo del estadillo supera el tamaño máximo permitido.",
                                 {"errores": ["cuerpo demasiado grande"]})

            try:
                cuerpo = json.loads(self.rfile.read(largo) or b"{}")
            except Exception:  # noqa: BLE001 — JSON malformado del cliente
                return rechazar(400, "json_invalido", "El cuerpo enviado no es JSON válido.",
                                 {"errores": ["JSON invalido"]})

            vuelos = cuerpo.get("vuelos") if isinstance(cuerpo, dict) else None
            if not isinstance(vuelos, list) or not vuelos:
                return rechazar(422, "faltan_vuelos", "Falta la lista 'vuelos' (no puede estar vacía).",
                                 {"errores": ["falta 'vuelos' (lista no vacia)"]})

            try:
                resultado = api._estadillo_recibir(vuelos)
            except Exception:  # noqa: BLE001 — no filtrar detalles internos al cliente
                traceback.print_exc()
                return rechazar(500, "error_interno", "Error interno del Organizer al procesar el estadillo.",
                                 {"errores": ["error interno al procesar el estadillo"]})

            if resultado.get("ok"):
                n = (resultado.get("resumen") or {}).get("n_vuelos")
                api._estadillo_registrar_evento(ip, "aceptado", f"{n} vuelos" if n is not None else "")
                if pendiente_carpeta:
                    resultado = {**resultado, "pendiente_carpeta": True}
                return self._json_cors(200, resultado)
            motivo = "; ".join(resultado.get("errores") or []) or "El estadillo no pasó la validación."
            respuesta = {"codigo": "validacion_estadillo", "motivo": motivo, **resultado}
            api._estadillo_registrar_evento(ip, "rechazado", f"{motivo} (validacion_estadillo)")
            logger.warning("POST /api/estadillo rechazado: codigo=validacion_estadillo http=422 ip=%s", ip)
            return self._json_cors(422, respuesta)

        def _api_docs_get(self) -> None:
            """`GET /api/docs`: HTML autocontenido con la doc de las 3 rutas
            LAN abiertas. `?format=json` devuelve la misma info estructurada
            (ver `atom_core.api_docs.especificacion`, unica fuente para
            ambos formatos, para que no se desincronicen)."""
            from atom_core import api_docs

            spec = api_docs.especificacion()
            qs = parse_qs(urlsplit(self.path).query)
            formato = (qs.get("format") or [""])[0].strip().lower()
            if formato == "json":
                return self._json(200, spec)
            cuerpo = api_docs.render_html(spec).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _control_autenticado(self):
            """PIN de `_CABECERA_CONTROL_PIN` para las rutas `_RUTAS_CONTROL`.

            Devuelve `(ok, codigo, http_code)`. `codigo` es el slug estable
            del contrato (`pin_no_configurado`/`pin_bloqueado`/`pin_invalido`)
            para responder siempre `{"ok": False, "codigo": ...}`.
            """
            from atom_core import pin_kiosco

            ip = self.client_address[0]
            try:
                hay_pin = pin_kiosco.hay_pin(api._store_pin())
            except Exception:  # noqa: BLE001 — un store roto no debe autorizar nada
                hay_pin = False
            if not hay_pin:
                return False, "pin_no_configurado", 403
            if _control_pin_espera_segundos(ip) > 0:
                return False, "pin_bloqueado", 429
            pin = self.headers.get(_CABECERA_CONTROL_PIN) or ""
            try:
                correcto = bool(pin) and pin_kiosco.verificar(api._store_pin(), pin)
            except Exception:  # noqa: BLE001 — PIN invalido no debe tumbar la ruta
                correcto = False
            if not correcto:
                _control_pin_registrar_fallo(ip)
                return False, "pin_invalido", 401
            _control_pin_registrar_acierto(ip)
            return True, "", 200

        def _control_get(self, ruta: str) -> None:
            ok, codigo, http_code = self._control_autenticado()
            if not ok:
                return self._json_cors(http_code, {"ok": False, "codigo": codigo})
            # A diferencia de las rutas POST de control (login/carpeta/
            # organizar/cancelar), estas GET no emiten su propio
            # `atom:control_ui` mas abajo: se marca aqui, una vez, para las
            # tres (discos/carpetas/estado).
            self._marcar_actividad_remota()
            if ruta == _RUTA_CONTROL_DISCOS:
                return self._control_discos_get()
            if ruta == _RUTA_CONTROL_CARPETAS:
                return self._control_carpetas_get()
            if ruta == _RUTA_CONTROL_ESTADO:
                return self._control_estado_get()
            return self._json_cors(404, {"ok": False, "error": "ruta desconocida"})

        def _control_discos_get(self) -> None:
            """Nivel superior del confinamiento: los discos externos montados,
            mismo listado que ve el kiosco al abrir el selector sin carpeta
            (`Api.list_dir(None)`)."""
            try:
                resultado = api.list_dir(None)
            except Exception:  # noqa: BLE001 — no filtrar detalles internos
                traceback.print_exc()
                return self._json_cors(500, {"ok": False, "error": "error interno"})
            discos = (resultado or {}).get("dirs") or []
            return self._json_cors(200, {"discos": discos})

        def _control_carpetas_get(self) -> None:
            qs = parse_qs(urlsplit(self.path).query)
            path = (qs.get("path") or [""])[0] or None
            try:
                resultado = api._list_dir_confinado_pi(path)
            except Exception:  # noqa: BLE001 — no filtrar detalles internos
                traceback.print_exc()
                return self._json_cors(500, {"ok": False, "error": "error interno"})
            if not resultado.get("ok"):
                return self._json_cors(400, {
                    "ok": False, "codigo": "path_no_permitido",
                    "error": resultado.get("error"),
                })
            return self._json_cors(200, resultado)

        def _control_estado_get(self) -> None:
            carpeta = api._carpeta_trabajo_actual()
            estadillo = None
            if carpeta:
                try:
                    estadillo = api._estadillo_en_carpeta(carpeta)
                except Exception:  # noqa: BLE001 — informativo, no debe romper /estado
                    estadillo = None
            return self._json_cors(200, {
                "carpeta": carpeta,
                "estadillo": estadillo,
                "en_curso": bool(getattr(api, "_running", False)),
                "fase": getattr(api, "_control_fase", None),
                "progreso": getattr(api, "_control_progreso", None),
                "ultimo_error": getattr(api, "_control_ultimo_error", None),
            })

        def _control_post(self, ruta: str) -> None:
            ok, codigo, http_code = self._control_autenticado()
            if not ok:
                return self._json_cors(http_code, {"ok": False, "codigo": codigo})

            cuerpo: dict = {}
            if ruta == _RUTA_CONTROL_CARPETA:
                content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    return self._json_cors(415, {"ok": False, "codigo": "content_type_invalido"})
                try:
                    largo = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    largo = -1
                if largo < 0:
                    return self._json_cors(400, {"ok": False, "codigo": "content_length_invalido"})
                try:
                    cuerpo = json.loads(self.rfile.read(largo) or b"{}")
                except Exception:  # noqa: BLE001 — JSON malformado del cliente
                    return self._json_cors(400, {"ok": False, "codigo": "json_invalido"})
                if not isinstance(cuerpo, dict):
                    cuerpo = {}

            if ruta == _RUTA_CONTROL_CARPETA:
                return self._control_carpeta_post(cuerpo)
            if ruta == _RUTA_CONTROL_ORGANIZAR:
                return self._control_organizar_post()
            if ruta == _RUTA_CONTROL_CANCELAR:
                return self._control_cancelar_post()
            if ruta == _RUTA_CONTROL_LOGIN:
                return self._control_login_post()
            return self._json_cors(404, {"ok": False, "error": "ruta desconocida"})

        def _control_login_post(self) -> None:
            """La autenticacion (PIN, rate-limit) ya la resolvio
            `_control_autenticado()` en `_control_post` antes de llegar aqui:
            si estamos en este metodo es que el PIN era correcto. Solo queda
            avisar a la UI del kiosco de que alguien acaba de entrar, sin
            filtrar nunca el PIN ni sus digitos en el evento."""
            sink.dispatch("atom:control_ui", {"accion": "login"})
            return self._json_cors(200, {"ok": True})

        def _control_carpeta_post(self, cuerpo: dict) -> None:
            path = cuerpo.get("path")
            try:
                resultado = api._list_dir_confinado_pi(path) if path else {"ok": False, "error": "Falta 'path'."}
            except Exception:  # noqa: BLE001 — no filtrar detalles internos
                traceback.print_exc()
                return self._json_cors(500, {"ok": False, "error": "error interno"})
            if not resultado.get("ok"):
                return self._json_cors(400, {
                    "ok": False, "codigo": "path_no_permitido",
                    "error": resultado.get("error"),
                })
            # `carpeta_trabajo_fijar` fija el estado unico de `Api` (lo ven
            # tambien `/api/control/estado` y `/api/control/organizar`) y, si
            # hay espera o estadillo pendiente, la entera de la nueva carpeta
            # (no reinicia la caducidad); no-op si no hay espera.
            api.carpeta_trabajo_fijar(path)
            # La UI del kiosco (React) se entera por SSE, igual que progreso/
            # nube: mismo transporte que `Api._sink` (`atom:progress`, ...).
            sink.dispatch("atom:control_carpeta", {"path": path})
            sink.dispatch("atom:control_ui", {"accion": "carpeta", "path": path})
            return self._json_cors(200, {"ok": True, "path": path})

        def _control_organizar_post(self) -> None:
            carpeta = api._carpeta_trabajo_actual()
            if not carpeta:
                return self._json_cors(409, {"ok": False, "codigo": "sin_carpeta"})
            # Mismo estadillo que detectaria el kiosco en la carpeta
            # (`estadillos_detectar`, lo que rellena `kioskEstadillo` en
            # `App.jsx`); si aun no hay nada en la carpeta pero SI llego un
            # estadillo por LAN pendiente de mover (`estadillo_espera_estado`,
            # `recibido=True` y la misma carpeta), se incluye su ruta para
            # que `_mover_estadillo_espera_si_toca` (app_webview.py) lo
            # encuentre y lo mueva al arrancar el run. Empaquetado con
            # `empaquetar_rutas`: el pipeline sigue esperando el estadillo
            # como UN string (ver `atom_core/estadillo.py`), igual que hace
            # `App.jsx` antes de llamar a `runTask`.
            try:
                rutas = list((api.estadillos_detectar(carpeta) or {}).get("rutas") or [])
            except Exception:  # noqa: BLE001 — informativo, no debe bloquear organizar
                traceback.print_exc()
                rutas = []
            if not rutas:
                try:
                    espera = api.estadillo_espera_estado()
                except Exception:  # noqa: BLE001 — informativo, no debe bloquear organizar
                    espera = {}
                if (espera.get("recibido") and espera.get("carpeta")
                        and os.path.normpath(espera["carpeta"]) == os.path.normpath(carpeta)):
                    rutas = list(espera.get("rutas") or [])
            from atom_core.estadillo import empaquetar_rutas
            params = {
                "origen": carpeta,
                "destino": _control_derivar_destino(carpeta),
                "estadillo": empaquetar_rutas(rutas),
                "rename": True,
            }
            try:
                resultado = api.run_organize(params, None)
            except Exception:  # noqa: BLE001 — no filtrar detalles internos
                traceback.print_exc()
                return self._json_cors(500, {"ok": False, "error": "error interno"})
            if not resultado.get("started"):
                return self._json_cors(409, {"ok": False, "codigo": "en_curso"})
            sink.dispatch("atom:control_ui", {"accion": "organizar"})
            return self._json_cors(200, {"ok": True, "started": True})

        def _control_cancelar_post(self) -> None:
            try:
                resultado = api.analisis_cancel()
            except Exception:  # noqa: BLE001 — no filtrar detalles internos
                traceback.print_exc()
                return self._json_cors(500, {"ok": False, "error": "error interno"})
            sink.dispatch("atom:control_ui", {"accion": "cancelar"})
            return self._json_cors(200, resultado)

        def _es_local(self) -> bool:
            return self.client_address[0] in _IPS_LOOPBACK

        def _marcar_actividad_remota(self) -> None:
            """Enciende el marco azul del kiosco (`App.jsx`
            `encenderControlRemoto`, evento `atom:control_ui`): CUALQUIER
            peticion a `/api/*` de un cliente NO loopback cuenta como "la Pi
            se esta usando por API desde fuera" (portal de estadillo de
            Christian, panel de control del portatil, RPC generico via token
            de AP...). El frontend no distingue el detalle, solo reenciende
            el marco.
            """
            sink.dispatch("atom:control_ui", {"accion": "api_remota"})

        def _marcar_si_api_remota(self, ruta: str) -> None:
            """Punto unico para `do_GET`/`do_POST`: cualquier ruta `/api/*`
            pedida por un cliente NO loopback marca actividad remota, salvo
            `_RUTAS_CONTROL` (`_control_get`/`_control_post` ya emiten su
            propio evento mas especifico -login/carpeta/organizar/cancelar, o
            el generico de las GET- y marcar aqui tambien duplicaria el
            evento). Cubre, entre otras, las rutas LAN abiertas de estadillo
            (`_RUTA_ESTADILLO_RECIBIR`/`_RUTA_ESTADILLO_ESPERA`/
            `_RUTA_ESTADILLO_PING`, sin token), que no pasan por el RPC
            generico ni por `/api/control/*`.
            """
            if ruta in _RUTAS_CONTROL or self._es_local():
                return
            self._marcar_actividad_remota()

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

        def do_OPTIONS(self):
            # Preflight CORS. Para las rutas LAN abiertas y las de control
            # remoto (`_RUTAS_CONTROL`, que ademas mandan `X-Atom-Pin`): el
            # resto del servidor no necesita preflight (loopback/hotspot no
            # lo mandan).
            ruta = self.path.split("?", 1)[0].rstrip("/")
            if ruta not in _RUTAS_LAN_ABIERTAS and ruta not in _RUTAS_CONTROL:
                return self.send_error(404)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {_CABECERA_CONTROL_PIN}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            # Rutas LAN abiertas (ver `_RUTAS_LAN_ABIERTAS`): antes que nada
            # (portal cautivo, token...), no son parte del kiosco ni del AP.
            ruta_lan = self.path.split("?", 1)[0].rstrip("/")
            # Punto unico de marcado (ver `_marcar_si_api_remota`): cualquier
            # `/api/*` que llegue de fuera enciende el marco azul, antes de
            # decidir a que handler concreto va.
            if ruta_lan.startswith("/api/"):
                self._marcar_si_api_remota(ruta_lan)
            if ruta_lan == _RUTA_ESTADILLO_ESPERA:
                return self._estadillo_espera_get()
            if ruta_lan == _RUTA_ESTADILLO_PING:
                return self._estadillo_ping_get()
            if ruta_lan == _RUTA_ESTADO_LAN:
                return self._json(200, _estado_lan(api))
            if ruta_lan == _RUTA_API_DOCS:
                return self._api_docs_get()
            # Rutas de control remoto (ver `_RUTAS_CONTROL`): PIN por cabecera,
            # no token/loopback.
            if ruta_lan in _RUTAS_CONTROL:
                return self._control_get(ruta_lan)
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
            if ruta in ("", "/index.html", "/red"):
                # Loopback (kiosco) y el flujo de configuracion wifi del
                # hotspot (token valido por `?t=`) siguen viendo la UI React
                # de siempre. Cualquier OTRO cliente de la LAN (alguien que
                # teclea la IP/host de la Pi desde su propio equipo) recibe
                # una pagina informativa minima en vez de la UI completa: no
                # tiene el contexto (carpetas, sesion) para que tenga sentido.
                if not self._autenticado():
                    return self._servir_lan_info()
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

        def _servir_lan_info(self) -> None:
            """Pagina informativa minima para quien entra a la Pi desde la LAN
            sin ser el kiosco (loopback) ni el flujo de configuracion wifi del
            hotspot (`?t=<token>` valido, ver `_autenticado`). NO es la UI
            completa -esa la sigue sirviendo `_servir_index`-: solo dice que
            la maquina es el Organizer y el estado del modo "esperando
            estadillo" (refrescado con `fetch` a `_RUTA_ESTADILLO_ESPERA`), que
            es lo unico que le interesa a alguien mirando desde otro equipo.
            """
            from atom_core import updater

            try:
                version = updater.current_version()
            except Exception:  # noqa: BLE001 — informativo, nunca debe romper la pagina
                version = "?"
            try:
                host = socket.gethostname()
            except Exception:  # noqa: BLE001
                host = ""

            cuerpo = (
                _PAGINA_LAN_INFO
                .replace("__VERSION__", _html_escape(version))
                .replace("__HOSTNAME__", _html_escape(host))
                .replace("__RUTA_ESTADO__", _RUTA_ESTADO_LAN)
            ).encode("utf-8")
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
            # `getattr` con default: una peticion malformada (HTTP/0.9, un
            # cliente que manda basura antes de la linea de peticion) puede
            # dejar `self.path` sin fijar -`BaseHTTPRequestHandler.handle_one_request`
            # no lo asigna si `parse_request` falla- y usarlo a pelo tumbaria
            # el hilo con `AttributeError` en vez de dejar que el propio
            # servidor responda el error que ya intenta mandar.
            ruta = getattr(self, "path", "").split("?", 1)[0]
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
            ruta_sin_barra = ruta.rstrip("/")
            # Punto unico de marcado (ver `_marcar_si_api_remota`): cualquier
            # `/api/*` que llegue de fuera enciende el marco azul, antes de
            # decidir a que handler concreto va (incluida `/api/estadillo`,
            # que es LAN abierta y no pasa por token/CSRF).
            if ruta_sin_barra.startswith("/api/"):
                self._marcar_si_api_remota(ruta_sin_barra)
            # Ruta LAN abierta (ver `_RUTAS_LAN_ABIERTAS`): antes de token/CSRF,
            # que no aplican aqui a proposito.
            if ruta.rstrip("/") == _RUTA_ESTADILLO_RECIBIR:
                return self._estadillo_recibir_post()
            # Rutas de control remoto (ver `_RUTAS_CONTROL`): PIN por
            # cabecera, antes de token/CSRF, que no aplican aqui a proposito
            # (mismo criterio que las rutas de estadillo).
            if ruta.rstrip("/") in _RUTAS_CONTROL:
                return self._control_post(ruta.rstrip("/"))
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
