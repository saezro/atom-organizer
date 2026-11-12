"""`propuesta.json` (contrato v1 de la spec «índice calendario», §4).

Agrega `manifiesto.db` + `resumen.json` del índice en un JSON de KB que el backend
de la Suite lee sin estado. Nada se adivina: un dato incoherente o inválido lanza
`ErrorPropuesta` y no se escribe fichero.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

from atom_core import camara_rgb as camara_rgb_mod
from atom_core import pares as pares_mod
from atom_core.indice import VERSION_ALGORITMO_GIRO, _es_pb_generales  # noqa: F401

VERSION_PROPUESTA = 1
NOMBRE_PROPUESTA = "propuesta.json"
MAX_EJEMPLOS = 5
MOTIVO_SIN_CLASIFICAR = "sin-clasificar"
MOTIVO_GENERALES = "pb-generales"
MOTIVOS_NO_INDEXADOS = ("no-dji", "a-medio-copiar", "no-disponible")
TIPOS_VALIDOS = ("RGB", "RGB_Extra", "TERMICA")
TIPOS_RGB = ("RGB", "RGB_Extra")
ANGULOS_VALIDOS = (0, 90, 270)
_RE_EXIF = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?$")


CATEGORIAS_COLUMNA = ("vuelo", "pb_general", "fotos_generales", "sin_ordenar")


class ErrorPropuesta(Exception):
    """Manifiesto/resumen incoherentes o inválidos: no se genera propuesta."""


def _natural(texto: str) -> list:
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", str(texto)) if p != ""]


def _exif_valido(valor) -> "str | None":
    if not isinstance(valor, str):
        return None
    m = _RE_EXIF.match(valor)
    return m.group(1) if m else None


def _prefijo_comun(rutas: list[str]) -> str:
    """Carpeta común (con `/` final) de las rutas de origen; '' si no hay."""
    if not rutas:
        return ""
    partes = [str(r).replace("\\", "/").split("/")[:-1] for r in rutas]
    comun = partes[0]
    for p in partes[1:]:
        n = 0
        while n < min(len(comun), len(p)) and comun[n] == p[n]:
            n += 1
        comun = comun[:n]
    return "/".join(comun) + "/" if comun else ""


def _abrir_ro(ruta_db: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{ruta_db}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _requerido(resumen: dict, clave: str):
    valor = resumen.get(clave)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        raise ErrorPropuesta(f"resumen.json sin '{clave}'")
    return valor


def _validar_resumen(resumen) -> None:
    """Forma mínima de `resumen.json`; malformado -> ErrorPropuesta (nunca KeyError)."""
    if not isinstance(resumen, dict):
        raise ErrorPropuesta("resumen.json no es un objeto")
    if resumen.get("version") != VERSION_PROPUESTA:
        raise ErrorPropuesta(f"resumen.json version {resumen.get('version')!r} no soportada")
    if not isinstance(resumen.get("pb"), list):
        raise ErrorPropuesta("resumen.json: 'pb' no es una lista")
    for i, e in enumerate(resumen["pb"]):
        if not isinstance(e, dict) or any(k not in e for k in ("pb", "vuelo", "imagenes", "pares")):
            raise ErrorPropuesta(f"resumen.json: pb[{i}] malformado (se esperan pb, vuelo, imagenes, pares): {e!r}")
    fdo = resumen.get("fuera_de_orden", [])
    if not isinstance(fdo, list):
        raise ErrorPropuesta("resumen.json: 'fuera_de_orden' no es una lista")
    for i, e in enumerate(fdo):
        if not isinstance(e, dict) or "motivo" not in e or "n" not in e:
            raise ErrorPropuesta(f"resumen.json: fuera_de_orden[{i}] malformado (se esperan motivo, n): {e!r}")


def _suma_bytes(fs: list, donde: str) -> int:
    sin = sum(1 for f in fs if f["bytes_origen"] is None)
    if sin:
        raise ErrorPropuesta(f"{donde}: {sin} imagen(es) con bytes_origen NULL en el manifiesto")
    return sum(f["bytes_origen"] for f in fs)


def construir_propuesta(ruta_db: str, resumen: dict, planta: str, generado: str) -> dict:
    _validar_resumen(resumen)
    ejecucion = _requerido(resumen, "ejecucion_indice")
    hash_listado = _requerido(resumen, "hash_listado")
    for clave in ("n_imagenes", "n_sin_ordenar", "pb"):
        if clave not in resumen:
            raise ErrorPropuesta(f"resumen.json sin '{clave}'")
    con = _abrir_ro(ruta_db)
    try:
        con_columna = {c["name"] for c in con.execute("PRAGMA table_info(imagenes)")} >= {
            "categoria", "motivo_fuera"}
        filas = con.execute(
            "SELECT clave, ruta_origen, tipo, timestamp_exif, pb, vuelo, unassigned, angulo_giro, "
            "bytes_origen" + (", categoria, motivo_fuera" if con_columna else "")
            + " FROM imagenes").fetchall()
        vuelos_db = con.execute("SELECT * FROM vuelos").fetchall()
        tiene_ajustes = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ajustes'").fetchone()
        ajuste = None
        if tiene_ajustes:
            r = con.execute("SELECT valor FROM ajustes WHERE clave = ?",
                            (camara_rgb_mod.AJUSTE_MANIFIESTO,)).fetchone()
            ajuste = r["valor"] if r else None
        tiene_grupos = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='grupos_sin_colocar'").fetchone()
        try:
            grupos = [json.loads(g["datos"]) for g in con.execute(
                "SELECT datos FROM grupos_sin_colocar")] if tiene_grupos else []
        except (ValueError, TypeError) as exc:
            raise ErrorPropuesta(f"grupos_sin_colocar corruptos en el manifiesto: {exc}") from exc
        if any(not isinstance(g, dict) or not isinstance(g.get("claves", []), list) for g in grupos):
            raise ErrorPropuesta("grupos_sin_colocar corruptos en el manifiesto: grupo que no es objeto con 'claves' lista")
    finally:
        con.close()

    for f in filas:
        if f["tipo"] not in TIPOS_VALIDOS:
            raise ErrorPropuesta(f"tipo desconocido en el manifiesto: {f['tipo']!r}")

    con_sufijo = sorted(f"{v['pb']}/{v['vuelo']}/{v['sufijo']}" for v in vuelos_db if v["sufijo"] != "")
    if con_sufijo:
        raise ErrorPropuesta(f"vuelos con sufijo no soportados en propuesta v1: {con_sufijo}")
    vuelos_reg = {(v["pb"], v["vuelo"]): dict(v) for v in vuelos_db}

    # Paridad: misma regla que construir_resumen_indice / _escribir_resumen.
    hay_zoom = any(f["tipo"] in TIPOS_RGB and camara_rgb_mod.es_zoom(f["ruta_origen"]) for f in filas)
    rgb = "Z" if (str(ajuste or "").strip().upper() == "Z" and hay_zoom) else "W"

    # Fuente de la categoría: la columna si existe y es coherente con la estructura;
    # si falta (manifiesto antiguo) o una fila migrada con el default 'vuelo' la
    # contradice (sin asignar / PB GENERALES), se deriva con la regla de la FASE 1.
    fuente = "derivada-v1"
    if con_columna:
        for f in filas:
            if f["categoria"] not in CATEGORIAS_COLUMNA:
                raise ErrorPropuesta(f"categoria desconocida en el manifiesto: {f['categoria']!r}")
        if not any(f["categoria"] == "vuelo" and (f["unassigned"] or _es_pb_generales(f["pb"]))
                   for f in filas):
            fuente = "columna"
    if fuente == "columna":
        for f in filas:
            if f["unassigned"] != (f["categoria"] == "sin_ordenar"):
                raise ErrorPropuesta(
                    f"categoria {f['categoria']!r} incoherente con unassigned={f['unassigned']}: {f['ruta_origen']}")
            if f["categoria"] == "sin_ordenar" and not (f["motivo_fuera"] or "").strip():
                raise ErrorPropuesta(f"imagen sin_ordenar sin motivo_fuera en el manifiesto: {f['ruta_origen']}")

    por_vuelo: dict[tuple[str, str], list] = {}
    generales: dict[str, list] = {}
    sin_ordenar: list = []
    for f in filas:
        if fuente == "columna" and f["categoria"] in ("fotos_generales", "pb_general"):
            generales.setdefault((f["categoria"], str(f["pb"])), []).append(f)
        elif fuente == "columna" and f["categoria"] == "sin_ordenar":
            sin_ordenar.append(f)
        elif fuente == "columna":
            if f["pb"] is None or f["vuelo"] is None:
                raise ErrorPropuesta(f"imagen asignada sin pb/vuelo: {f['ruta_origen']}")
            por_vuelo.setdefault((str(f["pb"]), str(f["vuelo"])), []).append(f)
        elif f["unassigned"]:
            sin_ordenar.append(f)
        elif _es_pb_generales(f["pb"]):
            generales.setdefault(("fotos_generales", str(f["pb"])), []).append(f)
        else:
            if f["pb"] is None or f["vuelo"] is None:
                raise ErrorPropuesta(f"imagen asignada sin pb/vuelo: {f['ruta_origen']}")
            por_vuelo.setdefault((str(f["pb"]), str(f["vuelo"])), []).append(f)

    vuelos = []
    for (pb, vuelo), fs in por_vuelo.items():
        t, w = set(), set()
        for f in fs:
            clave = pares_mod.clave_pareja(f["ruta_origen"], rgb)
            if clave:
                (t if clave[1] == "T" else w).add((os.path.dirname(f["ruta_origen"]), clave[0]))
        validas, invalidas = [], 0
        for f in fs:
            if f["timestamp_exif"] is None:
                continue
            v = _exif_valido(f["timestamp_exif"])
            if v is None:
                invalidas += 1
            else:
                validas.append(v)
        reg = vuelos_reg.get((pb, vuelo))
        if reg is None:
            angulos = {f["angulo_giro"] for f in fs}
            if angulos == {None}:
                raise ErrorPropuesta(
                    f"vuelo {pb}/{vuelo} sin registro de giro y sin angulo_giro en imágenes")
            if len(angulos) != 1:
                raise ErrorPropuesta(
                    f"vuelo {pb}/{vuelo} sin registro de giro y con ángulos distintos: {sorted(angulos)}")
            base = {"angulo": angulos.pop(), "estado": "sin_registro_giro",
                    "motivo": "el manifiesto no tiene consenso de giro para este vuelo",
                    "n_total": None, "n_90": None, "n_270": None, "n_0": None,
                    "pct_ganadora": None, "version_algoritmo": None}
        else:
            base = {k: reg[k] for k in ("angulo", "estado", "motivo", "n_total", "n_90", "n_270",
                                        "n_0", "pct_ganadora", "version_algoritmo")}
        if base["angulo"] not in ANGULOS_VALIDOS:
            raise ErrorPropuesta(f"vuelo {pb}/{vuelo}: ángulo inválido {base['angulo']!r}")
        vuelos.append({
            "pb": pb, "vuelo": vuelo, "sufijo": "", **base,
            "n_imagenes": len(fs), "bytes": _suma_bytes(fs, f"vuelo {pb}/{vuelo}"),
            "inicio_exif": min(validas) if validas else None,
            "fin_exif": max(validas) if validas else None,
            "n_con_exif": len(validas), "n_exif_invalido": invalidas,
            "n_pares": len(t & w), "n_rgb": len(w), "n_termica": len(t),
        })
    vuelos.sort(key=lambda v: (v["inicio_exif"] is None, v["inicio_exif"] or "",
                               _natural(v["pb"]), _natural(v["vuelo"])))

    # Coherencia con resumen.json.
    esperados = {(str(e["pb"]), str(e["vuelo"])): e for e in resumen["pb"]
                 if not _es_pb_generales(str(e["pb"]))}
    propios = {(v["pb"], v["vuelo"]): v for v in vuelos}
    if set(esperados) != set(propios):
        raise ErrorPropuesta(
            "vuelos distintos entre manifiesto y resumen: solo en resumen "
            f"{sorted(set(esperados) - set(propios))}, solo en manifiesto {sorted(set(propios) - set(esperados))}")
    for clave, e in esperados.items():
        v = propios[clave]
        if (e["imagenes"], e["pares"]) != (v["n_imagenes"], v["n_pares"]):
            raise ErrorPropuesta(
                f"vuelo {clave[0]}/{clave[1]}: resumen imagenes/pares {e['imagenes']}/{e['pares']} "
                f"!= manifiesto {v['n_imagenes']}/{v['n_pares']}")
    if len(sin_ordenar) != resumen["n_sin_ordenar"]:
        raise ErrorPropuesta(
            f"filas unassigned=1 ({len(sin_ordenar)}) != resumen n_sin_ordenar ({resumen['n_sin_ordenar']})")

    prefijo = _prefijo_comun([f["ruta_origen"] for f in filas])

    def rel(ruta: str) -> str:
        norm = str(ruta).replace("\\", "/")
        return norm[len(prefijo):] if prefijo and norm.startswith(prefijo) else ruta

    def ejemplos(rutas: list[str]) -> list[str]:
        return [rel(r) for r in sorted(rutas)[:MAX_EJEMPLOS]]

    fuera = []
    for cat, pb in sorted(generales, key=lambda k: (k[0], _natural(k[1]))):
        fs = generales[(cat, pb)]
        fuera.append({"categoria": cat, "motivo": MOTIVO_GENERALES, "pb": pb,
                      "n_imagenes": len(fs), "bytes": _suma_bytes(fs, f"generales {pb}"),
                      "ejemplos": ejemplos([f["ruta_origen"] for f in fs]), "indexada": True})
    motivo_de: dict[str, str] = {}
    for g in grupos:
        for c in g.get("claves", []):
            m = g.get("motivo")
            if c in motivo_de and motivo_de[c] != m:
                raise ErrorPropuesta(f"clave en grupos con dos motivos: {motivo_de[c]!r} y {m!r}")
            motivo_de[c] = m
    por_motivo: dict[str, list] = {}
    for f in sin_ordenar:
        if fuente == "columna":
            m = f["motivo_fuera"].strip()
        else:
            m = (motivo_de.get(f["clave"]) or "").strip() or MOTIVO_SIN_CLASIFICAR
        por_motivo.setdefault(m, []).append(f)
    for m in sorted(por_motivo):
        fs = por_motivo[m]
        fuera.append({"categoria": "sin_ordenar", "motivo": m, "pb": None,
                      "n_imagenes": len(fs), "bytes": _suma_bytes(fs, f"sin_ordenar {m}"),
                      "ejemplos": ejemplos([f["ruta_origen"] for f in fs]), "indexada": True})
    for e in resumen.get("fuera_de_orden", []):
        if e["motivo"] in MOTIVOS_NO_INDEXADOS:
            fuera.append({"categoria": "sin_ordenar", "motivo": e["motivo"], "pb": None,
                          "n_imagenes": e["n"], "bytes": None,
                          "ejemplos": list(e.get("ejemplos", []))[:MAX_EJEMPLOS], "indexada": False})

    return {
        "version": VERSION_PROPUESTA, "ejecucion_indice": ejecucion, "hash_listado": hash_listado,
        "planta": planta, "generado": generado, "fuente_categoria": fuente,
        "totales": {"n_imagenes": resumen["n_imagenes"],
                    "bytes": _suma_bytes(filas, "totales"),
                    "n_pb": len({v["pb"] for v in vuelos}), "n_vuelos": len(vuelos),
                    "n_sin_ordenar": resumen["n_sin_ordenar"]},
        "vuelos": vuelos, "fuera_de_vuelo": fuera,
    }


def _es_gs(ruta: str) -> bool:
    return str(ruta).strip().lower().startswith("gs://")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="atom_core.propuesta")
    ap.add_argument("--manifiesto", required=True)
    ap.add_argument("--resumen", required=True)
    ap.add_argument("--planta", required=True)
    ap.add_argument("--salida", required=True)
    args = ap.parse_args(argv)
    for ruta in (args.manifiesto, args.resumen, args.salida):
        if _es_gs(ruta):
            print(f"error: solo rutas locales, no gs:// ({ruta})", file=sys.stderr)
            return 2
    try:
        with open(args.resumen, encoding="utf-8") as fh:
            resumen = json.load(fh)
        generado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        propuesta = construir_propuesta(args.manifiesto, resumen, args.planta, generado)
    except (ErrorPropuesta, OSError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    tmp = args.salida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(propuesta, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, args.salida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
