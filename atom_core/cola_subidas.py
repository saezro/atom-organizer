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

NOMBRE_COLA = "cola_subidas.json"

# Serializa encolar/descartar/marcar_intento dentro de este proceso: son
# read-modify-write completos sobre el JSON y dos hilos del kiosco
# (ThreadingHTTPServer) pueden entrelazarse y pisarse el _escribir del otro
# (lost update / colisión en el .tmp compartido).
_LOCK_PROCESO = threading.Lock()


def _ruta_cola() -> Path:
    from atom_core.google_auth import user_data_dir
    return user_data_dir() / NOMBRE_COLA


def _id_job(folder: str, prefix: str) -> str:
    """Un trabajo se identifica por carpeta resuelta + destino: pulsar 'subir'
    dos veces sobre lo mismo es un trabajo, no dos."""
    clave = f"{Path(folder).resolve()}|{prefix}"
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()[:16]


def _leer(ruta: Path) -> list[dict]:
    try:
        crudo = ruta.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        datos = json.loads(crudo)
    except ValueError:
        # Escritura a medias o disco lleno: se trata como cola vacía en vez de
        # impedir que arranque la app. Se reescribirá limpia al siguiente encolar.
        return []
    if not isinstance(datos, list):
        return []
    return [j for j in datos if isinstance(j, dict) and "id" in j]


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
    """Escritura atómica: un corte no debe dejar la cola truncada."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ruta)


def encolar(folder: str, prefix: str, inspeccion_id: int | None = None,
            *, ruta: Path | None = None) -> dict:
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        job_id = _id_job(folder, prefix)
        for j in jobs:
            if j["id"] == job_id:
                return j
        job = {
            "id": job_id,
            "folder": str(folder),
            "prefix": str(prefix),
            "inspeccion_id": inspeccion_id,
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


def marcar_intento(job_id: str, error: str = "", *, ruta: Path | None = None) -> None:
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        for j in jobs:
            if j["id"] == job_id:
                j["intentos"] = int(j.get("intentos", 0)) + 1
                j["ultimo_error"] = error
                _escribir(ruta, jobs)
                return
