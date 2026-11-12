"""Índice map/reduce: la fase `indice` repartida entre N tasks de Cloud Run.

El cuello del índice es leer las cabeceras EXIF/XMP de todas las imágenes en UNA
task. Aquí se parte en tres pasos dentro de la MISMA fase `--fase indice`:

  1. listado   - TODAS las tasks listan el origen igual (`indice._listar_imagenes`
                 devuelve la lista ORDENADA por ruta: orden determinista).
  2. map       - la task `k` de `N` lee SOLO su tramo CONTIGUO del listado
                 (`tramo`: `[k*n//N, (k+1)*n//N)`; sin solapes ni huecos por
                 construcción) y deja su parcial
                 `<destino>/.organizado/indice/<ejecucion>/map-<k>.jsonl.gz`.
  3. reduce    - la ÚLTIMA task en terminar (la que ve los N parciales y gana el
                 lock `reduce.lock`, `ifGenerationMatch=0` en gs://, `O_EXCL` en
                 local) une los parciales por posición del listado y ejecuta el
                 resto del índice de siempre (`indice.construir_indice`):
                 PB/vuelo, giro por consenso, nombres, manifiesto. El INDICE
                 xlsx y los CSV los emite el cierre como siempre.

Con N=1 NO se usa nada de esto: es el índice de siempre, byte a byte.

Sin fallbacks silenciosos: un parcial ausente, ilegible, truncado, de otro
reparto o calculado sobre un listado distinto al del reduce es un error visible
(`ErrorIndiceParcial`), nunca se rellena ni se omite.

Formato del parcial (jsonl dentro de gzip, una línea por registro):
  {"tipo":"cabecera","version":1,"ejecucion","shard_index","shard_count",
   "n_listado","hash_listado","inicio","fin"}
  {"ruta","estado","motivo","dato"}      x (fin-inicio), en orden de listado
  {"tipo":"fin","n": fin-inicio}
`estado`: ok | a_medias | a_medias_reintento | no_dji | no_dji_reintento |
no_disponible. `dato` solo con `ok` (`_MetadatosImagen` serializado).
"""
from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import os
import tempfile

from atom_core.almacen import (
    abrir_para_lectura, es_uri_gcs, existe_ruta, listar_ficheros, publicar_en, unir,
)

VERSION_PARCIAL = 1
NOMBRE_CARPETA_INDICE = "indice"
NOMBRE_LOCK = "reduce.lock"

ESTADOS = ("ok", "a_medias", "a_medias_reintento", "no_dji", "no_dji_reintento",
           "no_disponible")


class ErrorIndiceParcial(RuntimeError):
    """Parcial del índice ausente, corrupto o incoherente con el listado."""


def carpeta_indice(destino: str, ejecucion: str) -> str:
    return unir(destino, ".organizado", NOMBRE_CARPETA_INDICE, str(ejecucion))


def nombre_parcial(shard_index: int) -> str:
    return f"map-{int(shard_index)}.jsonl.gz"


def ruta_parcial(destino: str, ejecucion: str, shard_index: int) -> str:
    return unir(carpeta_indice(destino, ejecucion), nombre_parcial(shard_index))


def ruta_lock(destino: str, ejecucion: str) -> str:
    return unir(carpeta_indice(destino, ejecucion), NOMBRE_LOCK)


def tramo(n: int, shard_index: int, shard_count: int) -> tuple[int, int]:
    """`[inicio, fin)` del listado (de `n` elementos) que le toca a la task.
    Contiguo y exhaustivo: la unión de los N tramos es `range(n)` sin solapes."""
    return (shard_index * n) // shard_count, ((shard_index + 1) * n) // shard_count


def hash_listado(imagenes: list[str]) -> str:
    resumen = hashlib.sha256()
    for ruta in imagenes:
        resumen.update(ruta.encode("utf-8"))
        resumen.update(b"\n")
    return resumen.hexdigest()


# ---- listado externo (`--listado`) -------------------------------------------

