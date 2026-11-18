"""Subida de un vuelo a un bucket de Cloud Storage.

Pieza cliente de la arquitectura "la app solo sube, el server organiza": en vez
de procesar las ~1300 imágenes en el PC del usuario, se suben en bruto a un
bucket y el procesado ocurre en Cloud Run.

Números que condicionan TODO el diseño (medidos sobre el vuelo PLANTA_B,
2026-08-05): un vuelo son **8,7 GB / 2530 ficheros** (2,1 GB de térmicas `_T` +
6,5 GB de RGB, y las RGB también se procesan, así que no se pueden descartar).
A 300 Mbps de subida eso son ~4 min; a 50 Mbps, ~23. De ahí que aquí importe
más la robustez de la transferencia que cualquier microoptimización:

  - **Resumable de verdad.** Un corte a los 8 GB no puede costar empezar de
    cero. Se usa el protocolo resumable de GCS y, además, un manifiesto en
    disco: si se cierra la app a mitad, al reabrirla continúa donde iba.
  - **El bucket es la fuente de verdad de lo ya subido.** El manifiesto es
    solo una caché: vive dentro de la carpeta del vuelo, y si se borra, se
    copia el vuelo a otro disco o se reinstala la app, desaparece — y antes
    eso significaba resubir los 8,7 GB enteros aunque GCS ya los tuviera. Por
    eso al arrancar se **lista el prefijo destino** y se descarta lo que ya
    está allí con el mismo tamaño (ver `reconciliar`). Es una petición por
    cada 1000 objetos: dos segundos que ahorran media hora de subida.
  - **Todo queda escrito.** Reintentos, cortes de red, tokens caducados y
    reanudaciones por offset van a `subidas.log` (ver `upload_log`). Sin eso,
    un «va lento» o un «se quedó colgado» no se puede diagnosticar después.
  - **Sin credenciales en el cliente.** Una service account key dentro del
    `.exe` es escritura en el bucket para cualquiera que lo abra con un editor
    hexadecimal. Quien autoriza es una identidad **del usuario**, vía
    `UrlProvider`: por defecto `GcsOAuthProvider`, que usa el login con Google
    (`google_auth`) y deja el permiso en manos de IAM sobre el bucket. Queda
    `SignedUrlProvider` para el día en que haga falta un backend firmando.
  - **Solo stdlib.** Misma razón que en `updater.py`: `requests` y
    `google-cloud-storage` engordarían el bundle de PyInstaller sin aportar
    nada que `urllib` no haga.
  - **Paralelo por ficheros.** La subida es I/O de red, no CPU: los hilos no
    sufren el GIL aquí (al contrario que la fase del SDK, donde el pool a hilos
    ya se demostró que no era el cuello).

Uso típico::

    auth = GoogleAuth(CLIENT_ID, CLIENT_SECRET, hosted_domain="aerotools.es")
    if not auth.is_logged_in():
        auth.login()

    plan = build_plan(Path(r"D:/Vuelos/PLANTA_B"), prefix="vuelos/planta_b")
    result = upload_plan(plan, GcsOAuthProvider("aerotools-vuelos", auth),
                         on_progress=print)
    if not result.ok:
        ...  # result.failed trae (ruta, error) por fichero
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import rust_bridge, upload_log

__all__ = [
    "UploadItem",
    "UploadPlan",
    "UploadResult",
    "RemoteObject",
    "UrlProvider",
    "GcsOAuthProvider",
    "SignedUrlProvider",
    "YaExiste",
    "build_plan",
    "upload_plan",
    "upload_one",
    "objetos_en_prefijo",
    "listar_objetos_remotos",
    "descargar_objeto",
    "reconciliar",
]

# El logger se OBTIENE aquí pero se CONFIGURA en `upload_plan`: importar este
# módulo no debe crear ficheros por su cuenta (los tests lo importan, y la app
# lo importa antes de que el usuario decida subir nada). Un logger sin handlers
# descarta lo que reciba sin fallar, así que las llamadas sueltas son seguras.
_log = logging.getLogger(upload_log.LOGGER_NAME)

# GCS exige que todo chunk intermedio de una subida resumable sea múltiplo de
# 256 KiB. 16 MiB es el compromiso habitual: suficientemente grande para no
# pagar una ida y vuelta por megabyte, suficientemente pequeño para que un
# corte no tire mucho trabajo.
_CHUNK_MULTIPLE = 256 * 1024
CHUNK_SIZE = 16 * 1024 * 1024
assert CHUNK_SIZE % _CHUNK_MULTIPLE == 0

# Subidas simultáneas. Medido sobre PLANTA_B (2518 ficheros, 8,5 GB) el
# 2026-08-06: con 4 la línea se pasa el rato esperando round-trips (cada
# fichero paga la apertura de sesión resumable) y no se satura ni una conexión
# mala. Misma red, mismo momento: 4 → 4,4 Mbps · 16 → 11 Mbps. Por cable, 16
# da 60 Mbps. Con 32 no se ganó nada medible, así que 16 es el punto.
DEFAULT_CONCURRENCY = 16

TIMEOUT = 60
MAX_RETRIES = 5
# Ficheros seguidos con 403 persistente (tras refrescar el token) a partir de
# los cuales se aborta toda la subida en vez de reintentar los miles restantes.
MAX_403_CONSECUTIVOS = 3
# Un refresco de token hecho hace menos de esto se reutiliza (ver
# `GcsOAuthProvider.recover_auth`): N hilos con 403 = 1 petición a la Suite.
REFRESCO_REUTILIZAR_S = 30.0
# La Suite limita gcs-token (60/min): tras un 429 no se vuelve a pedir antes.
ESPERA_429_MIN_S = 60.0
# Tras un refresco fallido (no 429), los demás hilos no repiten la llamada
# hasta pasado este backoff.
REFRESCO_FALLO_BACKOFF_S = 5.0
# Parámetros de query que NO son credencial y se pueden loguear.
_QUERY_LOGUEABLE = {"ifgenerationmatch", "uploadtype", "name"}
BODY_LOG_MAX = 500
USER_AGENT = "ATOM-Organizer-Uploader"

# Extensiones que forman parte de un vuelo. El resto (temporales del sistema,
# miniaturas de Windows) no se sube: son basura que solo cuesta ancho de banda.
FLIGHT_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".mp4",
                   ".mov", ".srt", ".csv", ".txt", ".xlsx", ".xls"}
IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


class YaExiste(Exception):
    """El objeto ya estaba en el bucket y la subida se abortó sin gastar bytes.

    La lanza `upload_one` cuando GCS contesta 412 a la precondición
    `ifGenerationMatch=0` (ver `GcsOAuthProvider.upload_url`). No es un fallo:
    significa que el listado previo no llegó a ver ese objeto (tope de páginas,
    o subido por otra sesión mientras tanto) y que la red de seguridad ha hecho
    su trabajo. Se cuenta como saltado, no se reintenta.
    """

    def __init__(self, remote: str) -> None:
        super().__init__(f"{remote} ya existe en el bucket")
        self.remote = remote

    def __reduce__(self):  # picklable (pool de procesos)
        return (type(self), (self.remote,))


class GcsRechazado(RuntimeError):
    """GCS sigue contestando 403 tras refrescar el token: no es caducidad.

    Es un permiso que falta (IAM del bucket / token acotado a otro prefijo).
    No se reintenta; `upload_plan` corta la subida entera tras
    `MAX_403_CONSECUTIVOS` ficheros así (ver ahí).
    """

    def __init__(self, motivo: str) -> None:
        super().__init__(f"GCS rechaza la escritura (403): {motivo}")
        self.motivo = motivo

    def __reduce__(self):  # picklable (pool de procesos)
        return (type(self), (self.motivo,))


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

@dataclass
class UploadItem:
    """Un fichero a subir."""

    local: Path
    # Ruta dentro del bucket, con `/` siempre (aunque el cliente sea Windows).
    remote: str
    size: int
    # Si hay que subir SIN la precondición `ifGenerationMatch=0` (ver
    # `GcsOAuthProvider.upload_url`). Por defecto False: la precondición actúa
    # de red de seguridad para lo que `listar_objetos_remotos` no llegó a ver
    # (tope de 50 páginas). Se pone a True para los items que `reconciliar`
    # decide resubir a propósito PORQUE el objeto ya existe en el bucket (con
    # otro tamaño, otro MD5, o porque el local cambió desde que se subió): en
    # esos casos el objeto SÍ existe, y con la precondición puesta GCS
    # rechazaría con 412 justo lo que se quiere sobreescribir.
    sobrescribir: bool = False

    @property
    def key(self) -> str:
        return self.remote


@dataclass
class UploadPlan:
    root: Path
    items: list[UploadItem]
    prefix: str

    @property
    def total_bytes(self) -> int:
        return sum(i.size for i in self.items)

    def eta_seconds(self, mbps: float) -> float:
        """Segundos estimados a `mbps` de subida REAL (no la contratada).

        Existe para poder avisar al usuario antes de empezar: con 8,7 GB la
        diferencia entre una línea y otra son minutos contra media hora, y es
        mejor decirlo de entrada que a mitad de la barra de progreso.
        """
        if mbps <= 0:
            return float("inf")
        return (self.total_bytes * 8) / (mbps * 1_000_000)


def build_plan(root: Path, prefix: str = "", *,
               suffixes: Iterable[str] | None = None,
               on_progress=None, should_stop=None) -> UploadPlan:
    """Recorre `root` y construye el plan de subida.

    `prefix` es la carpeta destino dentro del bucket (p.ej. `vuelos/planta_b`).
    La estructura de subcarpetas del vuelo se conserva tal cual: el server
    necesita saber qué imagen venía de qué carpeta `DJI_*`.

    `on_progress`/`should_stop` son opcionales: permiten emitir avance y
    cortar el recorrido desde una llamada asíncrona (ver `Api.cloud_prepare_start`).
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    allowed = {s.lower() for s in (suffixes if suffixes is not None
                                   else FLIGHT_SUFFIXES)}
    prefix = prefix.strip("/")

    items: list[UploadItem] = []
    for path in sorted(root.rglob("*")):
        if should_stop is not None and should_stop():
            break
        if not path.is_file():
            continue
        # El manifiesto del organizado vive dentro del destino (memoria para
        # organizar por cachitos) pero nunca se entrega.
        if ".organizado" in path.relative_to(root).parts:
            continue
        if path.name.lower() in IGNORED_NAMES:
            continue
        if allowed and path.suffix.lower() not in allowed:
            continue
        rel = path.relative_to(root).as_posix()
        remote = f"{prefix}/{rel}" if prefix else rel
        items.append(UploadItem(local=path, remote=remote,
                                size=path.stat().st_size))
        if on_progress is not None and len(items) % 250 == 0:
            on_progress(len(items))

    return UploadPlan(root=root, items=items, prefix=prefix)


