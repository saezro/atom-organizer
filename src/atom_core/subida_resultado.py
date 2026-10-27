"""Subida del RESULTADO organizado (salida local) a `gs://plantas_pv_nl/...`.

Fase 1 de «el Organizer sube el resultado al bucket». Reutiliza `cloud_upload`
(XML resumable, MD5, 16 hilos, `ifGenerationMatch=0`); este módulo pone lo
específico: qué ficheros suben y en qué orden (urgencia / normal), qué se puede
sobrescribir, cómo se detectan conflictos y cuándo se dispara solo.

Las reglas de sobrescritura REPLICAN las de la Suite
(`lib/organizer-resultado.js::SUBPREFIJOS_SOBRESCRIBIBLES`): si cambian allí,
cambian aquí. El token que emite la Suite solo permite sobrescribir en `CSVs/`,
`ESTADILLOS/` e `INDICE_*`; el resto da 403, no «pisa en silencio».
"""
from __future__ import annotations

import logging
import sqlite3
import time
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import cloud_upload as cu
from .manifiesto import NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO

logger = logging.getLogger(__name__)

BUCKET_RESULTADO = "plantas_pv_nl"
AMBITO_RESULTADO = "resultado"

MODO_URGENCIA = "urgencia"
MODO_NORMAL = "normal"
MODOS = (MODO_URGENCIA, MODO_NORMAL)

URGENTE = "urgente"
NORMAL = "normal"

NOMBRE_SIN_ORDENAR = "SIN_ORDENAR"
NOMBRE_MANIFIESTO_SUBIDA = "subida_resultado.json"

# Carpetas que nunca se suben ni cuentan como «sin clasificar».
_EXCLUIDAS = {NOMBRE_CARPETA_MANIFIESTO.lower(), "logs"}

# Prefijos (relativos a la salida) donde SE PUEDE sobrescribir. Igual que la Suite.
_SOBRESCRIBIBLES = ("CSVs/", "ESTADILLOS/", "INDICE_")


def es_sobrescribible(rel: str) -> bool:
    return rel.startswith(_SOBRESCRIBIBLES)


def clasificar(rel: str) -> str | None:
    """`URGENTE` / `NORMAL` / `None` para una ruta relativa POSIX de la salida.

    `None` = ni se sube ni se ignora en silencio: `seleccionar` lo cuenta como
    «sin clasificar» y lo avisa. Los `SIN_ORDENAR/` seleccionados por manifiesto
    NO pasan por aquí (los trata `seleccionar`).
    """
    partes = rel.split("/")
    top, nombre = partes[0], partes[-1]
    bajo = nombre.lower()
    if top.lower() in _EXCLUIDAS:
        return None
    if top == "TERMICA" and len(partes) >= 2:
        if bajo.endswith("_t.tiff"):
            return URGENTE
        if bajo.endswith("_t.jpg"):
            return NORMAL
    elif top == "RGB" and len(partes) >= 2:
        if bajo.endswith(("_w_crop.jpg", "_v_crop.jpg")):
            return URGENTE
        if bajo.endswith(("_w.jpg", "_v.jpg")):
            return NORMAL
    elif top == "CSVs":
        if len(partes) == 2 and (bajo.endswith("_meta.csv") or bajo.endswith("_location.csv")):
            return URGENTE
        if len(partes) == 3 and partes[1] == "_criterio":
            return NORMAL
    elif top == "ESTADILLOS" and len(partes) == 2:
        return URGENTE
    elif len(partes) == 1 and nombre.startswith("INDICE_") and bajo.endswith(".xlsx"):
        return NORMAL
    return None


