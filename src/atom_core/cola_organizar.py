"""Cola de trabajos de «Organizar» («modo cola»).

El operario elige carpeta/inspección/estadillo/destino, lo manda a organizar y,
mientras corre, puede encolar otros. Se procesan UNO A UNO en la misma app
(el pipeline sigue teniendo un solo run a la vez) y se pueden dejar varios hechos.

Este módulo solo guarda QUÉ hay que organizar y en qué estado está cada trabajo.
No ejecuta nada: el hilo que los consume vive en `Api` (`app_webview.py`). La
idempotencia real (no repetir lo ya organizado) la da el manifiesto del destino,
por eso reintentar un trabajo interrumpido es seguro.

Persistencia: `user_data_dir()/cola_organizar.json`, mismo patrón que
`cola_subidas.py` (JSON con indent 2, escritura atómica tmp + os.replace,
`threading.Lock` + `fcntl.flock`).
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Misma sección crítica (threading.Lock + flock sobre `<fichero>.lock`) que la
# cola de subidas: se reutiliza en vez de duplicar 60 líneas de locking.
from atom_core.cola_subidas import (  # noqa: F401
    ColaIlegible, _SeccionCritica, _escribir as _escribir_cola, _leer_lista_json, tomar_avisos)

NOMBRE_COLA = "cola_organizar.json"

PENDIENTE = "pendiente"
EN_CURSO = "en_curso"
HECHO = "hecho"
ERROR = "error"
CANCELADO = "cancelado"
INTERRUMPIDO = "interrumpido"

ESTADOS = (PENDIENTE, EN_CURSO, HECHO, ERROR, CANCELADO, INTERRUMPIDO)
# Se pueden quitar de la lista; un `en_curso` NUNCA (se cancela, no se borra).
QUITABLES = (PENDIENTE, HECHO, ERROR, CANCELADO, INTERRUMPIDO)
REINTENTABLES = (ERROR, CANCELADO, INTERRUMPIDO)

# Hueco mínimo en el destino aunque el origen sea pequeño.
MARGEN_MINIMO_BYTES = 1 * 1024 ** 3

_TZ = ZoneInfo("Europe/Madrid")


def _ahora() -> str:
    """Hora ISO en Europe/Madrid (regla del equipo: nunca UTC al guardar/mostrar)."""
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _ruta_cola() -> Path:
    from atom_core.google_auth import user_data_dir
    return user_data_dir() / NOMBRE_COLA


def _clave_ruta(p: str) -> str:
    """Clave de comparación de una carpeta: resuelta y normalizada."""
    return os.path.normcase(os.path.realpath(str(p or "").strip()))


def _origen(params: dict) -> str:
    return str((params or {}).get("origen") or (params or {}).get("input_folder") or "").strip()


def _destino(params: dict) -> str:
    return str((params or {}).get("destino") or (params or {}).get("output_folder") or "").strip()


def _leer(ruta: Path) -> list[dict]:
    """Ver `cola_subidas._leer_lista_json`: ausente = vacía; OSError persistente
    lanza `ColaIlegible` (sin escribir); JSON corrupto se aparta y se empieza vacía."""
    datos = _leer_lista_json(ruta)
    return [j for j in datos if isinstance(j, dict) and "id" in j and "params" in j]


def _escribir(ruta: Path, jobs: list[dict]) -> None:
    """Escritura atómica con fsync: un corte no debe dejar la cola truncada."""
    _escribir_cola(ruta, jobs)


def encolar(params: dict, advanced: dict | None = None, *, ruta: Path | None = None) -> dict:
    """Añade un trabajo `pendiente`.

    Devuelve `{"ok": True, "job": {...}, "avisos": [...]}` o `{"ok": False, "error": "..."}`.
    - Mismo ORIGEN ya en cola (pendiente/en_curso): se rechaza con error claro.
    - Mismo DESTINO en otro trabajo: se permite con aviso (tandas multi-día: el
      manifiesto acumula).
    """
    ruta = ruta or _ruta_cola()
    if not isinstance(params, dict):
        return {"ok": False, "error": "Parámetros del trabajo inválidos."}
    origen, destino = _origen(params), _destino(params)
    if not origen:
        return {"ok": False, "error": "Falta la carpeta del vuelo (origen)."}
    if not destino:
        return {"ok": False, "error": "Falta la carpeta final (destino)."}
    k_origen, k_destino = _clave_ruta(origen), _clave_ruta(destino)
    if k_origen == k_destino:
        return {"ok": False, "error": "El origen y el destino no pueden ser la misma carpeta."}
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        avisos: list[str] = []
        for j in jobs:
            p = j.get("params") or {}
            if j.get("estado") in (PENDIENTE, EN_CURSO) and _clave_ruta(_origen(p)) == k_origen:
                return {"ok": False, "codigo": "ya_en_cola",
                        "error": f"Esa carpeta ya está en la cola ({j['estado'].replace('_', ' ')}): {origen}"}
        if any(_clave_ruta(_destino(j.get("params") or {})) == k_destino
               and j.get("estado") in (PENDIENTE, EN_CURSO, HECHO) for j in jobs):
            avisos.append(
                f"El destino {destino} ya lo usa otro trabajo de la cola: las imágenes se "
                "acumulan en él (tandas del mismo destino).")
        job = {
            "id": uuid.uuid4().hex[:12],
            "params": dict(params),
            "advanced": dict(advanced) if advanced else None,
            "estado": PENDIENTE,
            "creado": _ahora(),
            "iniciado": None,
            "fin": None,
            "resumen": "",
            "error": "",
        }
        jobs.append(job)
        _escribir(ruta, jobs)
        return {"ok": True, "job": job, "avisos": avisos}


def listar(*, ruta: Path | None = None) -> list[dict]:
    return _leer(ruta or _ruta_cola())


def obtener(job_id: str, *, ruta: Path | None = None) -> dict | None:
    for j in listar(ruta=ruta):
        if j["id"] == job_id:
            return j
    return None


def siguiente(*, ruta: Path | None = None) -> dict | None:
    """Primer trabajo `pendiente` (orden de encolado) o None."""
    for j in listar(ruta=ruta):
        if j.get("estado") == PENDIENTE:
            return j
    return None


def quitar(job_id: str, *, ruta: Path | None = None) -> dict:
    """Quita un trabajo de la lista. Nunca un `en_curso` (se cancela, no se borra)."""
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        for j in jobs:
            if j["id"] == job_id:
                if j.get("estado") not in QUITABLES:
                    return {"ok": False, "error": "No se puede quitar un trabajo en curso: cancélalo antes."}
                _escribir(ruta, [x for x in jobs if x["id"] != job_id])
                return {"ok": True}
    return {"ok": False, "error": "El trabajo ya no está en la cola."}


def marcar(job_id: str, estado: str, *, resumen: str | None = None,
           error: str | None = None, solo_si: str | None = None,
           ruta: Path | None = None) -> dict | None:
    """Cambia el estado y sella `iniciado`/`fin` en hora de Madrid. None si no existe
    (o si `solo_si` se indica y el trabajo ya no está en ese estado)."""
    if estado not in ESTADOS:
        raise ValueError(f"estado desconocido: {estado!r}")
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        for j in jobs:
            if j["id"] == job_id:
                if solo_si is not None and j.get("estado") != solo_si:
                    return None
                j["estado"] = estado
                if estado == EN_CURSO:
                    j["iniciado"] = _ahora()
                    j["fin"] = None
                    j["error"] = ""
                    j["resumen"] = ""
                elif estado in (HECHO, ERROR, CANCELADO, INTERRUMPIDO):
                    j["fin"] = _ahora()
                if resumen is not None:
                    j["resumen"] = resumen
                if error is not None:
                    j["error"] = error
                _escribir(ruta, jobs)
                return j
    return None


def reintentar(job_id: str, *, ruta: Path | None = None) -> dict:
    """`error`/`cancelado`/`interrumpido` -> `pendiente`. Seguro: el manifiesto del
    destino salta lo ya hecho. Rechaza si el mismo origen ya está pendiente/en curso."""
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        for j in jobs:
            if j["id"] == job_id:
                if j.get("estado") not in REINTENTABLES:
                    return {"ok": False, "error": "Solo se reintentan trabajos con error, cancelados o interrumpidos."}
                k = _clave_ruta(_origen(j["params"]))
                for o in jobs:
                    if (o is not j and o.get("estado") in (PENDIENTE, EN_CURSO)
                            and _clave_ruta(_origen(o.get("params") or {})) == k):
                        return {"ok": False, "error": "Esa carpeta ya está pendiente o en curso en la cola."}
                j.update(estado=PENDIENTE, iniciado=None, fin=None, resumen="", error="")
                _escribir(ruta, jobs)
                return {"ok": True, "job": j}
    return {"ok": False, "error": "El trabajo ya no está en la cola."}


def marcar_interrumpidos(*, ruta: Path | None = None) -> int:
    """Al abrir la app: lo que quedó `en_curso` (la app se cerró a mitad) pasa a
    `interrumpido`. No se reanuda solo. Devuelve cuántos."""
    ruta = ruta or _ruta_cola()
    with _SeccionCritica(ruta):
        jobs = _leer(ruta)
        n = 0
        for j in jobs:
            if j.get("estado") == EN_CURSO:
                j["estado"] = INTERRUMPIDO
                j["fin"] = _ahora()
                j["error"] = "La app se cerró mientras se organizaba. Reintenta: se saltará lo ya hecho."
                n += 1
        if n:
            _escribir(ruta, jobs)
        return n


def _tamano_carpeta(carpeta: str) -> int:
    total = 0
    for raiz, _dirs, ficheros in os.walk(carpeta):
        for f in ficheros:
            try:
                total += os.path.getsize(os.path.join(raiz, f))
            except OSError:
                continue
    return total


def _existente_mas_cercano(p: str) -> str:
    p = os.path.abspath(p)
    while p and not os.path.exists(p):
        padre = os.path.dirname(p)
        if padre == p:
            break
        p = padre
    return p


def espacio_suficiente(origen: str, destino: str, *, uso_disco=shutil.disk_usage,
                       tamano=_tamano_carpeta) -> tuple[bool, str]:
    """¿Cabe el trabajo en el destino? Necesita al menos el tamaño del origen
    (y un margen mínimo de 1 GiB). `(True, "")` si cabe; `(False, mensaje)` si no.
    Si no se puede medir el disco no bloquea: el run ya falla visible si se llena."""
    try:
        libre = uso_disco(_existente_mas_cercano(destino)).free
    except OSError:
        return True, ""
    try:
        necesario = tamano(origen)
    except OSError:
        necesario = 0
    necesario = max(necesario, MARGEN_MINIMO_BYTES)
    if libre < necesario:
        def gb(n):
            return f"{n / 1024 ** 3:.1f} GB"
        return False, (f"Sin espacio en el destino: libres {gb(libre)}, "
                       f"se necesitan al menos {gb(necesario)} ({destino}).")
    return True, ""