def agregar_estadillos(plan: UploadPlan, rutas: Iterable[str]) -> list[str]:
    """Añade al plan, IN-PLACE, una entrada por cada estadillo detectado en la
    carpeta, con destino `<prefix>/ESTADILLOS/<basename>` (ver `atom_core.lotes`
    y `atom_core.estadillo.detectar_estadillos`).

    Van fuera de `build_plan` porque no son parte del árbol del vuelo que se
    escanea con `rglob`: `detectar_estadillos` ya sabe elegir cuáles cuentan
    (columnas esenciales, no temporales de Office) y el árbol de un vuelo
    podría tener más de un candidato en carpetas distintas.

    Devuelve las rutas remotas RELATIVAS al lote (`ESTADILLOS/<basename>`), que
    es justo lo que espera `atom_core.lotes.manifest_lote(..., estadillos=...)`.
    """
    relativas: list[str] = []
    for ruta in rutas:
        path = Path(ruta)
        rel = f"ESTADILLOS/{path.name}"
        remote = f"{plan.prefix}/{rel}" if plan.prefix else rel
        plan.items.append(UploadItem(local=path, remote=remote,
                                     size=path.stat().st_size))
        relativas.append(rel)
    return relativas


def token_gcs(auth, prefix: str | None = None, *,
              inspeccion_id: int | None = None,
              planta_id: int | None = None,
              ambito: str | None = None,
              force_refresh: bool = False,
              forzar: bool = False) -> str:
    """`auth.access_token(...)` pasando `prefix`/`inspeccion_id` solo en modo
    password (token de GCS acotado a un prefijo). En Google/broker la llamada
    es la de siempre. Sin prefijo en modo password, `access_token` falla con un
    error explícito: no hay fallback."""
    kw: dict = {}
    if force_refresh:
        kw["force_refresh"] = True
    if (getattr(auth, "es_password", False) or (
            getattr(auth, "es_google", False) and ambito == "resultado")) and ambito:
        # Ámbito estadillos: sin prefijo (lo decide la Suite), pero con la
        # planta y la inspección concreta (la Suite valida visibilidad y
        # vigencia de esa inspección; sin ella responde 400).
        kw["planta_id"] = planta_id
        kw["inspeccion_id"] = inspeccion_id
        kw["ambito"] = ambito
        if forzar and ambito == "resultado":
            kw["forzar"] = True
    elif getattr(auth, "es_password", False):
        if inspeccion_id is None:
            # La Suite exige `inspeccion_id` (400 inspeccion-id-requerido):
            # se corta aquí con un error legible, nunca un listado vacío.
            from atom_core.google_auth import AuthError
            raise AuthError("Falta el id de la inspección para pedir el token "
                            "de GCS en modo usuario/contraseña.")
        kw["prefix"] = prefix
        kw["inspeccion_id"] = inspeccion_id
    return auth.access_token(**kw)