def sin_ordenar_ultima_ejecucion(destino: Path) -> tuple[set[str], list[str]]:
    """Rutas relativas (bajo `SIN_ORDENAR/`) de las imágenes que, según el estado
    ACTUAL del manifiesto (de cualquier ejecución), siguen sin asignar (original,
    `_CROP` y TIFF) y se escribieron bien. Una fila `hecho` conserva su
    `ejecucion_id` antiguo aunque siga sin asignar, por eso no se filtra por ejecución.
    El nombre se mantiene por compatibilidad con los consumidores (Task 4).

    Sin manifiesto o ilegible NO se inventa nada: se devuelve vacío + un aviso que
    la UI enseña (sin fallbacks silenciosos).
    """
    destino = Path(destino)
    ruta = destino / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    if not ruta.is_file():
        return set(), ["Sin manifiesto del organizado: no se pueden subir las imágenes sin asignar."]
    filas: list = []
    try:
        con = sqlite3.connect(f"{ruta.as_uri()}?mode=ro&immutable=1", uri=True, timeout=5.0)
        try:
            filas = con.execute(
                "SELECT ruta_salida_original, ruta_salida_crop, ruta_salida_tiff FROM imagenes "
                "WHERE unassigned = 1 AND estado = 'hecho'").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return set(), [f"No se pudo leer el manifiesto ({exc}): no se suben las imágenes sin asignar."]
    base = destino.resolve()
    rels: set[str] = set()
    for fila in filas:
        for ruta_s in fila:
            if not ruta_s:
                continue
            try:
                rel = Path(ruta_s).resolve().relative_to(base).as_posix()
            except (ValueError, OSError):
                continue  # fuera de la salida (gs://, otro disco): no es de esta carpeta
            if rel.split("/")[0] == NOMBRE_SIN_ORDENAR and (base / rel).is_file():
                rels.add(rel)
    if filas and not rels:
        return rels, ["El manifiesto tiene imágenes sin asignar pero ninguna está bajo esta carpeta "
                      "(¿se movió el destino o cambió de unidad?): no se suben."]
    return rels, []


@dataclass
class Seleccion:
    urgentes: list = field(default_factory=list)
    resto: list = field(default_factory=list)
    sin_clasificar: int = 0
    avisos: list = field(default_factory=list)


def seleccionar(destino, prefijo: str) -> Seleccion:
    """Recorre la salida con `build_plan` (sin filtro de extensiones) y reparte."""
    destino = Path(destino)
    plan = cu.build_plan(destino, prefijo, suffixes=())  # () = sin filtro; ya salta `.organizado`
    sin_ord, avisos = sin_ordenar_ultima_ejecucion(destino)
    sel = Seleccion(avisos=list(avisos))
    for item in plan.items:
        rel = item.local.relative_to(destino).as_posix()
        top = rel.split("/")[0]
        if rel in sin_ord:
            sel.resto.append(item)
            continue
        if top.lower() in _EXCLUIDAS or top == NOMBRE_SIN_ORDENAR:
            continue
        clase = clasificar(rel)
        if clase == URGENTE:
            sel.urgentes.append(item)
        elif clase == NORMAL:
            sel.resto.append(item)
        else:
            sel.sin_clasificar += 1
            logger.warning("resultado: sin clasificar %s", rel)
    if sel.sin_clasificar:
        sel.avisos.append(f"{sel.sin_clasificar} fichero(s) sin clasificar: no se suben (ver log).")
    return sel


# ---------------------------------------------------------------------------
# Parte 2: partición, proveedor y subida
# ---------------------------------------------------------------------------

MAX_PAGINAS_RESULTADO = 2000


class SubidaResultadoError(RuntimeError):
    """Fallo de la subida del resultado que la UI debe enseñar tal cual."""


@dataclass
class Particion:
    pendientes: list = field(default_factory=list)
    hechos: list = field(default_factory=list)
    conflictos: list = field(default_factory=list)  # [(remote, motivo)]


def _necesita_md5(item: cu.UploadItem, remoto: cu.RemoteObject, manifest: cu.Manifest) -> bool:
    """¿Hace falta leer el fichero del disco para decidir «mismo contenido»?"""
    if remoto.size != item.size or not remoto.md5:
        return False
    return not (manifest.is_done(item) and manifest.md5_de(item) == remoto.md5)


def _mismo_contenido(item: cu.UploadItem, remoto: cu.RemoteObject, manifest: cu.Manifest,
                     md5_local: str | None = None) -> bool:
    """¿El objeto del bucket ES este fichero? Tamaño primero (gratis), luego MD5.

    Evita releer el disco: si el manifiesto recuerda el MD5 de cuando se subió y el
    fichero no ha cambiado (tamaño+mtime), basta compararlo con el del bucket. Un
    objeto sin `md5Hash` (compuesto) solo se puede comparar por tamaño.
    `md5_local` es el MD5 ya calculado (en paralelo) por `particionar`."""
    if remoto.size != item.size:
        return False
    if not remoto.md5:
        return True
    if manifest.is_done(item) and manifest.md5_de(item) == remoto.md5:
        return True
    if md5_local is None:
        md5_local = cu._file_md5_b64(item.local)
    return md5_local == remoto.md5


