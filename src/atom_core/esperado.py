"""Contrato `esperado_shardN.json` entre las etapas `struct` y `post`.

Cada tarea de `struct` anota, ANTES de mover nada, las fotos que le tocan y
tienen vuelo asignado: `{"origen": "TERMICA/x.JPG", "destino": "PB1/PB1_V1/x.JPG"}`
(rutas relativas al destino, clave completa, nunca basename). La etapa final
(`post`, tarea 0) lo cruza con lo que realmente quedó organizado: una foto cuyo
origen sigue en la raíz o cuyo destino no existe es un ERROR. Sustituye al
guard basado en `manifiesto.db`, que no existe en las etapas Cloud Run.
Vive en `.organizado/` (excluida de la subida del resultado)."""
from __future__ import annotations

import json
import os
import re
import tempfile

from atom_core import almacen
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO

_PATRON = re.compile(r"^esperado_shard\d+\.json$")


def _relativa(raiz: str, ruta: str) -> str:
    raiz = raiz.replace("\\", "/").rstrip("/") + "/"
    ruta = str(ruta).replace("\\", "/")
    return ruta[len(raiz):] if ruta.startswith(raiz) else ruta


def escribir_esperado(raiz: str, shard_index: int, movimientos) -> str:
    """Publica `esperado_shard<N>.json` (siempre, aunque la lista vaya vacía)."""
    pares = [{"origen": _relativa(raiz, o), "destino": _relativa(raiz, d)}
             for o, d in movimientos]
    destino = almacen.unir(raiz, NOMBRE_CARPETA_MANIFIESTO, f"esperado_shard{shard_index}.json")
    if not almacen.es_uri_gcs(raiz):
        os.makedirs(os.path.dirname(destino), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"esperado_shard{shard_index}.json")
        with open(local, "w", encoding="utf-8") as f:
            json.dump({"shard": shard_index, "fotos": pares}, f)
        almacen.publicar_en(local, destino)
    return destino


def borrar_propio(raiz: str, shard_index: int) -> bool:
    """Borra SOLO el `esperado_shard<shard_index>.json` de este shard (el de una
    corrida previa), al empezar la tarea: si muere antes de escribir el suyo, no
    debe quedar el viejo haciéndose pasar por el de esta corrida. Devuelve True
    si existía. No toca ningún otro índice (los compañeros escriben los suyos)."""
    ruta = almacen.unir(raiz, NOMBRE_CARPETA_MANIFIESTO, f"esperado_shard{shard_index}.json")
    if not almacen.existe_ruta(ruta):
        return False
    alm, prefijo = almacen.abrir_almacen(ruta)
    try:
        alm.borrar(prefijo)
    except FileNotFoundError:
        return False
    return True


def limpiar_sobrantes(raiz: str, shard_count: int) -> list[str]:
    """Borra los `esperado_shardN.json` con N >= `shard_count`: restos de una
    corrida previa con MÁS shards que la actual, que `leer_esperados` leería
    como si fueran de esta (y acusaría fotos que ya no se esperan).

    Es seguro llamarla desde cada shard concurrente: solo toca índices que
    ningún shard de ESTA corrida escribe (todos son < shard_count), así que no
    puede borrar el fichero de un compañero. Los índices < shard_count los
    reescribe siempre su dueño (`escribir_esperado`, aunque vaya vacío).
    Devuelve los nombres borrados."""
    carpeta = almacen.unir(raiz, NOMBRE_CARPETA_MANIFIESTO)
    if not almacen.existe_ruta(carpeta):
        return []
    borrados = []
    for nombre in almacen.listar_ficheros(carpeta):
        if not _PATRON.match(nombre) or int(re.search(r"\d+", nombre).group()) < shard_count:
            continue
        alm, prefijo = almacen.abrir_almacen(almacen.unir(carpeta, nombre))
        try:
            alm.borrar(prefijo)
        except FileNotFoundError:
            pass  # otro shard del mismo reparto ya lo quitó
        borrados.append(nombre)
    return borrados


def leer_esperados(raiz: str) -> "list[dict] | None":
    """Todas las entradas de todos los shards; `None` si no hay ningún fichero."""
    carpeta = almacen.unir(raiz, NOMBRE_CARPETA_MANIFIESTO)
    if not almacen.existe_ruta(carpeta):
        return None
    nombres = [n for n in almacen.listar_ficheros(carpeta) if _PATRON.match(n)]
    if not nombres:
        return None
    fotos: list[dict] = []
    for nombre in nombres:
        with almacen.abrir_para_lectura(almacen.unir(carpeta, nombre)) as local:
            with open(local, encoding="utf-8") as f:
                fotos.extend(json.load(f).get("fotos", []))
    return fotos


def _existentes_gcs(raiz: str, rutas: "list[str]") -> "set[str]":
    """Subconjunto de `rutas` (relativas a `raiz`, en `gs://`) que existe, listando
    UNA vez cada carpeta contenedora distinta (origen TERMICA/RGB y carpetas de
    destino) en vez de 2-3 round-trips por foto. Misma semántica que
    `almacen.existe_ruta` para ficheros: la clave exacta está en el listado."""
    carpetas = {rel.rsplit("/", 1)[0] if "/" in rel else "" for rel in rutas}
    claves: set[str] = set()
    for carpeta in carpetas:
        alm, prefijo = almacen.abrir_almacen(almacen.unir(raiz, carpeta) if carpeta else raiz)
        claves.update(alm.listar(prefijo))
    existentes = set()
    for rel in rutas:
        _alm, clave = almacen.abrir_almacen(almacen.unir(raiz, rel))
        if clave in claves:
            existentes.add(rel)
    return existentes


def faltantes(raiz: str, fotos: "list[dict]") -> list[str]:
    """Entradas incumplidas: el origen sigue en la raíz o el destino no existe."""
    if almacen.es_uri_gcs(raiz):
        existentes = _existentes_gcs(
            raiz, [r for foto in fotos for r in (foto["origen"], foto["destino"])])
        existe = existentes.__contains__
    else:
        def existe(rel: str) -> bool:
            return almacen.existe_ruta(almacen.unir(raiz, rel))
    malas = []
    for foto in fotos:
        sigue = existe(foto["origen"])
        if sigue or not existe(foto["destino"]):
            malas.append(f"{foto['origen']} -> {foto['destino']}" +
                         (" (sigue en la raíz)" if sigue else " (no aparece en destino)"))
    return malas
