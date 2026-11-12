"""Resumen de telemetria del apply que deja el cierre para la Suite.

Cada task de apply deja `<destino>/.organizado/telemetria/<ejec>-apply-<shard>.json`
(ver `phases._emitir_telemetria`, con `ejecucion_indice`). El cierre las agrega en
`<destino>/.organizado/apply/<ejecucion del indice>/resumen_apply.json`, la ruta que
lee `rutaResumenApply` (lib/organizer-nube-gcs.js). Fracciones en 0-1. Un shard sin
telemetria se lista en `faltan_shards`: nunca se omite en silencio.
"""
from __future__ import annotations

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from atom_core.almacen import (
    abrir_para_lectura, es_uri_gcs, existe_ruta, listar_ficheros, publicar_en, unir,
)


def ruta_resumen(destino: str, ejecucion_indice: str) -> str:
    return unir(destino, ".organizado", "apply", str(ejecucion_indice), "resumen_apply.json")


def _leer(ruta: str) -> "dict | None":
    try:
        with abrir_para_lectura(ruta) as local:
            with open(local, "r", encoding="utf-8") as fh:
                dato = json.loads(fh.read())
        return dato if isinstance(dato, dict) else None
    except (OSError, ValueError):
        return None


def agregar(destino: str, ejecucion_indice: str) -> dict:
    """Agrega la telemetria de las tasks de apply de la ejecucion del indice dada."""
    carpeta = unir(destino, ".organizado", "telemetria")
    nombres = []
    if existe_ruta(carpeta):
        nombres = sorted(n for n in listar_ficheros(carpeta)
                         if n.endswith(".json") and "-apply-" in n)
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(nombres)))) as pool:
        datos = list(pool.map(lambda n: _leer(unir(carpeta, n)), nombres))
    por_shard: dict[int, dict] = {}
    for d in datos:
        if (d and d.get("fase") == "apply"
                and str(d.get("ejecucion_indice", "")) == str(ejecucion_indice)):
            por_shard[int(d["shard_index"])] = d  # un reintento reemplaza al intento previo
    shard_count = max([int(d.get("shard_count", 1)) for d in por_shard.values()] or [0])
    faltan = [i for i in range(shard_count) if i not in por_shard]
    resumen: dict = {
        "n_tasks": len(por_shard),
        "faltan_shards": faltan,
    }
    if not por_shard:
        resumen.update(utilizacion=None, cpu_s=0, pared_s_max=0, vcpu=None)
        return resumen
    util = {i: float(d["utilizacion"]) for i, d in sorted(por_shard.items())}
    resumen = {
        "utilizacion": {
            "media": round(sum(util.values()) / len(util), 3),
            "min": round(min(util.values()), 3),
            "por_shard": {str(i): round(u, 3) for i, u in util.items()},
        },
        "n_tasks": len(por_shard),
        "cpu_s": round(sum(float(d["cpu_s"]) for d in por_shard.values()), 2),
        "pared_s_max": round(max(float(d["pared_s"]) for d in por_shard.values()), 2),
        "vcpu": max(float(d["vcpu"]) for d in por_shard.values()),
        "faltan_shards": faltan,
    }
    resumen["vcpu"] = int(resumen["vcpu"]) if resumen["vcpu"] == int(resumen["vcpu"]) else resumen["vcpu"]
    return resumen


def escribir(destino: str, ejecucion_indice: str) -> "tuple[str, dict]":
    """Agrega y publica el resumen. Devuelve (ruta, resumen)."""
    resumen = agregar(destino, ejecucion_indice)
    ruta = ruta_resumen(destino, ejecucion_indice)
    texto = json.dumps(resumen, ensure_ascii=False)
    if es_uri_gcs(ruta):
        descriptor, tmp = tempfile.mkstemp(suffix=".json")
        os.close(descriptor)
    else:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        tmp = ruta + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(texto + "\n")
        if es_uri_gcs(ruta):
            publicar_en(tmp, ruta)
        else:
            os.replace(tmp, ruta)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return ruta, resumen