class ErrorListado(RuntimeError):
    """Listado externo ilegible, malformado o con hash distinto: aborta la fase.
    NUNCA se cae a listar el origen."""


def serializar_listado(entradas: "list[dict]") -> bytes:
    """Contenido canonico (SIN comprimir) de un listado externo, y por tanto lo
    que se hashea. Util para el que lo escribe; la Suite (Node) lo replica.

    FORMATO del listado: fichero `.jsonl.gz` (gzip de un jsonl UTF-8). Una linea
    por imagen fuente (ya filtrada por las extensiones fuente), SIN cabecera:

        {"ruta":"gs://bucket/.../DJI_0001_T.JPG","tamano":4194304}

    - `ruta`: ruta completa del origen, tal como la listaria el indice
      (`gs://...` o ruta local absoluta). `tamano`: bytes (entero >= 0).
    - Forma canonica (obligatoria; el lector la exige): cada linea es el JSON
      con claves ordenadas (`ruta`, `tamano`), separadores `(",", ":")`, sin
      escapar no-ASCII (equivale a `JSON.stringify` de Node), terminada en
      `"\\n"` (incluida la ultima). Lineas ordenadas por `ruta` en orden de
      bytes UTF-8 (= orden de codepoints), sin rutas repetidas.

    HASH (`--hash-listado-esperado`): sha256 hexadecimal en minusculas del
    contenido DESCOMPRIMIDO canonico anterior (los bytes tal cual, no del .gz)."""
    lineas = sorted(entradas, key=lambda e: e["ruta"].encode("utf-8"))
    return b"".join(
        json.dumps({"ruta": e["ruta"], "tamano": e["tamano"]}, sort_keys=True,
                   separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        for e in lineas)


def hash_listado_externo(entradas: "list[dict]") -> str:
    return hashlib.sha256(serializar_listado(entradas)).hexdigest()


def leer_listado(uri: str, hash_esperado: str) -> "tuple[list[str], dict[str, int]]":
    """Lee el listado externo `uri` (`.jsonl.gz`, formato en `serializar_listado`),
    verifica el sha256 del contenido descomprimido contra `hash_esperado` y
    devuelve `(rutas ordenadas, {ruta: tamano})`. Cualquier fallo (lectura, gzip,
    hash, linea malformada, desorden, duplicados) es `ErrorListado`."""
    import gzip  # noqa: PLC0415

    from atom_core import almacen  # noqa: PLC0415 - evita ciclo

    esperado = str(hash_esperado or "").strip().lower()
    if not esperado:
        raise ErrorListado("--listado exige --hash-listado-esperado (sha256 del listado)")
    try:
        with almacen.abrir_para_lectura(uri) as local:
            with gzip.open(local, "rb") as fh:
                contenido = fh.read()
    except Exception as exc:  # noqa: BLE001 - se re-lanza con contexto
        raise ErrorListado(f"no se pudo leer el listado {uri}: {exc}") from exc
    actual = hashlib.sha256(contenido).hexdigest()
    if actual != esperado:
        raise ErrorListado(f"listado-externo-cambiado: esperado={esperado} actual={actual} ({uri})")
    rutas: list[str] = []
    tamanos: dict[str, int] = {}
    ultima = None
    try:
        lineas = contenido.decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        raise ErrorListado(f"listado {uri}: no es UTF-8: {exc}") from exc
    if lineas and lineas[-1] == "":
        lineas.pop()
    for n, linea in enumerate(lineas, 1):
        try:
            dato = json.loads(linea)
            ruta, tamano = dato["ruta"], dato["tamano"]
            valido = (isinstance(ruta, str) and ruta and set(dato) == {"ruta", "tamano"}
                      and isinstance(tamano, int) and not isinstance(tamano, bool) and tamano >= 0)
        except (ValueError, KeyError, TypeError):
            valido = False
        if not valido:
            raise ErrorListado(f"listado {uri}: linea {n} invalida")
        if ultima is not None and ruta.encode("utf-8") <= ultima.encode("utf-8"):
            raise ErrorListado(f"listado {uri}: linea {n} desordenada o repetida ({ruta})")
        ultima = ruta
        rutas.append(ruta)
        tamanos[ruta] = tamano
    return rutas, tamanos


# ---- serialización de `_MetadatosImagen` -------------------------------------

def _num(valor, campo):
    if valor is not None and (isinstance(valor, bool) or not isinstance(valor, (int, float, str))):
        raise TypeError(f"campo {campo!r}: tipo no serializable {type(valor).__name__}")
    return valor


def dato_a_json(dato) -> dict:
    """`_MetadatosImagen` -> dict JSON. Tipo inesperado = `TypeError` (visible)."""
    ts = dato.timestamp
    if ts is not None and not isinstance(ts, datetime.datetime):
        raise TypeError(f"timestamp de {dato.ruta}: tipo no serializable {type(ts).__name__}")
    gps = dato.gps
    if gps is not None:
        gps = [float(gps[0]), float(gps[1])]
    pos = dato.posicion
    if pos is not None:
        pos = {c: _num(getattr(pos, c), c) for c in (
            "lat", "lon", "altitud_abs", "altura_relativa", "gimbal_yaw", "gimbal_pitch",
            "gimbal_roll", "flight_yaw", "ancho_px", "alto_px")}
    return {
        "ruta": dato.ruta, "nombre": dato.nombre,
        "timestamp": None if ts is None else ts.isoformat(),
        "modelo": _num(dato.modelo, "modelo"), "yaw": _num(dato.yaw, "yaw"), "gps": gps,
        "posicion": pos, "meta_leida": bool(dato.meta_leida),
        "make": _num(dato.make, "make"), "make_ilegible": bool(dato.make_ilegible),
        "a_medias": bool(dato.a_medias), "pitch": _num(dato.pitch, "pitch"),
        "flight_yaw": _num(dato.flight_yaw, "flight_yaw"),
        "jpeg_sin_eoi": dato.jpeg_sin_eoi,
    }


def dato_de_json(j: dict):
    from atom_core.indice import _MetadatosImagen, _Posicion  # noqa: PLC0415 - evita ciclo
    pos = j["posicion"]
    return _MetadatosImagen(
        ruta=j["ruta"], nombre=j["nombre"],
        timestamp=None if j["timestamp"] is None else datetime.datetime.fromisoformat(j["timestamp"]),
        modelo=j["modelo"], yaw=j["yaw"],
        gps=None if j["gps"] is None else (j["gps"][0], j["gps"][1]),
        posicion=None if pos is None else _Posicion(**pos),
        meta_leida=j["meta_leida"], make=j["make"], make_ilegible=j["make_ilegible"],
        a_medias=j["a_medias"], pitch=j["pitch"], flight_yaw=j["flight_yaw"],
        jpeg_sin_eoi=j["jpeg_sin_eoi"])


# ---- escritura / lectura de parciales ----------------------------------------

def _escribir_atomico(ruta: str, contenido: bytes, condicion_generacion: "int | None" = None) -> None:
    """Escribe `contenido` en `ruta` (local o gs://) de una pieza. En local,
    tmp + `os.replace`; en gs:// un objeto se sube entero o no existe."""
    if es_uri_gcs(ruta):
        descriptor, tmp = tempfile.mkstemp(suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as fh:
                fh.write(contenido)
            if condicion_generacion is None:
                publicar_en(tmp, ruta)
            else:
                publicar_en(tmp, ruta, if_generation_match=condicion_generacion)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(contenido)
    os.replace(tmp, ruta)


def escribir_parcial(destino: str, ejecucion: str, shard_index: int, shard_count: int,
                     imagenes: list[str], registros: list[dict]) -> str:
    """Escribe el parcial de esta task. `registros` va en el orden del tramo
    (uno por ruta de `imagenes[inicio:fin]`). Devuelve la ruta del parcial."""
    n = len(imagenes)
    inicio, fin = tramo(n, shard_index, shard_count)
    rutas_tramo = imagenes[inicio:fin]
    if [r["ruta"] for r in registros] != rutas_tramo:
        raise ErrorIndiceParcial(
            f"map-{shard_index}: los registros leídos no coinciden con su tramo del listado "
            f"({len(registros)} registros, {len(rutas_tramo)} rutas)")
    lineas = [{"tipo": "cabecera", "version": VERSION_PARCIAL, "ejecucion": str(ejecucion),
               "shard_index": shard_index, "shard_count": shard_count, "n_listado": n,
               "hash_listado": hash_listado(imagenes), "inicio": inicio, "fin": fin}]
    lineas.extend(registros)
    lineas.append({"tipo": "fin", "n": len(registros)})
    texto = "".join(json.dumps(l, ensure_ascii=False, allow_nan=True) + "\n" for l in lineas)
    # mtime=0: el gzip no lleva marca de tiempo (mismo contenido = mismos bytes).
    crudo = gzip.compress(texto.encode("utf-8"), mtime=0)
    ruta = ruta_parcial(destino, ejecucion, shard_index)
    _escribir_atomico(ruta, crudo)
    return ruta


def parciales_presentes(destino: str, ejecucion: str, shard_count: int) -> bool:
    """True si están los `shard_count` parciales `map-0 .. map-(N-1)`."""
    carpeta = carpeta_indice(destino, ejecucion)
    if not existe_ruta(carpeta):
        return False
    nombres = set(listar_ficheros(carpeta))
    return all(nombre_parcial(k) in nombres for k in range(shard_count))


def _leer_parcial(ruta: str) -> tuple[dict, list[dict]]:
    """(cabecera, registros) de un parcial, validando gzip, JSON y el pie."""
    try:
        with abrir_para_lectura(ruta) as local:
            with gzip.open(local, "rt", encoding="utf-8") as fh:
                lineas = [json.loads(l) for l in fh.read().splitlines() if l.strip()]
    except FileNotFoundError as exc:
        raise ErrorIndiceParcial(f"falta el parcial del índice: {ruta}") from exc
    except (OSError, EOFError, ValueError, UnicodeDecodeError) as exc:
        raise ErrorIndiceParcial(
            f"parcial del índice corrupto o truncado: {ruta} ({type(exc).__name__}: {exc})") from exc
    if len(lineas) < 2 or lineas[0].get("tipo") != "cabecera" or lineas[-1].get("tipo") != "fin":
        raise ErrorIndiceParcial(f"parcial del índice sin cabecera o sin pie: {ruta}")
    cabecera, pie, registros = lineas[0], lineas[-1], lineas[1:-1]
    if cabecera.get("version") != VERSION_PARCIAL:
        raise ErrorIndiceParcial(
            f"parcial {ruta}: versión {cabecera.get('version')!r}, se esperaba {VERSION_PARCIAL}")
    if pie.get("n") != len(registros):
        raise ErrorIndiceParcial(
            f"parcial {ruta}: el pie dice {pie.get('n')} registros y hay {len(registros)}")
    for r in registros:
        if r.get("estado") not in ESTADOS or "ruta" not in r:
            raise ErrorIndiceParcial(f"parcial {ruta}: registro inválido {str(r)[:200]}")
        if (r["estado"] == "ok") != (r.get("dato") is not None):
            raise ErrorIndiceParcial(f"parcial {ruta}: estado/dato incoherentes en {r['ruta']}")
    return cabecera, registros


def shard_count_de(destino: str, ejecucion: str) -> int:
    """N con el que se repartió una ejecución, leído de `map-0` (para el reduce
    manual, que no sabe N)."""
    cabecera, _ = _leer_parcial(ruta_parcial(destino, ejecucion, 0))
    return int(cabecera["shard_count"])


def cargar_parciales(destino: str, ejecucion: str, shard_count: int,
                     imagenes: list[str]) -> list[dict]:
    """Une los `shard_count` parciales y devuelve todos los registros EN ORDEN
    DE LISTADO. Valida que cada parcial es de esta ejecución y de este reparto,
    que se calculó sobre el MISMO listado que `imagenes` (hash) y que cubre
    exactamente su tramo: cualquier desviación = `ErrorIndiceParcial`."""
    n = len(imagenes)
    huella = hash_listado(imagenes)
    union: list[dict] = []
    for k in range(shard_count):
        ruta = ruta_parcial(destino, ejecucion, k)
        cabecera, registros = _leer_parcial(ruta)
        esperado = {"ejecucion": str(ejecucion), "shard_index": k, "shard_count": shard_count,
                    "n_listado": n, "hash_listado": huella}
        for campo, valor in esperado.items():
            if cabecera.get(campo) != valor:
                raise ErrorIndiceParcial(
                    f"parcial {ruta}: {campo}={cabecera.get(campo)!r}, se esperaba {valor!r} "
                    "(parcial de otro reparto, o el origen cambió entre el map y el reduce)")
        inicio, fin = tramo(n, k, shard_count)
        if (cabecera.get("inicio"), cabecera.get("fin")) != (inicio, fin):
            raise ErrorIndiceParcial(
                f"parcial {ruta}: tramo {cabecera.get('inicio')}-{cabecera.get('fin')}, "
                f"se esperaba {inicio}-{fin}")
        if [r["ruta"] for r in registros] != imagenes[inicio:fin]:
            raise ErrorIndiceParcial(
                f"parcial {ruta}: sus rutas no coinciden con el tramo {inicio}-{fin} del listado")
        union.extend(registros)
    return union


# ---- resumen del índice (resumen.json) ---------------------------------------

VERSION_RESUMEN = 1
NOMBRE_RESUMEN = "resumen.json"
MAX_EJEMPLOS_FUERA_DE_ORDEN = 5
MOTIVO_SIN_CLASIFICAR = "sin-clasificar"


def ruta_resumen(destino: str, ejecucion: str) -> str:
    return unir(carpeta_indice(destino, ejecucion), NOMBRE_RESUMEN)


def construir_resumen_indice(imagenes: list[str], asignadas: list[tuple[str, "str | None", "str | None"]],
                             fuera_de_orden: list[tuple[str, "str | None"]],
                             relativa=lambda ruta: ruta,
                             tipos: "list[str] | None" = None) -> dict:
    """Contenido de `resumen.json` (sin `version` ni `ejecucion_indice`).

    `imagenes`: el listado completo (de él sale `hash_listado`). `asignadas`:
    `(ruta, pb, vuelo)` de cada imagen indexada (`pb`/`vuelo` None = SIN_ORDENAR).
    `fuera_de_orden`: `(ruta, motivo)` de toda imagen que no entra en un vuelo
    (SIN_ORDENAR y las omitidas del índice); motivo vacío = `sin-clasificar`.
    `relativa`: ruta absoluta -> ruta relativa al origen (para los ejemplos).
    Un par = idx DJI con T y W en el mismo vuelo y carpeta de origen.
    `tipos`: `fila.tipo` de cada imagen indexada; con el se escribe
    `n_por_tipo` = `{"rgb": n, "termica": n}` (rgb incluye RGB_Extra). Es lo que
    la Suite usa para dimensionar el apply. Sin `tipos` la clave no se escribe."""
    from atom_core import pares as pares_mod  # noqa: PLC0415 - evita ciclo

    por_vuelo: dict[tuple[str, str], list[str]] = {}
    n_sin_ordenar = 0
    for ruta, pb, vuelo in asignadas:
        if pb is None or vuelo is None:
            n_sin_ordenar += 1
        else:
            por_vuelo.setdefault((str(pb), str(vuelo)), []).append(ruta)

    def _pares(rutas: list[str]) -> int:
        t, w = set(), set()
        for ruta in rutas:
            clave = pares_mod.clave_pareja(ruta)
            if clave:
                (t if clave[1] == "T" else w).add((os.path.dirname(ruta), clave[0]))
        return len(t & w)

    pb = [{"pb": k[0], "vuelo": k[1], "imagenes": len(rutas), "pares": _pares(rutas)}
          for k, rutas in sorted(por_vuelo.items())]
    por_motivo: dict[str, list[str]] = {}
    for ruta, motivo in fuera_de_orden:
        por_motivo.setdefault((motivo or "").strip() or MOTIVO_SIN_CLASIFICAR, []).append(ruta)
    n_por_tipo = None
    if tipos is not None:
        desconocidos = {t for t in tipos if t != "TERMICA" and t not in ("RGB", "RGB_Extra")}
        if desconocidos:
            raise ErrorIndiceParcial(f"resumen del índice: tipo(s) desconocido(s) {sorted(desconocidos)}")
        n_termica = sum(1 for t in tipos if t == "TERMICA")
        n_por_tipo = {"rgb": len(tipos) - n_termica, "termica": n_termica}
    resumen = {
        "hash_listado": hash_listado(imagenes),
        "n_imagenes": len(imagenes),
        "n_pares": sum(e["pares"] for e in pb),
        "n_pb": len({e["pb"] for e in pb}),
        "n_vuelos": len(pb),
        "n_sin_ordenar": n_sin_ordenar,
        "pb": pb,
        "fuera_de_orden": [
            {"motivo": motivo, "n": len(rutas),
             "ejemplos": [relativa(r) for r in sorted(rutas)[:MAX_EJEMPLOS_FUERA_DE_ORDEN]]}
            for motivo, rutas in sorted(por_motivo.items())],
    }
    if n_por_tipo is not None:
        resumen["n_por_tipo"] = n_por_tipo
    return resumen


def escribir_resumen_indice(destino: str, ejecucion: str, indice: dict) -> str:
    """Escribe `<destino>/.organizado/indice/<ejecucion>/resumen.json` (local o
    gs://, de una pieza) con `indice` (ver `construir_resumen_indice`) y devuelve
    su ruta. Es el MISMO punto para N=1 y para el reduce."""
    if not str(ejecucion).strip():
        raise ErrorIndiceParcial("resumen del índice: falta la ejecución")
    contenido = {"version": VERSION_RESUMEN, "ejecucion_indice": str(ejecucion), **indice}
    ruta = ruta_resumen(destino, ejecucion)
    _escribir_atomico(ruta, (json.dumps(contenido, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return ruta


# ---- lock del reduce ---------------------------------------------------------

def _es_precondicion_fallida(error: BaseException) -> bool:
    return getattr(error, "code", None) == 412 or type(error).__name__ == "PreconditionFailed"


def _dueno_del_lock(ruta: str) -> "int | None":
    try:
        with abrir_para_lectura(ruta) as local:
            with open(local, "r", encoding="utf-8") as fh:
                return int(json.loads(fh.read())["shard_index"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def adquirir_lock_reduce(destino: str, ejecucion: str, shard_index: int) -> bool:
    """Intenta ser quien hace el reduce. gs://: subida con `ifGenerationMatch=0`
    (solo gana si el objeto no existe); local: `O_CREAT|O_EXCL`. Pierde si el lock
    es de otra task. Si el lock ya es de ESTA task (reintento de una task que
    ganó y murió a mitad del reduce) se vuelve a ganar, para que el reduce no
    quede huérfano. Cualquier otro error se propaga."""
    ruta = ruta_lock(destino, ejecucion)
    cuerpo = json.dumps({"shard_index": int(shard_index)}).encode("utf-8")
    if es_uri_gcs(ruta):
        try:
            _escribir_atomico(ruta, cuerpo, condicion_generacion=0)
            return True
        except Exception as exc:  # noqa: BLE001 - solo se traga la precondición fallida
            if not _es_precondicion_fallida(exc):
                raise
    else:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        try:
            descriptor = os.open(ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as fh:
                fh.write(cuerpo)
            return True
    return _dueno_del_lock(ruta) == int(shard_index)