def _calcular_md5s(items, remotos: dict, manifest: cu.Manifest, concurrency: int,
                   on_progreso: Callable[[int, int], None] | None) -> dict:
    """MD5 en paralelo de los ficheros que lo necesitan. Clave: `item.remote`.

    Un error de lectura se propaga (no se da por «distinto» ni «igual» en silencio).
    El progreso se emite como mucho una vez por segundo y siempre al terminar."""
    todo = [it for it in items
            if (r := remotos.get(it.remote)) is not None and _necesita_md5(it, r, manifest)]
    md5s: dict[str, str] = {}
    if not todo:
        return md5s
    total = len(todo)
    ultimo = 0.0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for n, (it, md5) in enumerate(
                zip(todo, pool.map(lambda i: cu._file_md5_b64(i.local), todo)), 1):
            md5s[it.remote] = md5
            if on_progreso is not None:
                ahora = time.monotonic()
                if n == total or ahora - ultimo >= 1.0:
                    ultimo = ahora
                    on_progreso(n, total)
    return md5s


def particionar(items, remotos: dict, manifest: cu.Manifest, prefijo: str, *,
                concurrency: int = cu.DEFAULT_CONCURRENCY,
                on_progreso: Callable[[int, int], None] | None = None) -> Particion:
    """Parte `items` en pendientes / hechos / conflictos cruzando local y bucket.

    - No existe en el bucket → pendiente.
    - Mismo contenido → hecho (no se resube nada).
    - Contenido distinto: si es sobrescribible (`CSVs/`, `ESTADILLOS/`, `INDICE_*`) →
      pendiente con `sobrescribir=True`; si no → CONFLICTO, nunca se pisa.

    Los MD5 que haga falta calcular se calculan en paralelo (`concurrency` hilos);
    `on_progreso(hechos, total)` se llama durante esa fase (≤1/s).
    """
    items = list(items)
    pref = prefijo.strip("/") + "/"
    p = Particion()
    md5s = _calcular_md5s(items, remotos, manifest, concurrency, on_progreso)
    for it in items:
        remoto = remotos.get(it.remote)
        if remoto is None:
            p.pendientes.append(it)
        elif _mismo_contenido(it, remoto, manifest, md5s.get(it.remote)):
            p.hechos.append(it)
        elif es_sobrescribible(it.remote[len(pref):]):
            p.pendientes.append(replace(it, sobrescribir=True))
        else:
            p.conflictos.append((it.remote, "ya existe en el bucket con otro contenido (no se sobrescribe)"))
    return p


MOTIVO_SIN_CREDENCIAL = ("Subir el resultado al bucket exige entrar con Google o con usuario "
                         "y contraseña de ATOM Suite.")
MOTIVO_SIN_CREDENCIAL_BROKER = ("Subir el resultado no se puede hacer con el emparejamiento de la Pi/broker: "
                                "entra con Google o con usuario y contraseña de ATOM Suite.")


def credencial_resultado(auth) -> dict:
    """`{ok, tipo:'google'|'password'|None, usuario, reason}`. Sin red."""
    if auth is None:
        return {"ok": False, "tipo": None, "usuario": None,
                "reason": "No hay sesión iniciada: entra con Google o con usuario y contraseña."}
    if getattr(auth, "es_password", False):
        tipo = "password"
    elif getattr(auth, "es_google", False):
        tipo = "google"
    else:
        reason = (MOTIVO_SIN_CREDENCIAL_BROKER if getattr(auth, "es_broker", False)
                  else "No hay sesión iniciada: entra con Google o con usuario y contraseña.")
        return {"ok": False, "tipo": None, "usuario": None, "reason": reason}
    ident = getattr(auth, "identity", None)
    usuario = getattr(ident, "email", None) or getattr(ident, "nombre", None)
    return {"ok": True, "tipo": tipo, "usuario": usuario, "reason": ""}


