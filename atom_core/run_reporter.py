"""Telemetría de una subida hacia ATOM Suite (`/organizer`).

`RunReporter` avisa a la API de la Suite de que hay una subida en curso, le
manda su progreso a intervalos y le dice cómo terminó, para que la página
`/organizer` pinte la subida en vivo y quede en el histórico.

**Regla número uno, por encima de todo lo demás: fail-open.** Este módulo no
puede romper ni ralentizar una subida de minutos u horas bajo ninguna
circunstancia. Sin red, con la Suite caída, con el token caducado, con un 500
del servidor, con un JSON corrupto: se traga la excepción, deja un aviso en el
log y sigue subiendo. Por eso todo método público está envuelto en
`try/except Exception` y nunca deja escapar nada; y por eso el timeout es
corto y duro (5 s por defecto): un servidor que no contesta no puede colgar un
hilo de subida.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from . import upload_log
from .inspecciones import API_BASE as _API_BASE

__all__ = ["RunReporter"]

_log = logging.getLogger(upload_log.LOGGER_NAME)

USER_AGENT = "ATOM-Organizer-Uploader"
RUTA_RUNS = "/api/organizer/runs"
TAM_LOTE_LOGS = 200


class RunReporter:
    """Reporta el ciclo de vida de una subida (`iniciar` → `progreso*` → `fin`).

    Si `iniciar` no consigue crear el run en la Suite (red caída, 500, token
    caducado...), `self._id` queda `None` y el resto de métodos pasan a ser
    no-op silenciosos: no hay reintentos en bucle ni excepciones, la subida
    sigue exactamente igual que si `RunReporter` no existiera.
    """

    def __init__(self, auth=None, *, secreto: str | None = None,
                 tipo: str = "subida", base: str | None = None,
                 timeout: int = 5, intervalo_latido: float = 10.0) -> None:
        # Dos credenciales posibles, porque hay dos clientes distintos:
        #
        # - `auth` (app de escritorio): el operador ya ha hecho login con Google,
        #   así que manda su `id_token` y el servidor sabe QUIÉN sube sin fiarse
        #   de un `usuario_id` en el body.
        # - `secreto` (Cloud Run Job): un contenedor no tiene usuario. Va con el
        #   secreto compartido y el run queda con `usuario_id` NULL.
        #
        # Nunca los dos a la vez: si hay `auth`, manda `auth`.
        self._auth = auth
        self._secreto = secreto
        self._tipo = tipo
        self._base = (base or _API_BASE).rstrip("/")
        self._timeout = timeout
        self._intervalo_latido = intervalo_latido

        # Estado mutable compartido entre el hilo que llama a `iniciar`/`fin`
        # y los hilos de subida que llaman a `progreso` vía `on_stats`.
        self._lock = threading.Lock()
        self._id: int | None = None
        self._ultimo_latido = 0.0
        self._buffer_logs: list[dict] = []
        self._seq_log = 0

    # -- utilidad interna ---------------------------------------------------
    def _peticion(self, metodo: str, ruta: str, cuerpo: dict) -> dict | None:
        """POST/PATCH a la API de la Suite. `None` si algo falla, nunca lanza.

        Centraliza urllib + headers + timeout + el try/except para que
        `iniciar`/`progreso`/`fin` no repitan la fontanería, siguiendo el
        mismo patrón que `descargar_catalogo_api` en `inspecciones.py`.
        """
        try:
            url = f"{self._base}{ruta}"
            datos = json.dumps(cuerpo).encode("utf-8")
            req = urllib.request.Request(url, data=datos, method=metodo)
            # El middleware de la Suite mira primero `Authorization`: si viene un
            # Bearer inválido responde 401 y NO cae al secreto, así que mandar
            # los dos headers a la vez convertiría un token caducado en un fallo
            # duro. Por eso es o uno o el otro, nunca ambos.
            if self._auth is not None:
                req.add_header("Authorization", f"Bearer {self._auth.id_token()}")
            elif self._secreto:
                req.add_header("x-organizer-secret", self._secreto)
            req.add_header("User-Agent", USER_AGENT)
            req.add_header("Content-Type", "application/json")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001 - fail-open: nunca puede romper la subida
            _log.debug("run_reporter: %s %s falló (%s)", metodo, ruta, exc)
            return None

    # -- ciclo de vida --------------------------------------------------------
    def iniciar(self, *, inspeccion: str | None = None, etapa: str | None = None,
                items_total: int | None = None, bytes_total: int | None = None,
                **extra) -> None:
        """Crea el run en la Suite. Si falla, todo lo demás queda en no-op."""
        try:
            cuerpo: dict = {"tipo": self._tipo}
            if inspeccion is not None:
                cuerpo["inspeccion"] = inspeccion
            if etapa is not None:
                cuerpo["etapa"] = etapa
            if items_total is not None:
                cuerpo["items_total"] = items_total
            if bytes_total is not None:
                cuerpo["bytes_total"] = bytes_total
            cuerpo.update(extra)

            resp = self._peticion("POST", RUTA_RUNS, cuerpo)
            run_id = (resp or {}).get("id") if isinstance(resp, dict) else None
            with self._lock:
                self._id = run_id if isinstance(run_id, int) else None
                self._ultimo_latido = 0.0
                self._seq_log = 0
                self._buffer_logs = []
            if self._id is None:
                _log.warning("run_reporter: no se pudo crear el run en la Suite; "
                             "se sigue subiendo sin telemetría")
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.iniciar: excepción inesperada (%s)", exc)
            with self._lock:
                self._id = None

    def progreso(self, stats: dict) -> None:
        """Manda un PATCH de progreso, con throttle salvo en la foto final.

        `stats` es el dict que emite `_stats` en `cloud_upload.py` (mismas
        claves que recibe `on_stats`). Se llama desde los hilos de subida, así
        que decidir si toca mandar el latido va dentro del lock; la petición
        HTTP en sí queda fuera para no serializar a los hilos de subida.
        """
        try:
            with self._lock:
                run_id = self._id
                if run_id is None:
                    return
                es_final = bool(stats.get("final"))
                ahora = time.monotonic()
                if not es_final and (ahora - self._ultimo_latido) < self._intervalo_latido:
                    return
                self._ultimo_latido = ahora

            cuerpo: dict = {}
            if "files_done" in stats:
                cuerpo["items_hechos"] = stats["files_done"]
            if "files_total" in stats:
                cuerpo["items_total"] = stats["files_total"]
            if "bytes_done" in stats:
                cuerpo["bytes_hechos"] = stats["bytes_done"]
            if "bytes_total" in stats:
                cuerpo["bytes_total"] = stats["bytes_total"]
            eta = stats.get("eta")
            if eta is not None:
                cuerpo["eta_segundos"] = int(round(eta))

            if not cuerpo:
                return
            if es_final:
                # La foto final SÍ va síncrona: ya no hay subida a la que frenar,
                # y mandarla en hilo aparte la haría competir con el `fin()` que
                # viene justo después (llegaría un latido tras el cierre).
                self._peticion("PATCH", f"{RUTA_RUNS}/{run_id}", cuerpo)
                return
            # El resto, EN HILO APARTE, a propósito. `progreso` se invoca desde un hilo de
            # subida: si el PATCH fuese síncrono, una Suite lenta bloquearía ese
            # hilo hasta `timeout` segundos en cada latido — telemetría
            # ralentizando la subida, justo lo que no puede pasar. El throttle
            # (1 cada `intervalo_latido`) y el timeout corto garantizan que no se
            # acumulen hilos: como mucho hay uno vivo a la vez.
            # `daemon=True` para que un latido en vuelo no impida cerrar la app.
            threading.Thread(
                target=self._peticion,
                args=("PATCH", f"{RUTA_RUNS}/{run_id}", cuerpo),
                name="run-reporter-latido",
                daemon=True,
            ).start()
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.progreso: excepción inesperada (%s)", exc)

    def fase(self, *, index: int, total: int, nombre: str,
             eta_segundos: int | None = None) -> None:
        """Marca un cambio de fase del pipeline (`organize_cli.py`).

        A diferencia de `progreso`, no hay throttle: los cambios de fase son
        pocos (una decena por run), cada uno es información valiosa (fase
        entera cerrada, no un latido intermedio) y no compiten entre sí por
        recursos como sí lo hacen los hilos de subida.
        """
        try:
            with self._lock:
                run_id = self._id
                if run_id is None:
                    return
            cuerpo: dict = {"fase_actual": index, "fases_total": total,
                             "fase_nombre": nombre}
            if eta_segundos is not None:
                cuerpo["eta_segundos"] = eta_segundos
            # SÍNCRONO a propósito, al revés que `progreso`: aquí `emit` se
            # llama desde el hilo principal del pipeline, entre fases, no
            # desde un hilo de subida al que no se le puede hacer esperar. No
            # hay nada más corriendo en ese instante a lo que este PATCH
            # pueda frenar, y el timeout corto (5 s por defecto) ya acota el
            # riesgo de que la Suite esté lenta.
            self._peticion("PATCH", f"{RUTA_RUNS}/{run_id}", cuerpo)
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.fase: excepción inesperada (%s)", exc)

    def log(self, mensaje: str, nivel: str = "info") -> None:
        """Encola una linea de log. Se envia en lotes de TAM_LOTE_LOGS.

        Fail-open como el resto del reporter: la telemetria nunca tumba el Job.
        """
        try:
            with self._lock:
                if self._id is None:
                    return
                self._seq_log += 1
                self._buffer_logs.append({
                    "seq": self._seq_log,
                    "nivel": nivel if nivel in ("info", "warn", "error") else "info",
                    "mensaje": str(mensaje)[:4000],
                })
                lleno = len(self._buffer_logs) >= TAM_LOTE_LOGS
            if lleno:
                self.vaciar_logs()
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.log: excepcion inesperada (%s)", exc)

    def vaciar_logs(self) -> None:
        """Envia lo que haya en el buffer. Idempotente si esta vacio."""
        try:
            with self._lock:
                run_id = self._id
                lote = self._buffer_logs
                self._buffer_logs = []
            if run_id is None or not lote:
                return
            self._peticion("POST", f"{RUTA_RUNS}/{run_id}/logs", {"lineas": lote})
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.vaciar_logs: excepcion inesperada (%s)", exc)

    def fin(self, *, ok: bool, error: str | None = None, stats: dict | None = None) -> None:
        """Cierra el run. A partir de aqui, `activo` vuelve a ser `False`.

        `stats` es el snapshot de atom_core.progress_stats.StatsTracker: se manda
        en el mismo POST de cierre para no abrir otra ruta. Las claves que la
        Suite no tenga whitelisted se ignoran alli en silencio.
        """
        try:
            self.vaciar_logs()   # ANTES de soltar el id: despues ya no hay run al que colgarlos
            with self._lock:
                run_id = self._id
                self._id = None
            if run_id is None:
                return
            cuerpo: dict = {"estado": "ok"} if ok else {"estado": "error", "error": error or ""}
            if stats:
                cuerpo.update({
                    "img_rgb": stats.get("rgb"),
                    "img_termica": stats.get("termica"),
                    "img_rot90": stats.get("rot90"),
                    "img_rot270": stats.get("rot270"),
                    "img_sin_rotar": stats.get("rot_none"),
                })
            self._peticion("POST", f"{RUTA_RUNS}/{run_id}/fin", cuerpo)
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.fin: excepcion inesperada (%s)", exc)

    @property
    def activo(self) -> bool:
        """`True` si hay un run vivo (con id asignado por la Suite)."""
        with self._lock:
            return self._id is not None