def objetos_en_prefijo(bucket: str, prefix: str, auth, *,
                       base: str = "https://storage.googleapis.com",
                       timeout: int = TIMEOUT,
                       token_prefix: str | None = None,
                       inspeccion_id: int | None = None,
                       planta_id: int | None = None,
                       ambito: str | None = None) -> int:
    """Cuántos objetos hay ya bajo `prefix` (se corta al llegar a unos pocos).

    Existe para no pisar datos en silencio: dos vuelos distintos pueden generar
    el mismo nombre de carpeta y los ficheros de dron se llaman igual
    (`DJI_0001.JPG`) en todos. Subir encima sustituye el objeto y el original
    sólo es recuperable durante los 7 días de soft-delete del bucket, si es que
    alguien se entera. Mejor negarse a subir y avisar.

    Devuelve 0 si el prefijo está libre. Si la consulta falla (red, permisos),
    propaga: quien llama decide, pero NO se interpreta como "está vacío".
    """
    q = urllib.parse.urlencode({
        "prefix": prefix.strip("/") + "/" if prefix.strip("/") else "",
        "maxResults": 5,
        "fields": "items(name)",
    })
    url = f"{base.rstrip('/')}/storage/v1/b/{urllib.parse.quote(bucket)}/o?{q}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token_gcs(auth, token_prefix or prefix, inspeccion_id=inspeccion_id, planta_id=planta_id, ambito=ambito)}")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return len(data.get("items") or [])


def descargar_objeto(bucket: str, name: str, auth, dest_path: "Path", *,
                     base: str = "https://storage.googleapis.com",
                     timeout: int = TIMEOUT,
                     token_prefix: str | None = None,
                     inspeccion_id: int | None = None,
                     planta_id: int | None = None,
                     ambito: str | None = None) -> None:
    """Baja UN objeto del bucket a `dest_path` (JSON API, `alt=media`).

    Contraparte de lectura de `upload_one`: mismo esquema de auth
    (`auth.access_token()`) y mismo `urllib` de stdlib, sin dependencias
    nuevas. Escribe directo a `dest_path`; quien llama decide el nombre final
    (no pisa nada por sí sola).
    """
    url = (f"{base.rstrip('/')}/storage/v1/b/{urllib.parse.quote(bucket)}/o/"
           f"{urllib.parse.quote(name, safe='')}?alt=media")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token_gcs(auth, token_prefix, inspeccion_id=inspeccion_id, planta_id=planta_id, ambito=ambito)}")
    req.add_header("User-Agent", USER_AGENT)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(resp, f)


@dataclass(frozen=True)
class RemoteObject:
    """Un objeto que YA está en el bucket, tal y como GCS lo describe."""

    name: str
    size: int
    md5: str = ""


class ListadoTruncado(RuntimeError):
    """El inventario remoto no cabe en `max_paginas` y no se puede dar por completo."""


def faltantes_en_remoto(items, remotos: dict) -> list[tuple[str, str]]:
    """Cruza `items` contra un inventario remoto: `[(remote, motivo)]` de lo que
    no está en el bucket o está con otro tamaño. Helper común de la verificación
    de lotes (`Api._verificar_lote_completo`) y de la subida del resultado."""
    faltantes: list[tuple[str, str]] = []
    for item in items:
        remoto = remotos.get(item.remote)
        if remoto is None:
            faltantes.append((item.remote, "no esta en el bucket"))
        elif item.size >= 0 and remoto.size != item.size:
            faltantes.append((
                item.remote,
                f"tamaño remoto {remoto.size} != {item.size} local"))
    return faltantes


def listar_objetos_remotos(bucket: str, prefix: str, auth, *,
                           base: str = "https://storage.googleapis.com",
                           timeout: int = TIMEOUT,
                           max_paginas: int = 50,
                           token_prefix: str | None = None,
                           inspeccion_id: int | None = None,
                           planta_id: int | None = None,
                           ambito: str | None = None,
                           estricto: bool = False,
                           on_pagina: Callable[[int, int], None] | None = None
                           ) -> dict[str, RemoteObject]:
    """Inventario COMPLETO de lo que hay bajo `prefix`, indexado por nombre.

    Esta es la pieza que hace que reseleccionar la misma carpeta no vuelva a
    subir nada: el manifiesto local dice lo que *esta* instalación subió, pero
    el bucket dice lo que hay de verdad. Un vuelo entero cabe en 3 páginas de
    1000, así que el coste son un par de segundos frente a los GB que ahorra.

    Se piden `size` y `md5Hash` porque son los dos criterios de comparación (y
    vienen ya calculados por GCS: no cuestan nada extra).

    `max_paginas` acota lo que puede tardar esto: 50 páginas son 50.000
    objetos, veinte veces un vuelo grande. Existe porque esta consulta se hace
    de forma síncrona antes de dejar seguir al usuario, y un prefijo degenerado
    no puede dejar la pantalla colgada. Cortar por lo sano es seguro: los
    objetos que no se lleguen a ver se tratan como no subidos, así que como
    mucho se resube de más — nunca de menos.

    `estricto=True` (verificación del resultado): un inventario truncado NO es
    utilizable, porque daría falsos faltantes; en vez de avisar y seguir, lanza
    `ListadoTruncado`.

    `on_pagina(objetos_acumulados, paginas)` se llama tras cada página leída
    (progreso en vivo del listado; un fallo del callback no rompe el listado).
    """
    prefijo = prefix.strip("/")
    prefijo = prefijo + "/" if prefijo else ""
    encontrados: dict[str, RemoteObject] = {}
    token = ""
    bucket_q = urllib.parse.quote(bucket)

    for n_pagina in range(1, max_paginas + 1):
        params = {
            "prefix": prefijo,
            "maxResults": 1000,
            "fields": "items(name,size,md5Hash),nextPageToken",
        }
        if token:
            params["pageToken"] = token
        url = f"{base.rstrip('/')}/storage/v1/b/{bucket_q}/o?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token_gcs(auth, token_prefix or prefijo, inspeccion_id=inspeccion_id, planta_id=planta_id, ambito=ambito)}")
        req.add_header("User-Agent", USER_AGENT)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")

        for it in data.get("items") or []:
            nombre = it.get("name")
            if not nombre:
                continue
            try:
                tam = int(it.get("size", 0))
            except (TypeError, ValueError):
                tam = -1  # tamaño ilegible: nunca casará, se resube. Correcto.
            encontrados[nombre] = RemoteObject(nombre, tam, it.get("md5Hash") or "")

        if on_pagina is not None:
            try:
                on_pagina(len(encontrados), n_pagina)
            except Exception:  # noqa: BLE001 - el progreso nunca rompe el listado
                _log.debug("on_pagina falló", exc_info=True)
        token = data.get("nextPageToken") or ""
        if not token:
            break
    else:
        # Se agotaron las páginas con `nextPageToken` todavía pendiente: el
        # inventario está incompleto y hay que decirlo, porque explica que se
        # resuba algo que en realidad ya estaba.
        if estricto:
            raise ListadoTruncado(
                f"el destino tiene más de {max_paginas * 1000} objetos: "
                "listado truncado, no verificable")
        _log.warning("el destino tiene más de %d objetos; me quedo con los "
                     "primeros %d y el resto se tratará como no subido",
                     max_paginas * 1000, len(encontrados))

    return encontrados