class ProveedorResultado(cu.GcsOAuthProvider):
    """`GcsOAuthProvider` hacia `plantas_pv_nl` con el token `ambito:'resultado'`.

    A diferencia del modo password «crudo», este token SÍ puede sobrescribir en las
    carpetas permitidas (regla objectAdmin de la Suite), así que `solo_crear` es False:
    `reconciliar` no rechazará los items con `sobrescribir=True`.
    """

    solo_crear = False

    def __init__(self, auth, inspeccion_id: int):
        if not (getattr(auth, "es_password", False) or getattr(auth, "es_google", False)):
            raise SubidaResultadoError(MOTIVO_SIN_CREDENCIAL_BROKER if getattr(auth, "es_broker", False)
                                       else MOTIVO_SIN_CREDENCIAL)
        super().__init__(BUCKET_RESULTADO, auth, inspeccion_id=int(inspeccion_id), ambito=AMBITO_RESULTADO)

    def listar_remotos(self, prefix: str):
        """Inventario SIN el tope de 50.000 de los crudos: 2.000 páginas (2 M de
        objetos). Si aun así se trunca lanza `ListadoTruncado` (no verificable)."""
        return cu.listar_objetos_remotos(
            self.bucket, prefix, self.auth, base=self.base,
            token_prefix=self.prefix or prefix, inspeccion_id=self.inspeccion_id,
            planta_id=self.planta_id, ambito=self.ambito,
            max_paginas=MAX_PAGINAS_RESULTADO, estricto=True)

    def prefijo_destino(self) -> str:
        """Prefijo decidido por la Suite (pide el token si aún no lo hay)."""
        return self.auth.prefijo_resultado(self.inspeccion_id)


_RE_VUELO = re.compile(r"^PB\d+_V\d+", re.IGNORECASE)


def tipo_de(rel: str) -> str:
    """Tipo legible de un fichero del resultado (para las métricas de la subida)."""
    partes = rel.split("/")
    top, bajo = partes[0], partes[-1].lower()
    if top == "TERMICA":
        return "tiff_termica" if bajo.endswith(".tiff") else "termica_jpg"
    if top == "RGB":
        return "rgb_crops" if "_crop" in bajo else "rgb"
    if top == "CSVs":
        return "csv"
    if top == "ESTADILLOS":
        return "estadillos"
    return "otros"


def _vuelos_de(rels) -> set:
    return {p for r in rels for p in r.split("/") if _RE_VUELO.match(p)}


def _metricas_grupo(items, prefijo: str) -> dict:
    """Ficheros/bytes por tipo y nº de vuelos (carpetas PBx_Vy) de una lista de items."""
    pref = prefijo.strip("/") + "/"
    rels = [it.remote[len(pref):] if it.remote.startswith(pref) else it.remote for it in items]
    por_tipo: dict = {}
    for r, it in zip(rels, items):
        d = por_tipo.setdefault(tipo_de(r), {"ficheros": 0, "bytes": 0})
        d["ficheros"] += 1
        d["bytes"] += max(0, it.size)
    return {"por_tipo": por_tipo, "vuelos": len(_vuelos_de(rels))}


@dataclass
class Contador:
    total: int = 0
    hechos: int = 0
    pendientes: int = 0


