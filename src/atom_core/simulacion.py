"""Modo simulación del apply (`--fase apply --simular`).

Mide tiempo y coste reales del apply sin tocar la planta: hace TODA la lectura y el
cómputo igual que el real, pero ninguna operación de ESCRITURA/COPIA/MOVE/DELETE
llega al almacenamiento.

GUARD CENTRAL: `interceptar(...)` se llama en el punto más bajo de cada camino de
escritura (`almacen.py`: `AlmacenGCS`/`AlmacenLocal`.publicar/mover/borrar,
`publicar_en`, `crear_sin_pisar`, `copiar_servidor`; `apply_shards`: volcado de
deltas; `phases`: telemetría). Si la simulación está activa cuenta la operación (y
los bytes que habría escrito) y devuelve True: el llamador NO escribe.

ÚNICA escritura real: el informe `<destino>/.organizado/simulaciones/
<execution>-task<idx>.json`, bajo `permitir_informe()`.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager

try:  # Windows (escritorio) no tiene `resource`; la simulación es de Cloud Run (Linux)
    import resource
except ImportError:  # pragma: no cover
    resource = None

CARPETA_SIMULACIONES = "simulaciones"
FASES_TIEMPO = ("listado", "lectura", "computo", "escritura_simulada")

_lock = threading.Lock()
_permitir = threading.local()
_estado: "dict | None" = None


def activa() -> bool:
    return _estado is not None


def activar() -> None:
    """Enciende la simulación y reinicia contadores (una por proceso/task)."""
    global _estado
    with _lock:
        _estado = {
            "t0": time.monotonic(),
            "ru0": _cpu(),
            "segundos": {f: 0.0 for f in FASES_TIEMPO},
            "ficheros": {"rgb": 0, "termica": 0},
            "bytes_leidos": 0,
            "ops": {},
        }


def desactivar() -> None:
    global _estado
    with _lock:
        _estado = None


def _cpu() -> float:
    if resource is None:
        return 0.0
    a = resource.getrusage(resource.RUSAGE_SELF)
    b = resource.getrusage(resource.RUSAGE_CHILDREN)
    return a.ru_utime + a.ru_stime + b.ru_utime + b.ru_stime


@contextmanager
def permitir_informe():
    """Deja pasar las escrituras DENTRO del bloque (solo el informe de simulación)."""
    previo = getattr(_permitir, "ok", False)
    _permitir.ok = True
    try:
        yield
    finally:
        _permitir.ok = previo


def interceptar(op: str, destino: str, nbytes: int = 0) -> bool:
    """True = simulación activa: la operación NO debe ejecutarse (ya contada)."""
    if _estado is None or getattr(_permitir, "ok", False):
        return False
    with _lock:
        o = _estado["ops"].setdefault(op, {"n": 0, "bytes": 0})
        o["n"] += 1
        o["bytes"] += int(nbytes or 0)
    return True


@contextmanager
def fase(nombre: str):
    """Acumula segundos (suma de hilos: tiempo de trabajo, no de pared) en la fase."""
    if _estado is None:
        yield
        return
    t = time.monotonic()
    try:
        yield
    finally:
        with _lock:
            if _estado is not None:
                _estado["segundos"][nombre] += time.monotonic() - t


def registrar_lectura(es_termica: bool, nbytes: int) -> None:
    if _estado is None:
        return
    with _lock:
        _estado["ficheros"]["termica" if es_termica else "rgb"] += 1
        _estado["bytes_leidos"] += int(nbytes or 0)


def ruta_informe(destino: str, ejecucion: str, shard_index: int) -> str:
    from atom_core.almacen import unir
    return unir(destino, ".organizado", CARPETA_SIMULACIONES,
                f"{ejecucion}-task{int(shard_index)}.json")


def informe(ejecucion: str, shard_index: int, shard_count: int, extra: "dict | None" = None) -> dict:
    with _lock:
        e = _estado
        ops = {k: dict(v) for k, v in e["ops"].items()}
        escritos = sum(ops.get(k, {}).get("bytes", 0)
                       for k in ("publicar", "copiar_servidor"))
        dato = {
            "simulacion": True,
            "ejecucion": ejecucion,
            "task_idx": int(shard_index),
            "tasks": int(shard_count),
            "segundos_por_fase": {k: round(v, 3) for k, v in e["segundos"].items()},
            "nota_segundos": "suma de tiempo de trabajo de todos los hilos; no es tiempo de pared",
            "ficheros": dict(e["ficheros"]),
            "bytes_leidos": e["bytes_leidos"],
            "bytes_que_se_habrian_escrito": escritos,
            "operaciones_interceptadas": ops,
            "cpu_user_sys_s": round(_cpu() - e["ru0"], 3),
            "wall_s": round(time.monotonic() - e["t0"], 3),
        }
    dato.update(extra or {})
    return dato


def escribir_informe(destino: str, ejecucion: str, shard_index: int, shard_count: int,
                     extra: "dict | None" = None) -> str:
    """Escribe el informe (ÚNICA escritura real de la simulación) y devuelve su ruta."""
    from atom_core.almacen import crear_sin_pisar
    ruta = ruta_informe(destino, ejecucion, shard_index)
    dato = informe(ejecucion, shard_index, shard_count, extra)
    descriptor, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
            json.dump(dato, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        # Precondicion "no existe" (gs://: if_generation_match=0; local: enlace duro):
        # nunca pisa el informe de otra ejecucion; si existe, `ChoqueDestino`.
        with permitir_informe():
            crear_sin_pisar(tmp, ruta)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return ruta