class SobrescrituraNoPermitida(RuntimeError):
    """El plan exige reemplazar objetos ya subidos y el token no puede borrar."""


def reconciliar(plan: UploadPlan, remotos: dict[str, RemoteObject],
                manifest: "Manifest | None" = None, *,
                permitir_sobrescribir: bool = True) -> tuple[list[UploadItem], list[UploadItem]]:
    """Parte el plan en `(pendientes, ya_subidos)` cruzando local, bucket y manifiesto.

    Un fichero se da por subido si el bucket tiene un objeto con **su mismo
    nombre y su mismo tamaño**. El tamaño es la comparación barata y en la
    práctica decisiva: dos JPG distintos de un dron no coinciden al byte.

    Cuando además el manifiesto guarda el MD5 de cuando se subió y GCS
    devuelve el suyo, se comparan: si difieren, el objeto remoto **no** es
    este fichero (o se subió corrupto) y se vuelve a subir. Es verificación
    gratis — los dos hashes ya están calculados, no hay que releer el disco.

    El manifiesto se usa de dos formas, y ninguna es «fuente de verdad»:

    - Como **refuerzo**: si dice que algo está subido pero el bucket no lo
      tiene, manda el bucket.
    - Como **detector de cambios locales**: si el fichero fue subido y desde
      entonces ha cambiado en disco (`is_done` compara tamaño y mtime), se
      resube aunque el bucket tenga un objeto del mismo tamaño — porque lo que
      hay allí es la versión vieja. Sin esto, reemplazar una imagen por otra
      del mismo peso dejaría el bucket con la anterior y sin avisar.

    Queda un caso que esta función NO puede distinguir: un fichero local nunca
    subido cuyo nombre y tamaño coinciden por casualidad con un objeto que ya
    estaba en el destino (dos vuelos distintos bajo la misma inspección, donde
    las imágenes se llaman igual: `DJI_0001.JPG`). Se da por subido. Descartarlo
    del todo exigiría leer y hashear los 8,7 GB locales en cada arranque, que
    cuesta más que la propia subida; la protección real es que cada vuelo vaya
    a su inspección, que es lo que la pantalla obliga a elegir.

    `permitir_sobrescribir=False` (modo usuario/contraseña: el token de GCS no
    tiene permiso de borrado, y reemplazar un objeto lo exige): si hiciera
    falta sobrescribir algo se lanza `SobrescrituraNoPermitida` ANTES de
    devolver el plan, es decir, antes de subir o mutar nada.
    """
    pendientes: list[UploadItem] = []
    hechos: list[UploadItem] = []

    for item in plan.items:
        remoto = remotos.get(item.remote)
        if remoto is None:
            pendientes.append(item)
            continue
        # En los tres casos de abajo el objeto YA existe en el bucket (solo que
        # no es el que se quiere tener ahí), así que se marca `sobrescribir`:
        # sin eso, `upload_one` mandaría la precondición `ifGenerationMatch=0`
        # y GCS respondería 412 justo a la resubida que se está forzando aquí.
        if remoto.size != item.size:
            _log.debug("resubo %s: tamaño distinto (local %d, bucket %d)",
                       item.remote, item.size, remoto.size)
            pendientes.append(replace(item, sobrescribir=True))
            continue

        conocido = manifest is not None and manifest.md5_de(item) != ""
        if conocido and not manifest.is_done(item):
            _log.info("resubo %s: el fichero local ha cambiado desde que se "
                      "subió", item.remote)
            pendientes.append(replace(item, sobrescribir=True))
            continue

        md5_local = manifest.md5_de(item) if manifest is not None else ""
        if md5_local and remoto.md5 and md5_local != remoto.md5:
            _log.warning("resubo %s: el MD5 del bucket no coincide con el del "
                         "manifiesto (bucket %s, manifiesto %s)",
                         item.remote, remoto.md5, md5_local)
            pendientes.append(replace(item, sobrescribir=True))
            continue

        hechos.append(item)

    if not permitir_sobrescribir:
        pisar = [it.remote for it in pendientes if it.sobrescribir]
        if pisar:
            raise SobrescrituraNoPermitida(
                f"{len(pisar)} fichero(s) ya existen en el destino con distinto "
                "contenido y habría que sobrescribirlos (p. ej. "
                f"{pisar[0]}). Con usuario y contraseña el acceso al bucket no "
                "permite borrar ni reemplazar objetos: no se ha subido nada. "
                "Entra con tu cuenta de Google o pide a un administrador que "
                "elimine esos ficheros.")

    return pendientes, hechos


# --------------------------------------------------------------------------
# Proveedor de URLs firmadas
# --------------------------------------------------------------------------

class UrlProvider:
    """De dónde sale el permiso para escribir un objeto en el bucket.

    Hay dos formas de autorizar la subida y el resto del módulo no distingue:

    - **`GcsOAuthProvider`** — el usuario se ha identificado con Google y el
      permiso lo pone IAM sobre el bucket. No hace falta backend.
    - **`SignedUrlProvider`** — un servicio propio firma cada objeto. Más
      control (puede imponer rutas o cuotas), a cambio de mantenerlo.
    """

    def upload_url(self, remote: str, size: int, *,
                   sobrescribir: bool = False) -> str:  # pragma: no cover
        """URL a la que abrir la sesión resumable.

        `sobrescribir=True` pide la URL SIN protección: para el resto de casos
        (ver `GcsOAuthProvider`) el proveedor debe evitar pisar un objeto que
        ya exista en el destino.
        """
        raise NotImplementedError

    def headers(self) -> dict[str, str]:
        """Cabeceras de autorización, pedidas **en cada petición**.

        No se cachean a propósito: un access token de Google vive 1 h y una
        subida grande dura más que eso.
        """
        return {}

    def recover_auth(self) -> bool:
        """Intenta recuperar la autorización tras un 401/403.

        Devuelve True si con eso basta y la sesión resumable en curso sigue
        sirviendo (caso OAuth: solo había caducado el token, y los bytes ya
        confirmados en GCS siguen ahí). False si hay que abrir sesión nueva.
        """
        return False

    def listar_remotos(self, prefix: str) -> dict[str, RemoteObject] | None:
        """Qué hay ya en el destino, para no resubirlo.

        `None` significa «no puedo saberlo», no «está vacío»: quien no pueda
        listar (p.ej. una signed URL, que autoriza un objeto concreto y no da
        permiso de listado) se queda con el manifiesto local como antes.
        """
        return None