@dataclass
class ResumenResultado:
    modo: str
    prefijo: str
    urgentes: Contador = field(default_factory=Contador)
    resto: Contador = field(default_factory=Contador)
    conflictos: list = field(default_factory=list)
    fallidas: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    sin_clasificar: int = 0
    cancelado: bool = False
    simulado: bool = False
    # Objetos que `upload_plan` devolvió como "cancelado" SIN que `should_stop`
    # lo pidiera (subida cortada por otra causa): no son `fallidas`, pero faltan.
    cancelados: list = field(default_factory=list)
    # Verificación fresca contra el bucket tras subir: lo que falta o difiere
    # (`[(remote, motivo)]`) y si se pudo comprobar (False = no se pudo listar).
    faltantes: list = field(default_factory=list)
    verificado: bool = True
    # El listado del bucket no cabió (truncado): ni ok ni re-encolable.
    truncado: bool = False
    # Métricas de la pasada: {duracion_s, urgentes|resto: {subidos, omitidos, pendientes,
    # bytes_subidos, bytes_omitidos, por_tipo, vuelos}, conflictos_n}.
    metricas: dict = field(default_factory=dict)

    @property
    def incompleta(self) -> bool:
        """Faltan objetos en el bucket o no se pudo comprobar que estén."""
        return bool(self.faltantes or self.cancelados or not self.verificado or self.truncado)

    @property
    def ok(self) -> bool:
        return not (self.cancelado or self.fallidas or self.conflictos or self.incompleta)

    def motivo_incompleto(self) -> str:
        """Texto para log/UI/telemetría: cuántos faltan y unos ejemplos."""
        if self.truncado:
            return ("El bucket tiene demasiados objetos para comprobarlo (listado truncado): "
                    "la subida no es verificable.")
        if not self.verificado:
            return "No se pudo comprobar el bucket tras subir: no se da la subida por buena."
        fall = {f[0] for f in self.fallidas}
        total = {f[0] for f in self.faltantes} | set(self.cancelados) | fall
        n = len(total)
        if n == 0:
            return "Subida no completada."
        ej = "; ".join(f"{r}: {m}" for r, m in self.faltantes[:3])
        if fall:
            # Con fallidas la subida se corta: lo demás ni se intentó.
            desglose = f"{len(fall)} fallidos, {len(total - fall)} sin intentar"
        else:
            desglose = f"{n} faltan en el bucket"
        if self.cancelados:
            desglose += f", {len(self.cancelados)} cortados (cancelado)"
        return f"{n} objetos sin subir ({desglose}{'; ' + ej if ej else ''})."

    def a_dict(self) -> dict:
        return {
            "ok": self.ok, "cancelled": self.cancelado, "simulado": self.simulado,
            "modo": self.modo, "prefix": self.prefijo,
            "urgentes": {"hechos": self.urgentes.hechos, "total": self.urgentes.total},
            "resto": {"hechos": self.resto.hechos, "total": self.resto.total},
            "conflictos": [list(c) for c in self.conflictos],
            "fallidas": [list(f) for f in self.fallidas],
            "avisos": list(self.avisos), "sin_clasificar": self.sin_clasificar,
            "faltantes": len({f[0] for f in self.faltantes} | set(self.cancelados)),
            "faltantes_ejemplos": [list(f) for f in self.faltantes[:5]],
            "verificado": self.verificado, "truncado": self.truncado,
            "metricas": self.metricas,
        }


