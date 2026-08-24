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
import time
from pathlib import Path

NOMBRE_COLA = "cola_subidas.json"


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


def _escribir(ruta: Path, jobs: list[dict]) -> None:
    """Escritura atómica: un corte no debe dejar la cola truncada."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ruta)


def encolar(folder: str, prefix: str, inspeccion_id: int | None = None,
            *, ruta: Path | None = None) -> dict:
    ruta = ruta or _ruta_cola()
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
    jobs = _leer(ruta)
    quedan = [j for j in jobs if j["id"] != job_id]
    if len(quedan) == len(jobs):
        return False
    _escribir(ruta, quedan)
    return True


def marcar_intento(job_id: str, error: str = "", *, ruta: Path | None = None) -> None:
    ruta = ruta or _ruta_cola()
    jobs = _leer(ruta)
    for j in jobs:
        if j["id"] == job_id:
            j["intentos"] = int(j.get("intentos", 0)) + 1
            j["ultimo_error"] = error
            _escribir(ruta, jobs)
            return