class GcsOAuthProvider(UrlProvider):
    """Autoriza con la cuenta de Google del usuario (ver `google_auth`).

    Se usa la XML API de Cloud Storage (`https://storage.googleapis.com/
    <bucket>/<objeto>`) porque acepta el mismo protocolo resumable que las
    signed URLs: así hay un único camino de código para las dos formas de
    autorizar, y el que se ejercita en los tests es el que corre en producción.

    Quién puede subir se decide **fuera de la app**, en el IAM del bucket
    (`roles/storage.objectCreator` al grupo de Google que toque). Revocar a
    alguien no exige publicar versión.
    """

    BASE = "https://storage.googleapis.com"

    def __init__(self, bucket: str, auth, *, base: str | None = None,
                 prefix: str | None = None, inspeccion_id: int | None = None,
                 planta_id: int | None = None, ambito: str | None = None,
                 forzar: bool = False):
        if not bucket:
            raise ValueError("falta el nombre del bucket")
        self.bucket = bucket
        self.auth = auth
        # Prefijo/inspección con los que pedir el token acotado (modo password).
        self.prefix = prefix
        self.inspeccion_id = inspeccion_id
        # Ámbito por planta (estadillos, modo password): sin prefijo/inspección.
        self.planta_id = planta_id
        self.ambito = ambito
        # «Sobrescribir todo»: token resultado con objectAdmin (solo ámbito resultado).
        self.forzar = forzar
        self.base = (base or self.BASE).rstrip("/")
        # Refresco deduplicado entre hilos (ver `recover_auth`).
        self._refresh_lock = threading.Lock()
        self._ultimo_refresco = -1e9   # time.monotonic() del último refresco
        self._sin_pedir_hasta = 0.0    # time.monotonic(): cooldown tras 429
        # Generación del token: sube con cada refresco real. Cada hilo apunta
        # la que usó en su última petición (`headers`) para que `recover_auth`
        # sepa si el token reutilizable es más nuevo que el que dio el 403.
        self._generacion = 0
        self._tl = threading.local()
        # Último refresco fallido (no 429): los hilos en espera no repiten la
        # llamada en serie hasta pasado `_fallo_hasta`.
        self._fallo_hasta = 0.0
        self._fallo_exc: Exception | None = None

    @property
    def solo_crear(self) -> bool:
        """En modo password el token de GCS no puede borrar: no se puede
        reemplazar un objeto existente (ver `reconciliar`)."""
        return bool(getattr(self.auth, "es_password", False))

    def upload_url(self, remote: str, size: int, *,
                   sobrescribir: bool = False) -> str:
        objeto = urllib.parse.quote(remote.lstrip("/"), safe="/")
        url = f"{self.base}/{self.bucket}/{objeto}"
        if not sobrescribir:
            # `ifGenerationMatch=0` solo deja crear el objeto si NO existe
            # ninguna generación previa. Es la red de seguridad para lo que
            # `listar_objetos_remotos` no llegó a ver (tope de 50 páginas): si
            # el objeto ya está en el bucket, GCS corta con 412 sin aceptar ni
            # un byte, en vez de sobreescribirlo en silencio.
            url += "?" + urllib.parse.urlencode({"ifGenerationMatch": 0})
        return url

    def headers(self) -> dict[str, str]:
        self._tl.generacion = self._generacion
        return {"Authorization": "Bearer " + token_gcs(
            self.auth, self.prefix, inspeccion_id=self.inspeccion_id,
            planta_id=self.planta_id, ambito=self.ambito,
            forzar=self.forzar)}

    def recover_auth(self) -> bool:
        # Fuerza el refresco; si el usuario revocó el acceso, `access_token`
        # levanta AuthError y la subida falla con un mensaje que se entiende.
        #
        # Con 16 hilos y un 403 general, cada uno pedía un token nuevo y la
        # Suite (60/min) devolvía 429 en cascada. Ahora el refresco va bajo
        # lock: quien llega y ve que otro hilo refrescó hace <30 s reutiliza
        # ese token en vez de pedir otro. Tras un 429 de la Suite no se vuelve
        # a pedir hasta pasada la espera (>=60 s o Retry-After): el hilo
        # espera (bajo el lock, los demás también) y luego refresca de verdad,
        # para no reintentar con el token caducado.
        # El atajo de reutilizar solo vale si el token refrescado es más nuevo
        # que el que usó este hilo en la petición que dio el 403.
        with self._refresh_lock:
            usada = getattr(self._tl, "generacion", 0)
            ahora = time.monotonic()
            if ahora < self._fallo_hasta and self._fallo_exc is not None:
                raise self._fallo_exc
            if ahora < self._sin_pedir_hasta:
                time.sleep(self._sin_pedir_hasta - ahora)
            elif (self._generacion > usada
                    and ahora - self._ultimo_refresco < REFRESCO_REUTILIZAR_S):
                return True
            try:
                token_gcs(self.auth, self.prefix,
                          inspeccion_id=self.inspeccion_id,
                          planta_id=self.planta_id, ambito=self.ambito,
                          force_refresh=True, forzar=self.forzar)
            except Exception as exc:
                espera = getattr(exc, "retry_after", None)
                if espera is not None:
                    espera = max(float(espera), ESPERA_429_MIN_S)
                    self._sin_pedir_hasta = time.monotonic() + espera
                    _log.warning("la Suite limita gcs-token (429): no pido "
                                 "otro token en %.0fs", espera)
                else:
                    self._fallo_hasta = time.monotonic() + REFRESCO_FALLO_BACKOFF_S
                    self._fallo_exc = exc
                raise
            self._ultimo_refresco = time.monotonic()
            self._generacion += 1
            self._fallo_exc = None
            self._tl.generacion = self._generacion
        return True

    def listar_remotos(self, prefix: str) -> dict[str, RemoteObject] | None:
        return listar_objetos_remotos(self.bucket, prefix, self.auth,
                                      base=self.base,
                                      token_prefix=self.prefix or prefix,
                                      inspeccion_id=self.inspeccion_id,
                                      planta_id=self.planta_id,
                                      ambito=self.ambito)