def subir_resultado(destino, inspeccion_id: int, modo: str, auth, *, proveedor=None,
                    on_estado: Callable[[dict], None] | None = None,
                    should_stop: Callable[[], bool] | None = None,
                    concurrency: int = cu.DEFAULT_CONCURRENCY, simular: bool = False,
                    subir=cu.upload_plan) -> ResumenResultado:
    """Sube la salida organizada. Urgencia = solo urgentes; normal = urgentes y luego resto.

    Una sola cola: urgentes primero. Lo que el bucket ya tiene (mismo contenido) se
    salta, así que «normal» tras «urgencia» no resube nada. Si los urgentes tienen
    fallidas el resto NO se intenta (mejor un fallo claro que horas de reintentos).
    """
    if modo not in MODOS:
        raise ValueError(f"modo desconocido: {modo!r}")
    destino = Path(destino)
    if not destino.is_dir():
        raise SubidaResultadoError("La carpeta de salida no existe.")
    proveedor = proveedor or ProveedorResultado(auth, inspeccion_id)
    prefijo = proveedor.prefijo_destino()
    sel = seleccionar(destino, prefijo)
    (destino / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True, exist_ok=True)
    manifest = cu.Manifest(destino / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_MANIFIESTO_SUBIDA)

    resumen = ResumenResultado(modo=modo, prefijo=prefijo, avisos=list(sel.avisos),
                               sin_clasificar=sel.sin_clasificar, simulado=simular)
    resumen.urgentes.total = len(sel.urgentes)
    resumen.resto.total = len(sel.resto)

    bytes_total = sum(it.size for it in sel.urgentes) + (
        sum(it.size for it in sel.resto) if modo == MODO_NORMAL else 0)
    t0 = time.monotonic()
    bytes_ya = [0]  # bytes ya presentes en el bucket (grupos particionados)

    def emitir(fase: str, s: dict | None = None, comprobando: tuple | None = None) -> None:
        if on_estado is None:
            return
        on_estado({
            "comprobando": ({"hechos": comprobando[0], "total": comprobando[1]}
                            if comprobando else None),
            "modo": modo, "fase": fase,
            "urgentes": {"hechos": resumen.urgentes.hechos, "total": resumen.urgentes.total},
            "resto": {"hechos": resumen.resto.hechos, "total": resumen.resto.total},
            "mbps": (s or {}).get("mbps"), "eta": (s or {}).get("eta"),
            "files_per_s": (s or {}).get("files_per_s"),
            # Bytes de TODO lo del modo (ya en el bucket + lo que se va subiendo).
            "bytes_done": bytes_ya[0] + int((s or {}).get("bytes_done", 0)),
            "bytes_total": bytes_total,
            # Destino real (se conoce antes de listar): la UI lo enseña desde el principio.
            "prefix": prefijo,
        })

    emitir("listando")
    try:
        remotos = proveedor.listar_remotos(prefijo)
    except cu.ListadoTruncado as exc:
        raise SubidaResultadoError(f"{exc}: no se sube nada.") from exc
    if remotos is None:
        raise SubidaResultadoError("No se pudo consultar el contenido del bucket: no se sube nada.")

    grupos = [("urgentes", sel.urgentes)]
    if modo == MODO_NORMAL:
        grupos.append(("resto", sel.resto))
    try:
        for nombre, items in grupos:
            if should_stop is not None and should_stop():
                resumen.cancelado = True
                break
            cont = getattr(resumen, nombre)
            part = particionar(
                items, remotos, manifest, prefijo, concurrency=concurrency,
                on_progreso=lambda h, t, nombre=nombre: emitir(nombre, None, (h, t)))
            cont.hechos = len(part.hechos)
            cont.pendientes = len(part.pendientes)
            resumen.conflictos += part.conflictos
            bytes_ya[0] += sum(it.size for it in part.hechos)
            resumen.metricas[nombre] = {
                "omitidos": len(part.hechos), "pendientes": len(part.pendientes),
                "bytes_omitidos": sum(it.size for it in part.hechos),
                "bytes_pendientes": sum(it.size for it in part.pendientes),
                "subidos": 0, "bytes_subidos": 0,
                **_metricas_grupo(items, prefijo),
            }
            emitir(nombre)
            if simular or not part.pendientes:
                continue
            base = cont.hechos
            plan = cu.UploadPlan(root=destino, items=part.pendientes, prefix=prefijo.strip("/"))

            def _stats(s: dict, cont=cont, base=base, nombre=nombre) -> None:
                cont.hechos = base + int(s.get("files_done", 0))
                emitir(nombre, s)

            # `remotos={}`: la partición ya cruzó local y bucket. Con {} `reconciliar` deja los
            # items tal cual (conserva `sobrescribir`) y no vuelve a listar.
            res = subir(plan, proveedor, concurrency=concurrency, manifest=manifest,
                        on_stats=_stats, should_stop=should_stop, remotos={})
            cont.hechos = base + res.uploaded + res.skipped
            fall = {f[0] for f in res.failed}
            ok_items = [it for it in part.pendientes if it.remote not in fall]
            if res.uploaded or not fall:
                resumen.metricas[nombre]["subidos"] = int(res.uploaded)
                resumen.metricas[nombre]["bytes_subidos"] = int(getattr(res, "bytes_sent", 0) or
                                                               sum(it.size for it in ok_items))
            resumen.fallidas += [f for f in res.failed if f[1] != "cancelado"]
            if should_stop is not None and should_stop():
                resumen.cancelado = True
            else:
                # Cortada sin que nadie lo pidiera: antes se filtraban y quedaba ok.
                resumen.cancelados += [f[0] for f in res.failed if f[1] == "cancelado"]
            emitir(nombre)
            if resumen.cancelado or resumen.fallidas:
                break
    finally:
        resumen.metricas["duracion_s"] = round(time.monotonic() - t0, 1)
        resumen.metricas["conflictos_n"] = len(resumen.conflictos)
        manifest.flush()  # `subir` puede ser ajeno a `upload_plan`: flush final siempre

    # Verificación FRESCA contra el bucket: un `res.ok` solo dice que no hubo
    # excepciones. Se cruzan todos los items de los grupos de este modo (menos los
    # conflictos, que ya hacen la subida no-ok y a propósito no se suben).
    if not simular and not resumen.cancelado:
        conflictivos = {c[0] for c in resumen.conflictos}
        todos = [it for _n, items in grupos for it in items if it.remote not in conflictivos]
        try:
            frescos = proveedor.listar_remotos(prefijo)
        except cu.ListadoTruncado as exc:
            logger.warning("verificación del resultado: %s", exc)
            resumen.truncado = True
            return resumen
        except Exception:  # noqa: BLE001 - no poder comprobar = no ok
            frescos = None
        if frescos is None:
            resumen.verificado = False
        else:
            resumen.faltantes = cu.faltantes_en_remoto(todos, frescos)
    return resumen


