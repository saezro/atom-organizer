"""Apply repartido entre N tasks (Cloud Run): deltas por shard en jsonl.

Cada task de la fase `apply` baja `manifiesto.db` SOLO para leer: se queda con
las filas pendientes que le tocan (`sharding.toca_imagen` sobre `ruta_origen`,
hash estable por imagen) y NO sube el manifiesto -N tasks subiendo el mismo
objeto se pisarian-. Lo que hace cada fila se anota en
`<destino>/.organizado/shards/<ejecucion>/<fase>-<shard_index>.jsonl`
(`fase` = apply|rgb|termica: `rgb` y `termica` pueden correr A LA VEZ como
ejecuciones separadas y nunca comparten fichero), una linea por
fila:

    {"id": 7, "estado": "hecho"|"fallido", "verificacion": "...",
     "motivo": "...", "ruta_origen": "..."}

El volcado reescribe el fichero entero cada ~50 filas o ~60 s y al final
(en `gs://` un objeto no se puede ampliar). La fase `cierre` funde todos los
jsonl en el manifiesto (`Manifiesto.aplicar_deltas`) y lo sube con generation.

Reanudacion (maxRetries=1): al arrancar, la task relee SU jsonl y salta las
filas ya `hecho` en el. Las `fallido` se reintentan.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from atom_core import sharding, simulacion
from atom_core.almacen import (
    abrir_para_lectura, es_uri_gcs, existe_ruta, listar_ficheros,
    listar_subcarpetas, publicar_en, unir,
)

logger = logging.getLogger(__name__)

NOMBRE_CARPETA_SHARDS = "shards"
FILAS_POR_VOLCADO = 50
SEGUNDOS_POR_VOLCADO = 60.0


def _env_coste(nombre: str, defecto: float) -> float:
    """Coste desde env; valor ilegible o no positivo = error (sin fallback mudo)."""
    bruto = os.environ.get(nombre)
    if bruto is None or not str(bruto).strip():
        return defecto
    try:
        valor = float(bruto)
    except ValueError:
        raise ValueError(f"{nombre}={bruto!r}: no es un numero") from None
    if not valor > 0:
        raise ValueError(f"{nombre}={bruto!r}: debe ser > 0")
    return valor


#: Coste estimado por fila en vCPU*s (medido; sobreescribible por env). Solo
#: importa la proporcion entre ambos para equilibrar las tasks.
COSTE_FILA_RGB = _env_coste("ATOM_COSTE_FILA_RGB", 0.50)
COSTE_FILA_TERMICA = _env_coste("ATOM_COSTE_FILA_TERMICA", 0.68)


def coste_fila(fila) -> float:
    """Coste de una fila segun su `tipo` (`TERMICA` o RGB/RGB_Extra). Un tipo
    desconocido o ausente es un error: no se adivina."""
    tipo = fila["tipo"]
    if tipo == "TERMICA":
        return COSTE_FILA_TERMICA
    if tipo in ("RGB", "RGB_Extra"):
        return COSTE_FILA_RGB
    raise ValueError(f"fila {fila['ruta_origen']!r}: tipo {tipo!r} sin coste conocido")


def reparto_por_coste(filas, shard_count: int) -> list[list]:
    """Reparto LPT determinista de `filas` en `shard_count` tasks: devuelve una
    lista por task con SUS filas.

    Ordena por `(coste desc, ruta_origen)` y da cada fila a la task con menos
    carga acumulada (empate: la de menor indice). Todas las tasks ejecutan este
    mismo calculo por separado y llegan a la misma particion; por eso hay que
    pasarle SIEMPRE todas las filas del manifiesto (no solo las pendientes): asi
    el reparto es estable entre reintentos. Dentro de cada task el orden
    intercala tipos (round-robin ponderado: se emite antes el tipo que va mas
    atrasado respecto a su cuota), de modo que rgb y termica avanzan a la vez."""
    k = int(shard_count)
    if k < 1:
        raise ValueError(f"shard_count debe ser >= 1 (llego {shard_count!r})")
    ordenadas = sorted(filas, key=lambda f: (-coste_fila(f), f["ruta_origen"], f["id"]))
    carga = [0.0] * k
    por_task: list[list] = [[] for _ in range(k)]
    for fila in ordenadas:
        destino = min(range(k), key=lambda s: (carga[s], s))
        carga[destino] += coste_fila(fila)
        por_task[destino].append(fila)
    return [_intercalar_tipos(lista) for lista in por_task]


def _intercalar_tipos(filas: list) -> list:
    """Round-robin ponderado por tipo, estable (orden previo dentro de cada tipo)."""
    colas: dict[str, list] = {}
    for f in filas:
        colas.setdefault(f["tipo"], []).append(f)
    if len(colas) <= 1:
        return list(filas)
    total = {t: len(c) for t, c in colas.items()}
    emitidas = {t: 0 for t in colas}
    salida = []
    for _ in range(len(filas)):
        # Gana el tipo con menor fraccion emitida (el mas atrasado); empate por nombre.
        t = min((t for t in colas if emitidas[t] < total[t]),
                key=lambda t: ((emitidas[t] + 1) / total[t], t))
        salida.append(colas[t][emitidas[t]])
        emitidas[t] += 1
    return salida


#: Hilos para leer los jsonl de deltas en el cierre.
HILOS_LECTURA_DELTAS = 16


def carpeta_shards(destino: str) -> str:
    return unir(destino, ".organizado", NOMBRE_CARPETA_SHARDS)


def ruta_deltas(destino: str, ejecucion: str, shard_index: int, fase: str = "apply") -> str:
    return unir(carpeta_shards(destino), str(ejecucion), f"{fase}-{int(shard_index)}.jsonl")


def _parsear(texto: str) -> list[dict]:
    """Lineas jsonl validas; una linea cortada (muerte a mitad de escritura) se
    descarta en silencio solo si es la ULTIMA, el resto se avisa."""
    entradas = []
    lineas = texto.splitlines()
    for i, linea in enumerate(lineas):
        if not linea.strip():
            continue
        try:
            dato = json.loads(linea)
        except ValueError:
            if i != len(lineas) - 1:
                logger.warning("deltas: linea %d ilegible, se ignora", i + 1)
            continue
        if isinstance(dato, dict) and "id" in dato and dato.get("estado") in ("hecho", "fallido"):
            entradas.append(dato)
    return entradas


def _leer_fichero(ruta: str) -> list[dict]:
    with abrir_para_lectura(ruta) as local:
        with open(local, "r", encoding="utf-8") as fh:
            return _parsear(fh.read())


class RegistroDeltas:
    """Deltas de UNA task: acumula, vuelca y se reanuda desde su propio jsonl."""

    def __init__(self, destino: str, ejecucion: str, shard_index: int, fase: str = "apply",
                 cada_filas: int = FILAS_POR_VOLCADO,
                 cada_segundos: float = SEGUNDOS_POR_VOLCADO, reloj=time.monotonic):
        if not str(ejecucion).strip():
            raise ValueError("la fase apply necesita un identificador de ejecucion "
                             "(CLOUD_RUN_EXECUTION o --ejecucion)")
        self.destino = destino
        self.fase = fase
        self.ruta = ruta_deltas(destino, ejecucion, shard_index, fase)
        self._cada_filas = max(1, int(cada_filas))
        self._cada_segundos = cada_segundos
        self._reloj = reloj
        self._lock = threading.Lock()
        self._entradas: dict[int, dict] = {}
        self._sin_volcar = 0
        self._ultimo = reloj()
        self.error_volcado: Exception | None = None
        # En simulacion no se reanuda de un jsonl previo: se mide el trabajo completo.
        if not simulacion.activa() and existe_ruta(self.ruta):
            for entrada in _leer_fichero(self.ruta):
                self._entradas[int(entrada["id"])] = entrada
        self.hechas = {i for i, e in self._entradas.items() if e["estado"] == "hecho"}

    def registrar_hecha(self, id_fila: int, ruta_origen: str, verificacion: str) -> None:
        self._registrar({"id": int(id_fila), "estado": "hecho", "verificacion": verificacion,
                         "motivo": None, "ruta_origen": ruta_origen})

    def registrar_fallida(self, id_fila: int, ruta_origen: str, motivo: str) -> None:
        self._registrar({"id": int(id_fila), "estado": "fallido", "verificacion": None,
                         "motivo": motivo, "ruta_origen": ruta_origen})

    def _registrar(self, entrada: dict) -> None:
        with self._lock:
            self._entradas[entrada["id"]] = entrada
            if entrada["estado"] == "hecho":
                self.hechas.add(entrada["id"])
            else:
                self.hechas.discard(entrada["id"])
            self._sin_volcar += 1
            if (self._sin_volcar >= self._cada_filas
                    or self._reloj() - self._ultimo >= self._cada_segundos):
                try:
                    self._volcar_sin_lock()
                except Exception as exc:  # noqa: BLE001 — no se pierde la fila en vuelo
                    # El cierre (`cerrar`) lo reintenta y SI lo propaga.
                    self.error_volcado = exc
                    logger.error("deltas: no se pudo volcar %s: %s", self.ruta, exc)

    def _volcar_sin_lock(self) -> None:
        texto = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self._entradas.values())
        if simulacion.interceptar("deltas_shard", self.ruta, len(texto.encode("utf-8"))):
            self._sin_volcar = 0
            self._ultimo = self._reloj()
            self.error_volcado = None
            return
        if es_uri_gcs(self.ruta):
            descriptor, tmp = tempfile.mkstemp(suffix=".jsonl")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
                    fh.write(texto)
                publicar_en(tmp, self.ruta)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        else:
            os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
            tmp = self.ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(texto)
            os.replace(tmp, self.ruta)
        self._sin_volcar = 0
        self._ultimo = self._reloj()
        self.error_volcado = None

    def resumen_simulado(self) -> dict:
        """Deltas que NO se escribieron en simulacion: recuentos y fallidas (max. 50)."""
        with self._lock:
            hechas = sum(1 for e in self._entradas.values() if e["estado"] == "hecho")
            fallidas = [e for e in self._entradas.values() if e["estado"] == "fallido"]
        return {"hechas": hechas, "fallidas": len(fallidas),
                "fallidas_detalle": [{"id": e["id"], "motivo": e["motivo"]} for e in fallidas[:50]]}

    def volcar(self) -> None:
        with self._lock:
            self._volcar_sin_lock()

    def cerrar(self) -> None:
        """Volcado final (siempre, tambien sin filas: prueba de que la task corrio).
        Si falla se propaga: la task acaba fallida y se relanza."""
        self.volcar()


class ManifiestoShard:
    """Vista de un `Manifiesto` LOCAL (copia descargada) para una task de apply.

    `pendientes()` solo devuelve lo que le toca a esta task y no esta ya `hecho`
    en su jsonl. `marcar_hecha`/`marcar_fallida` actualizan la copia local (el
    guard de la fase termica y los recuentos del marcador la leen) y anotan el
    delta; NO borran los ficheros viejos de SIN_ORDENAR: eso lo hace el cierre al
    fundir (`Manifiesto.marcar_hecha`), cuando ya han terminado todas las tasks.
    El resto de metodos se delega tal cual."""

    def __init__(self, manifiesto, registro: RegistroDeltas, shard_index: int, shard_count: int):
        self._m = manifiesto
        self._registro = registro
        self._indice = shard_index
        self._cuenta = shard_count
        self._rutas: dict[int, str] = {}
        # Fase `apply`: reparto por coste (LPT) sobre TODAS las filas del
        # manifiesto. `rgb`/`termica` (ejecuciones antiguas) conservan el hash.
        self._ids_mios: "dict[int, int] | None" = None
        if getattr(registro, "fase", None) == "apply":
            filas_mias = reparto_por_coste(self._m.todas(), shard_count)[shard_index]
            self._ids_mios = {int(f["id"]): n for n, f in enumerate(filas_mias)}
        # Lo ya hecho en una vida anterior de esta task: en la copia local figura
        # como hecho (si no, el guard de termicas lo daria por no procesado).
        for id_fila in sorted(registro.hechas):
            self._m._actualizar(id_fila, estado="hecho", motivo_fallo=None)

    def __getattr__(self, nombre):
        return getattr(self._m, nombre)

    def _mia(self, fila) -> bool:
        if self._ids_mios is not None:
            return int(fila["id"]) in self._ids_mios
        return sharding.toca_imagen(fila["ruta_origen"], self._indice, self._cuenta)

    def pendientes(self, limite=None, origen=None):
        filas = [f for f in self._m.pendientes(origen=origen)
                 if self._mia(f) and f["id"] not in self._registro.hechas]
        if self._ids_mios is not None:
            filas.sort(key=lambda f: self._ids_mios[int(f["id"])])
        for f in filas:
            self._rutas[f["id"]] = f["ruta_origen"]
        return filas if limite is None else filas[:int(limite)]

    def todas(self):
        # Pendiente de OTRA task no es "sin procesar" para esta.
        return [f for f in self._m.todas()
                if f["estado"] not in ("pendiente", "en_curso") or self._mia(f)]

    def _ruta_origen(self, id_fila: int) -> str:
        ruta = self._rutas.get(id_fila)
        if ruta is None:
            fila = self._m._conexion().execute(
                "SELECT ruta_origen FROM imagenes WHERE id = ?", (id_fila,)).fetchone()
            ruta = fila["ruta_origen"] if fila else ""
        return ruta

    def marcar_hecha(self, id_fila: int, verificacion: str) -> None:
        self._m._actualizar(id_fila, estado="hecho", verificacion=verificacion, motivo_fallo=None)
        self._registro.registrar_hecha(id_fila, self._ruta_origen(id_fila), verificacion)

    def marcar_fallida(self, id_fila: int, motivo: str) -> None:
        self._m.marcar_fallida(id_fila, motivo)
        self._registro.registrar_fallida(id_fila, self._ruta_origen(id_fila), motivo)


def leer_deltas(destino: str) -> list[dict]:
    """Todas las entradas de todos los jsonl de `<destino>/.organizado/shards/`
    (todas las ejecuciones y shards). Orden: ejecucion y shard ascendentes."""
    base = carpeta_shards(destino)
    if not existe_ruta(base):
        return []
    rutas: list[str] = []
    for ejecucion in sorted(listar_subcarpetas(base)):
        carpeta = unir(base, ejecucion)
        ficheros = sorted(f for f in listar_ficheros(carpeta) if f.endswith(".jsonl"))
        rutas.extend(unir(carpeta, nombre) for nombre in ficheros)
    if not rutas:
        return []
    # Lectura en hilos (E/S pura; en `gs://` cada fichero es una descarga). `map`
    # conserva el orden de `rutas`: mismo resultado que la lectura en serie.
    with ThreadPoolExecutor(max_workers=min(HILOS_LECTURA_DELTAS, len(rutas))) as pool:
        trozos = list(pool.map(_leer_fichero, rutas))
    return [e for trozo in trozos for e in trozo]


def resolver_deltas(entradas: list[dict]) -> dict[int, dict]:
    """Una entrada por id. Los nombres de ejecucion de Cloud Run no ordenan en el
    tiempo, asi que no vale "la ultima gana": `hecho` gana a `fallido` (un
    reintento posterior que salio bien no lo tapa un fallo anterior, y un fallo
    nunca pisa un exito)."""
    final: dict[int, dict] = {}
    for e in entradas:
        previa = final.get(int(e["id"]))
        if previa is None or (previa["estado"] == "fallido" and e["estado"] == "hecho"):
            final[int(e["id"])] = e
    return final