class SignedUrlProvider(UrlProvider):
    """Pide la URL firmada a un endpoint HTTP propio.

    El endpoint recibe ``{"object": ..., "size": ...}`` con un Bearer token y
    responde ``{"url": "https://storage.googleapis.com/..."}``. Es el backend
    quien decide en qué bucket y bajo qué prefijo puede escribir este cliente
    — nunca el cliente, que es código en manos del usuario.
    """

    def __init__(self, endpoint: str, token: str, *, timeout: int = TIMEOUT):
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout

    def upload_url(self, remote: str, size: int, *,
                   sobrescribir: bool = False) -> str:
        # `sobrescribir` no viaja en el payload: quien decide si protege o no
        # el objeto es el backend que firma, no este cliente. El parámetro
        # existe solo para cumplir el contrato de `UrlProvider`.
        payload = json.dumps({"object": remote, "size": size}).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())
        url = data.get("url")
        if not url:
            raise RuntimeError(f"El endpoint no devolvió URL para {remote}")
        return url


# --------------------------------------------------------------------------
# Manifiesto: qué se ha subido ya
# --------------------------------------------------------------------------

class Manifest:
    """Registro en disco de lo ya subido, para reanudar entre sesiones.

    Vive en el propio directorio del vuelo (`.atom-upload.json`). Guarda
    tamaño y mtime además del nombre: si el usuario reemplaza una imagen entre
    dos intentos, el fichero se vuelve a subir en vez de darse por bueno.
    """

    FILENAME = ".atom-upload.json"
    FLUSH_CADA_MARCAS = 500
    FLUSH_CADA_SEGUNDOS = 5.0

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._done: dict[str, dict] = {}
        # Escritura por lotes: `mark` solo ensucia; se vuelca cada
        # FLUSH_CADA_MARCAS marcas o FLUSH_CADA_SEGUNDOS, y SIEMPRE con `flush()`
        # final. Reescribir el JSON entero por fichero era O(n²) con decenas de
        # miles de entradas hidratadas.
        self._sucio = 0
        self._ultimo_flush = time.monotonic()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._done = data.get("done", {})
        except (OSError, ValueError):
            # Un manifiesto corrupto no debe impedir subir: peor caso, se
            # resube todo.
            self._done = {}

    def _flush_locked(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            # `dumps` (encoder en C) y una sola escritura: `dump` a fichero usa
            # el encoder Python puro y retiene el GIL.
            texto = json.dumps({"version": 1, "done": self._done})
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(texto)
            os.replace(tmp, self.path)
        except OSError as exc:
            # El manifiesto es una optimización, no puede tumbar la subida;
            # queda sucio para reintentarlo en el siguiente flush.
            _log.warning("no se pudo escribir el manifiesto %s: %s", self.path, exc)
            return
        self._sucio = 0
        self._ultimo_flush = time.monotonic()

    def flush(self) -> None:
        """Vuelca a disco lo pendiente (no hace nada si no hay cambios)."""
        with self._lock:
            if self._sucio:
                self._flush_locked()

    def is_done(self, item: UploadItem) -> bool:
        rec = self._done.get(item.key)
        if not rec:
            return False
        try:
            stat = item.local.stat()
        except OSError:
            return False
        return (rec.get("size") == stat.st_size
                and int(rec.get("mtime", -1)) == int(stat.st_mtime))

    def md5_de(self, item: UploadItem) -> str:
        """MD5 registrado al subir este objeto, si lo hay.

        Sirve para contrastar contra el `md5Hash` que devuelve el bucket sin
        tener que releer el fichero del disco.
        """
        with self._lock:
            rec = self._done.get(item.key)
            return (rec or {}).get("md5", "") or ""

    def mark(self, item: UploadItem, md5_b64: str) -> None:
        try:
            stat = item.local.stat()
        except OSError:
            return
        with self._lock:
            self._done[item.key] = {
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "md5": md5_b64,
            }
            self._sucio += 1
            if (self._sucio >= self.FLUSH_CADA_MARCAS
                    or time.monotonic() - self._ultimo_flush >= self.FLUSH_CADA_SEGUNDOS):
                self._flush_locked()

    def hidratar(self, items: Sequence[UploadItem],
                 remotos: dict[str, "RemoteObject"]) -> int:
        """Anota como hechos los objetos que el bucket ya tenía.

        Reconstruye el manifiesto cuando se ha perdido (carpeta copiada a otro
        disco, reinstalación, borrado a mano). No es imprescindible —la
        reconciliación contra el bucket ya funciona sin esto— pero deja el
        registro local coherente con la realidad y guarda el MD5 del bucket,
        que en la siguiente pasada permite verificar sin releer nada.

        Se escribe **una sola vez** al final, no por fichero.

        Devuelve cuántas entradas nuevas se anotaron.
        """
        nuevas = 0
        with self._lock:
            for item in items:
                remoto = remotos.get(item.remote)
                if remoto is None or item.key in self._done:
                    continue
                try:
                    stat = item.local.stat()
                except OSError:
                    continue
                self._done[item.key] = {
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "md5": remoto.md5,
                }
                nuevas += 1
            if nuevas:
                self._sucio += nuevas
                self._flush_locked()
        return nuevas


def _file_md5_b64(path: Path) -> str:
    """MD5 en base64, que es el formato en el que GCS expone el suyo.

    Se usa MD5 y no CRC32C (el nativo de GCS) porque hashlib es C y CRC32C
    en Python puro sobre 8,7 GB costaría más que la propia subida.
    """
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return base64.b64encode(h.digest()).decode()


# Claves de `medidas` (segundos-hilo acumulados y reintentos por causa). Es lo
# único que la UI usa para decir «limitado por red / disco / ...»: solo se
# afirma lo que aquí se mide.
MEDIDAS_SEGUNDOS = ("disco_s", "red_s", "sesion_s", "espera_s", "token_s")
CAUSAS_RETRY = ("429", "5xx", "auth", "http", "red")


def medidas_vacias() -> dict:
    return {**{k: 0.0 for k in MEDIDAS_SEGUNDOS}, "reintentos": {c: 0 for c in CAUSAS_RETRY}}


def sumar_medidas(a: dict | None, b: dict | None) -> dict:
    """Suma dos `medidas` (p. ej. la de urgentes y la del resto)."""
    out = medidas_vacias()
    for m in (a, b):
        if not m:
            continue
        for k in MEDIDAS_SEGUNDOS:
            out[k] += float(m.get(k, 0.0) or 0.0)
        for c in CAUSAS_RETRY:
            out["reintentos"][c] += int((m.get("reintentos") or {}).get(c, 0) or 0)
    return out




# --------------------------------------------------------------------------
# Subida del plan completo
# --------------------------------------------------------------------------

@dataclass
class UploadResult:
    uploaded: int = 0
    skipped: int = 0
    bytes_sent: int = 0
    elapsed: float = 0.0
    failed: list[tuple[str, str]] = field(default_factory=list)
    # Reintentos totales de la corrida. Es la medida de lo mal que fue la red:
    # una subida lenta con 0 reintentos es una línea lenta, y una subida lenta
    # con 300 es una conexión que se cae. Sin esto no se distinguen.
    retries: int = 0
    # Cuántos de los `skipped` se descartaron por estar ya en el bucket (frente
    # a los que ya constaban en el manifiesto local).
    skipped_remoto: int = 0
    # Saltados ya en pleno vuelo, porque GCS rechazó con 412 la precondición
    # `ifGenerationMatch=0` (ver `YaExiste`). Es la medida de lo que el listado
    # previo NO llegó a ver: si crece, el inventario se está quedando corto.
    skipped_precondicion: int = 0
    # Si se pudo consultar el bucket. False = se subió a ciegas, fiándose solo
    # del manifiesto local, y por tanto el `skipped` puede quedarse corto.
    reconciliado: bool = False
    # Mensaje si la subida se cortó entera por 403 persistente de GCS
    # (`MAX_403_CONSECUTIVOS`); None si no hubo corte.
    abortado: str | None = None
    # Tiempos-hilo y reintentos por causa (ver `medidas_vacias`): de qué se
    # fue el tiempo. Sirve para decir red / disco / reintentos sin adivinar.
    medidas: dict = field(default_factory=medidas_vacias)

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def mbps(self) -> float:
        if self.elapsed <= 0:
            return 0.0
        return (self.bytes_sent * 8) / self.elapsed / 1_000_000


def _texto_pb_listo(pb: str) -> str:
    m = re.fullmatch(r"PB(\d+)", pb or "", re.IGNORECASE)
    if m:
        return f"PB {int(m.group(1))} listo para analizar"
    return f"{pb or 'General'}: subida verificada"


def _upload_plan_rust(rust, plan, provider, pending, result, manifest, bytes_pendientes,
                      urgentes, on_progress, on_stats, should_stop, on_evento) -> UploadResult:
    """Subida delegada en `atom_rs.subir_lote`. Solo recibe PENDIENTES; el manifiesto
    se marca únicamente con el md5 que Rust verificó contra el servidor."""
    oauth = isinstance(provider, GcsOAuthProvider)
    items = [(str(i.local), i.remote, int(i.size), bool(i.sobrescribir), i.remote in urgentes)
             for i in pending]
    por_remote = {i.remote: i for i in pending}
    t0 = time.monotonic()
    ultimo = {"p": None}

    def _foto(p: dict, final: bool = False) -> dict:
        elapsed = max(time.monotonic() - t0, 1e-6)
        done = int(p.get("files_done", 0))
        medidas = p.get("medidas") or {}
        return {
            "files_done": done,
            "files_total": int(p.get("files_total", len(pending))),
            "bytes_done": int(p.get("bytes_done", 0)),
            "bytes_total": int(p.get("bytes_total", bytes_pendientes)),
            "elapsed": elapsed,
            "eta": p.get("eta"),
            "mbps": float(p.get("mbps") or 0.0),
            "files_per_s": done / elapsed,
            "retries": int(p.get("reintentos", 0)),
            "hilos_activos": int(p.get("hilos_activos", 0)),
            "fichero_actual": p.get("fichero_actual") or "",
            "fallidas": int(p.get("fallidas", 0)),
            "hilos_max": int(p.get("hilos_max", DEFAULT_CONCURRENCY)),
            "medidas": {**{k: medidas.get(k, 0.0) for k in MEDIDAS_SEGUNDOS},
                        "reintentos": dict(medidas.get("reintentos") or {})},
            "final": final,
        }

    def _callback(ev: dict) -> None:
        kind = ev.get("kind")
        try:
            if kind == "progreso":
                ultimo["p"] = ev
                if on_stats is not None:
                    on_stats(_foto(ev))
            elif kind == "pb_listo":
                if on_progress is not None:
                    on_progress(_texto_pb_listo(str(ev.get("pb", ""))))
                if on_evento is not None:
                    on_evento(ev)
            elif kind == "error":
                _log.error("FALLÓ %s (%s) → %s", ev.get("remote"), ev.get("tipo"), ev.get("motivo"))
                if on_evento is not None:
                    on_evento(ev)
        except Exception:  # noqa: BLE001 - la UI nunca debe tumbar la subida
            _log.exception("callback de eventos de subida falló")

    token_fn = (lambda: provider.headers()["Authorization"]) if oauth else None
    pref = plan.prefix if plan.prefix.endswith("/") else plan.prefix + "/"
    hecho = rust.subir_lote(
        items, pref, "oauth" if oauth else "firmada",
        lambda remote, size, sobrescribir: provider.upload_url(
            remote, size, sobrescribir=sobrescribir),
        token_fn, provider.recover_auth, _callback,
        lambda: bool(should_stop and should_stop()))

    try:
        for remote, md5 in (hecho.get("md5") or {}).items():
            item = por_remote.get(remote)
            if item is not None and md5:
                manifest.mark(item, md5)
    finally:
        manifest.flush()
    result.uploaded = int(hecho.get("subidos", 0))
    n412 = int(hecho.get("saltados_412", 0))
    result.skipped += n412
    result.skipped_precondicion += n412
    result.bytes_sent = int(hecho.get("bytes_enviados", 0))
    result.elapsed = float(hecho.get("elapsed") or (time.monotonic() - t0))
    result.retries = int(hecho.get("reintentos", 0))
    result.failed = [(str(r), str(m)) for r, m in (hecho.get("fallidos") or [])]
    if hecho.get("abortado"):
        result.abortado = f"{hecho['abortado']} · subida abortada"
        _log.error("ABORTO: %s", result.abortado)
        if on_progress is not None:
            on_progress(f"ERROR: {result.abortado}")
    p = ultimo["p"] or {}
    medidas = p.get("medidas") or {}
    result.medidas = {**{k: medidas.get(k, 0.0) for k in MEDIDAS_SEGUNDOS},
                      "reintentos": dict(medidas.get("reintentos") or {})}
    if on_stats is not None:
        foto = _foto({**p, "files_done": result.uploaded + n412 + len(result.failed),
                      "bytes_done": result.bytes_sent}, final=True)
        try:
            on_stats(foto)
        except Exception:  # noqa: BLE001
            _log.exception("on_stats final falló")
    _log.info("FIN (Rust) · %d subidos · %d 412 · %.2f GB en %.0fs · %d reintentos · %d fallos",
              result.uploaded, n412, result.bytes_sent / 1024 ** 3, result.elapsed,
              result.retries, len(result.failed))
    for objeto, motivo in result.failed:
        _log.error("FALLÓ %s → %s", objeto, motivo)
    return result


class _ManifiestoNulo:
    """Manifiesto sin disco para `upload_one`: un objeto suelto no se reanuda."""

    def mark(self, item, md5_b64) -> None:
        pass

    def flush(self) -> None:
        pass


class _ManifiestoMd5(_ManifiestoNulo):
    def __init__(self, destino: dict) -> None:
        self._d = destino

    def mark(self, item, md5_b64) -> None:
        self._d[item.remote] = md5_b64


def upload_one(item: UploadItem, provider: UrlProvider, *,
               should_stop: Callable[[], bool] | None = None) -> str | None:
    """Sube UN fichero vía `atom_rs.subir_lote` (1 item) reutilizando `_upload_plan_rust`.

    Mantiene el contrato de errores del antiguo `upload_file` (retirado): lanza `YaExiste` si
    GCS contesta 412 (objeto ya en destino y sin `sobrescribir`), `GcsRechazado`
    si la subida se aborta por 403 persistente y `RuntimeError(motivo)` ante
    cualquier otro fallo. Devuelve el MD5 (base64) verificado por Rust.
    Sin atom_rs/ABI válida: `RustNoDisponible` (sin fallback a Python).
    """
    upload_log.get_logger()
    rust = rust_bridge.exigir("subida")
    remote = item.remote
    plan = UploadPlan(root=Path(item.local).parent, items=[item],
                      prefix=remote.rsplit("/", 1)[0] if "/" in remote else "")
    md5: dict[str, str] = {}
    man = _ManifiestoMd5(md5)

    res = _upload_plan_rust(rust, plan, provider, [item], UploadResult(), man,
                            item.size, set(), None, None, should_stop, None)
    if res.abortado:
        raise GcsRechazado(res.abortado)
    if res.failed:
        raise RuntimeError(res.failed[0][1])
    if res.skipped_precondicion:
        raise YaExiste(remote)
    return md5.get(remote)


def upload_plan(plan: UploadPlan, provider: UrlProvider, *,
                concurrency: int = DEFAULT_CONCURRENCY,
                manifest: Manifest | None = None,
                on_progress: Callable[[str], None] | None = None,
                on_stats: Callable[[dict], None] | None = None,
                should_stop: Callable[[], bool] | None = None,
                remotos: dict[str, RemoteObject] | None = None,
                urgentes: set[str] | None = None,
                on_evento: Callable[[dict], None] | None = None,
                on_item: Callable[[UploadItem, bool], None] | None = None) -> UploadResult:
    """Sube todos los ficheros del plan, en paralelo, reanudando y sin repetir.

    `urgentes` (remotos urgentes) y `on_evento` (`pb_listo`/`error`) solo los usa la
    subida en Rust.

    Antes de mover un byte se pregunta al bucket qué hay ya en el destino y se
    descarta de la lista (ver `reconciliar`). Eso es lo que hace que volver a
    elegir la misma carpeta cueste dos segundos en vez de media hora, incluso
    si el manifiesto local se ha perdido.

    `on_progress` recibe líneas ya formateadas, listas para la UI. No se le
    manda un evento por chunk: con 8,7 GB serían miles de refrescos y la
    interfaz se pasaría el rato repintando en vez de subiendo. El detalle fino
    (fichero a fichero, reintentos, cortes) va al log, no a la UI.

    `remotos` permite inyectar un inventario ya consultado. La pantalla previa
    SÍ lo reutiliza (`app_webview._inventario_cacheado`): en un prefijo con
    decenas de miles de objetos el listado tarda ~20 s, y pedirlo dos veces
    dejaba al operario esperando delante de una barra parada. Que la foto del
    bucket sea de hace unos minutos ya no es un riesgo: si algo se coló entre
    medias, la precondición `ifGenerationMatch=0` lo corta con un 412 (ver
    `YaExiste`) sin llegar a sobreescribir ni a gastar ancho de banda.
    """
    upload_log.get_logger()  # asegura que hay fichero donde escribir
    result = UploadResult()
    # Módulo/símbolo/ABI se validan AQUÍ, antes de listar o subir nada.
    # Sin try/except: el error es visible, no hay retroceso silencioso.
    rust = rust_bridge.exigir("subida")
    if manifest is None:
        manifest = Manifest(plan.root / Manifest.FILENAME)

    _log.info("=" * 70)
    _log.info("SUBIDA · %s → %s/ · %d ficheros · %.2f GB",
              plan.root, plan.prefix, len(plan.items),
              plan.total_bytes / 1024 ** 3)

    # --- qué hay ya en el destino -----------------------------------------
    if remotos is None:
        t0 = time.monotonic()
        try:
            remotos = provider.listar_remotos(plan.prefix)
        except Exception as exc:  # noqa: BLE001
            # No poder listar no puede impedir subir: se cae al manifiesto
            # local, que es exactamente el comportamiento de antes.
            _log.warning("no se pudo listar el destino (%s: %s); sigo solo con "
                         "el manifiesto local", type(exc).__name__, exc)
            remotos = None
        else:
            if remotos is not None:
                _log.info("el destino ya tiene %d objetos (consultado en %.1fs)",
                          len(remotos), time.monotonic() - t0)

    if remotos is not None:
        result.reconciliado = True
        pending, hechos = reconciliar(
            plan, remotos, manifest,
            permitir_sobrescribir=not getattr(provider, "solo_crear", False))
        result.skipped_remoto = len(hechos)
        nuevas = manifest.hidratar(hechos, remotos)
        if nuevas:
            _log.info("manifiesto reconstruido con %d entradas que solo "
                      "constaban en el bucket", nuevas)
    else:
        pending = [i for i in plan.items if not manifest.is_done(i)]

    result.skipped = len(plan.items) - len(pending)
    bytes_pendientes = sum(i.size for i in pending)
    _log.info("ya subidos: %d · pendientes: %d (%.2f GB)",
              result.skipped, len(pending), bytes_pendientes / 1024 ** 3)

    if on_progress is not None:
        gb = plan.total_bytes / 1024 ** 3
        on_progress(f"{len(plan.items)} ficheros · {gb:.1f} GB · "
                    f"{result.skipped} ya subidos · {len(pending)} pendientes")

    if not pending:
        _log.info("no hay nada que subir: el destino ya está completo")
        return result

    return _upload_plan_rust(rust, plan, provider, pending, result, manifest,
                             bytes_pendientes, urgentes or set(), on_progress,
                             on_stats, should_stop, on_evento)