def _desglose_barato(items, remotos: dict) -> dict:
    """Desglose por clave+tamaño (sin MD5, sin leer disco): `en_bucket` = existe con el
    mismo tamaño; `pendientes` = no existe; `distintos` = existe con otro tamaño
    (sobrescribible o conflicto, lo decide `particionar` al subir)."""
    d = {"total": 0, "en_bucket": 0, "pendientes": 0, "distintos": 0, "bytes_pendientes": 0}
    for it in items:
        d["total"] += 1
        r = remotos.get(it.remote)
        if r is None:
            d["pendientes"] += 1
            d["bytes_pendientes"] += max(0, it.size)
        elif r.size == it.size:
            d["en_bucket"] += 1
        else:
            d["distintos"] += 1
            d["bytes_pendientes"] += max(0, it.size)
    return d


def inspeccionar_destino(destino, inspeccion_id: int, auth, *, proveedor=None) -> dict:
    """Qué hay ya en el prefijo destino del bucket (solo LIST: sin MD5, sin escrituras).

    `sobrescribe` = rutas relativas (`CSVs/`, `ESTADILLOS/`, `INDICE_*`) que la salida local
    subiría y que ya existen en el bucket (candidatas a sobrescribirse). Un error de red o
    de credencial NO se silencia: `ok=False` con `error`.
    """
    base = {"ok": True, "existe": False, "n_objetos": 0, "bytes": 0, "sobrescribe": [],
            "prefijo": None, "error": None, "detalle": ""}
    try:
        proveedor = proveedor or ProveedorResultado(auth, inspeccion_id)
        prefijo = proveedor.prefijo_destino()
        base["prefijo"] = prefijo
        remotos = proveedor.listar_remotos(prefijo)
        if remotos is None:
            raise SubidaResultadoError("No se pudo consultar el contenido del bucket.")
        pref = prefijo.strip("/") + "/"
        base["n_objetos"] = len(remotos)
        base["bytes"] = sum(max(0, r.size) for r in remotos.values())
        base["existe"] = bool(remotos)
        sobre: list[str] = []
        destino = Path(destino)
        if destino.is_dir():
            sel = seleccionar(destino, prefijo)
            base["desglose"] = {"urgentes": _desglose_barato(sel.urgentes, remotos),
                                "resto": _desglose_barato(sel.resto, remotos)}
            for it in ([*sel.urgentes, *sel.resto] if remotos else []):
                rel = it.remote[len(pref):] if it.remote.startswith(pref) else it.remote
                if it.remote in remotos and es_sobrescribible(rel):
                    sobre.append(rel)
        base["sobrescribe"] = sorted(sobre)
        base["detalle"] = (f"El destino {prefijo} ya tiene {base['n_objetos']} objeto(s) "
                           f"({base['bytes']} bytes); {len(sobre)} se sobrescribirían."
                           if remotos else f"El destino {prefijo} está vacío.")
    except cu.ListadoTruncado as exc:
        base.update(ok=False, existe=None, error=str(exc), detalle=f"No verificable: {exc}")
    except Exception as exc:  # noqa: BLE001 - estado visible, nunca silencioso
        base.update(ok=False, existe=None, error=f"{type(exc).__name__}: {exc}",
                    detalle=f"No se pudo inspeccionar el destino: {exc}")
    return base


def decidir_automatica(task: str, params: dict, done: dict | None) -> tuple[bool, str]:
    """¿Hay que subir el resultado solo, al terminar un run? `(sube, motivo_de_aviso)`.

    Solo tras un `split_images` que terminó en `ok`/`warning`. Si procedería pero falta la
    inspección o la carpeta de salida, devuelve un motivo en español que la UI enseña:
    no subir sin decir por qué sería un fallback silencioso.
    """
    if task != "split_images" or (done or {}).get("status") not in ("ok", "warning"):
        return False, ""
    if params.get("subir_resultado") is False:
        return False, ""
    if not params.get("inspeccion_id"):
        return False, "No se sube el resultado al bucket: no hay inspección elegida."
    if not (params.get("destino") or params.get("output_folder") or "").strip():
        return False, "No se sube el resultado al bucket: falta la carpeta de salida."
    return True, ""
