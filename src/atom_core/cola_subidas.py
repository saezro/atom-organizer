"""Subidas que se aceptaron sin credencial y quedan a la espera.

La Raspberry Pi está en el campo: si el dispositivo aparece revocado, decirle
al operario "no se puede subir" es perder el trabajo del día. Se acepta el
encargo, se deja anotado en disco, y se sube cuando vuelva a haber credencial.

No guarda los ficheros: guarda QUÉ carpeta subir y a dónde. La idempotencia
real (no re-subir lo ya subido) ya la resuelve `cloud_upload.Manifest` y el
estado de lotes de `atom_core/lotes.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

try:
    # No existe en Windows (este repo también se empaqueta con
    # build_windows.bat / atom_organizer.spec). Ahí solo queda el
    # threading.Lock: cubre el caso real de hoy (varios hilos del
    # ThreadingHTTPServer del kiosco en el mismo proceso). El caso de
    # dos PROCESOS (app de escritorio + servidor) a la vez en Windows
    # no queda protegido, pero es un escenario que hoy no se da en
    # despliegue Windows (ahí no corre el kiosco).
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

NOMBRE_COLA = "cola_subidas.json"

TIPO_CRUDO = "crudo"
TIPO_RESULTADO = "resultado"

# Serializa encolar/descartar/marcar_intento dentro de este proceso: son
# read-modify-write completos sobre el JSON y dos hilos del kiosco
# (ThreadingHTTPServer) pueden entrelazarse y pisarse el _escribir del otro
# (lost update / colisión en el .tmp compartido).
_LOCK_PROCESO = threading.Lock()


def _ruta_cola() -> Path:
    from atom_core.google_auth import user_data_dir
    return user_data_dir() / NOMBRE_COLA


def _id_job(folder: str, prefix: str, tipo: str = TIPO_CRUDO) -> str:
    """Un trabajo se identifica por carpeta resuelta + destino: pulsar 'subir'
    dos veces sobre lo mismo es un trabajo, no dos."""
    if tipo == TIPO_CRUDO:
        clave = f"{Path(folder).resolve()}|{prefix}"
    else:
        clave = f"{tipo}|{Path(folder).resolve()}|{prefix}"
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()[:16]


class ColaIlegible(OSError):
    """El fichero de cola existe pero no se pudo leer tras reintentar.

    Se propaga (no se devuelve `[]`): la siguiente escritura habría pisado una
    cola buena con una vacía."""


_LECTURA_REINTENTOS = 4
_LECTURA_ESPERA_S = 0.15

# Avisos para la UI (cola corrupta apartada). `tomar_avisos()` los vacía.
_AVISOS: list[str] = []


def _fecha_madrid() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d_%H%M%S")


def tomar_avisos() -> list[str]:
    """Devuelve y vacía los avisos pendientes (p. ej. cola corrupta apartada)."""
    with _LOCK_AVISOS:
        out = list(_AVISOS)
        _AVISOS.clear()
        return out


_LOCK_AVISOS = threading.Lock()


def _leer_lista_json(ruta: Path, *, dormir=time.sleep) -> list:
    """Lee una lista JSON de cola con política segura.

    - Ausente: `[]` (cola vacía legítima).
    - OSError: reintenta unas veces; si persiste lanza `ColaIlegible` (NO se escribe).
    - JSON corrupto: lo renombra a `<nombre>.corrupto-<fecha Madrid>`, avisa en log
      y UI, y empieza vacía.
    """
    ultimo: OSError | None = None
    crudo = None
    for i in range(_LECTURA_REINTENTOS):
        try:
            crudo = ruta.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return []
        except OSError as exc:
            ultimo = exc
            if i < _LECTURA_REINTENTOS - 1:
                dormir(_LECTURA_ESPERA_S)
    if crudo is None:
        raise ColaIlegible(f"No se pudo leer la cola {ruta.name}: {ultimo}")
    try:
        datos = json.loads(crudo)
    except ValueError:
        apartado = ruta.with_name(f"{ruta.name}.corrupto-{_fecha_madrid()}")
        try:
            os.replace(ruta, apartado)
            msg = (f"La cola {ruta.name} estaba corrupta: se guardó aparte como "
                   f"{apartado.name} y se empieza vacía.")
        except OSError as exc:
            raise ColaIlegible(
                f"La cola {ruta.name} está corrupta y no se pudo apartar: {exc}") from exc
        logger.error(msg)
        with _LOCK_AVISOS:
            _AVISOS.append(msg)
        return []
    return datos if isinstance(datos, list) else []


def _leer(ruta: Path) -> list[dict]:
    datos = _leer_lista_json(ruta)
    jobs = [j for j in datos if isinstance(j, dict) and "id" in j]
    for j in jobs:
        j.setdefault("tipo", TIPO_CRUDO)
    return jobs


class _SeccionCritica:
    """Serializa un read-modify-write completo sobre el fichero de cola.

    Dos capas:
    - `threading.Lock`: cubre los hilos del propio proceso (el caso real de
      hoy, con el `ThreadingHTTPServer` del kiosco).
    - `fcntl.flock` sobre un `.lock` hermano al fichero de cola: cubre además
      dos PROCESOS a la vez (la app de escritorio y el servidor del kiosco
      pueden coexistir en la misma máquina). No existe en Windows: ahí se
      degrada a solo el `threading.Lock` (ver arriba).

    Nada de esperas infinitas: si no se consigue el flock en unos segundos
    (p.ej. un proceso murió sin soltarlo), se sigue igualmente en vez de
    colgar el kiosco para siempre — peor perder una actualización en un caso
    ya de por sí extremo que dejar el servidor sin responder.
    """

    _TIMEOUT_FLOCK = 5.0

    def __init__(self, ruta: Path) -> None:
        self._ruta_lock = ruta.with_suffix(ruta.suffix + ".lock")
        self._fd = None

    def __enter__(self) -> "_SeccionCritica":
        _LOCK_PROCESO.acquire()
        if fcntl is not None:
            try:
                self._ruta_lock.parent.mkdir(parents=True, exist_ok=True)
                self._fd = open(self._ruta_lock, "a+")
                inicio = time.monotonic()
                while True:
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() - inicio >= self._TIMEOUT_FLOCK:
                            # No se pudo asegurar el lock inter-proceso a
                            # tiempo: se continúa solo con el threading.Lock
                            # en vez de colgar el kiosco indefinidamente.
                            break
                        time.sleep(0.05)
            except OSError:
                self._fd = None
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fd.close()
            self._fd = None
        _LOCK_PROCESO.release()


def _escribir(ruta: Path, jobs: list[dict]) -> None:
    """Escritura atómica: un corte no debe dejar la cola truncada (fsync antes del replace)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(jobs, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def encolar(folder: str, prefix: str, inspeccion_id: int | None = None,
            *, tipo: str = TIPO_CRUDO, ruta: Path | None = None) -> dict:
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        job_id = _id_job(folder, prefix, tipo)
        for j in jobs:
            if j["id"] == job_id:
                return j
        job = {
            "id": job_id,
            "folder": str(folder),
            "prefix": str(prefix),
            "inspeccion_id": inspeccion_id,
            "tipo": tipo,
            "creado_en": time.time(),
            "intentos": 0,
            "ultimo_error": "",
        }
        jobs.append(job)
        _escribir(ruta, jobs)
        return job


def pendientes(*, ruta: Path | None = None) -> list[dict]:
    return _leer(ruta or _ruta_cola())


def descartar(job_id: str, *, ruta: Path | None = None) -> bool:
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        quedan = [j for j in jobs if j["id"] != job_id]
        if len(quedan) == len(jobs):
            return False
        _escribir(ruta, quedan)
        return True


def marcar_intento(job_id: str, error: str = "", *, ruta: Path | None = None) -> int:
    """Suma un intento al job y devuelve el contador nuevo (0 si no existe)."""
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        for j in jobs:
            if j["id"] == job_id:
                j["intentos"] = int(j.get("intentos", 0)) + 1
                j["ultimo_error"] = error
                _escribir(ruta, jobs)
                return int(j["intentos"])
    return 0


BACKOFF_RED_BASE_S = 30
BACKOFF_RED_TOPE_S = 1800


def aplazar_red(job_id: str, error: str = "", *, ahora: float | None = None,
                ruta: Path | None = None) -> float:
    """Fallo por red: backoff exponencial con tope, SIN consumir `intentos`.

    Anota `intentos_red` y `no_antes` (epoch); `listo()` deja el job fuera de los
    drenados hasta entonces, así un fallo persistente mal clasificado como red no
    relanza en bucle caliente. Devuelve los segundos de espera (0 si no existe)."""
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        for j in jobs:
            if j["id"] == job_id:
                n = int(j.get("intentos_red", 0)) + 1
                espera = min(BACKOFF_RED_BASE_S * 2 ** (n - 1), BACKOFF_RED_TOPE_S)
                j["intentos_red"] = n
                j["no_antes"] = (time.time() if ahora is None else ahora) + espera
                j["ultimo_error"] = error
                _escribir(ruta, jobs)
                return float(espera)
    return 0.0


def listo(job: dict, *, ahora: float | None = None) -> bool:
    """True si el job no está aplazado por backoff de red."""
    return float(job.get("no_antes", 0) or 0) <= (time.time() if ahora is None else ahora)
