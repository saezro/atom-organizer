"""Subida de un vuelo a un bucket de Cloud Storage.

Pieza cliente de la arquitectura "la app solo sube, el server organiza": en vez
de procesar las ~1300 imágenes en el PC del usuario, se suben en bruto a un
bucket y el procesado ocurre en Cloud Run.

Números que condicionan TODO el diseño (medidos sobre el vuelo ANTOLIN,
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

    plan = build_plan(Path(r"D:/Vuelos/ANTOLIN"), prefix="vuelos/antolin")
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import upload_log

__all__ = [
    "UploadItem",
    "UploadPlan",
    "UploadResult",
    "RemoteObject",
    "UrlProvider",
    "GcsOAuthProvider",
    "SignedUrlProvider",
    "build_plan",
    "upload_plan",
    "objetos_en_prefijo",
    "listar_objetos_remotos",
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

# Subidas simultáneas. Medido sobre ANTOLIN (2518 ficheros, 8,5 GB) el
# 2026-08-06: con 4 la línea se pasa el rato esperando round-trips (cada
# fichero paga la apertura de sesión resumable) y no se satura ni una conexión
# mala. Misma red, mismo momento: 4 → 4,4 Mbps · 16 → 11 Mbps. Por cable, 16
# da 60 Mbps. Con 32 no se ganó nada medible, así que 16 es el punto.
DEFAULT_CONCURRENCY = 16

TIMEOUT = 60
MAX_RETRIES = 5
USER_AGENT = "ATOM-Organizer-Uploader"

# Extensiones que forman parte de un vuelo. El resto (temporales del sistema,
# miniaturas de Windows) no se sube: son basura que solo cuesta ancho de banda.
FLIGHT_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng", ".mp4",
                   ".mov", ".srt", ".csv", ".txt", ".xlsx", ".xls"}
IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


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
               suffixes: Iterable[str] | None = None) -> UploadPlan:
    """Recorre `root` y construye el plan de subida.

    `prefix` es la carpeta destino dentro del bucket (p.ej. `vuelos/antolin`).
    La estructura de subcarpetas del vuelo se conserva tal cual: el server
    necesita saber qué imagen venía de qué carpeta `DJI_*`.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    allowed = {s.lower() for s in (suffixes if suffixes is not None
                                   else FLIGHT_SUFFIXES)}
    prefix = prefix.strip("/")

    items: list[UploadItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() in IGNORED_NAMES:
            continue
        if allowed and path.suffix.lower() not in allowed:
            continue
        rel = path.relative_to(root).as_posix()
        remote = f"{prefix}/{rel}" if prefix else rel
        items.append(UploadItem(local=path, remote=remote,
                                size=path.stat().st_size))

    return UploadPlan(root=root, items=items, prefix=prefix)


def objetos_en_prefijo(bucket: str, prefix: str, auth, *,
                       base: str = "https://storage.googleapis.com",
                       timeout: int = TIMEOUT) -> int:
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
    req.add_header("Authorization", f"Bearer {auth.access_token()}")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return len(data.get("items") or [])


@dataclass(frozen=True)
class RemoteObject:
    """Un objeto que YA está en el bucket, tal y como GCS lo describe."""

    name: str
    size: int
    md5: str = ""


def listar_objetos_remotos(bucket: str, prefix: str, auth, *,
                           base: str = "https://storage.googleapis.com",
                           timeout: int = TIMEOUT,
                           max_paginas: int = 50) -> dict[str, RemoteObject]:
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
    """
    prefijo = prefix.strip("/")
    prefijo = prefijo + "/" if prefijo else ""
    encontrados: dict[str, RemoteObject] = {}
    token = ""
    bucket_q = urllib.parse.quote(bucket)

    for _ in range(max_paginas):
        params = {
            "prefix": prefijo,
            "maxResults": 1000,
            "fields": "items(name,size,md5Hash),nextPageToken",
        }
        if token:
            params["pageToken"] = token
        url = f"{base.rstrip('/')}/storage/v1/b/{bucket_q}/o?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {auth.access_token()}")
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

        token = data.get("nextPageToken") or ""
        if not token:
            break
    else:
        # Se agotaron las páginas con `nextPageToken` todavía pendiente: el
        # inventario está incompleto y hay que decirlo, porque explica que se
        # resuba algo que en realidad ya estaba.
        _log.warning("el destino tiene más de %d objetos; me quedo con los "
                     "primeros %d y el resto se tratará como no subido",
                     max_paginas * 1000, len(encontrados))

    return encontrados


def reconciliar(plan: UploadPlan, remotos: dict[str, RemoteObject],
                manifest: "Manifest | None" = None) -> tuple[list[UploadItem], list[UploadItem]]:
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
    """
    pendientes: list[UploadItem] = []
    hechos: list[UploadItem] = []

    for item in plan.items:
        remoto = remotos.get(item.remote)
        if remoto is None:
            pendientes.append(item)
            continue
        if remoto.size != item.size:
            _log.debug("resubo %s: tamaño distinto (local %d, bucket %d)",
                       item.remote, item.size, remoto.size)
            pendientes.append(item)
            continue

        conocido = manifest is not None and manifest.md5_de(item) != ""
        if conocido and not manifest.is_done(item):
            _log.info("resubo %s: el fichero local ha cambiado desde que se "
                      "subió", item.remote)
            pendientes.append(item)
            continue

        md5_local = manifest.md5_de(item) if manifest is not None else ""
        if md5_local and remoto.md5 and md5_local != remoto.md5:
            _log.warning("resubo %s: el MD5 del bucket no coincide con el del "
                         "manifiesto (bucket %s, manifiesto %s)",
                         item.remote, remoto.md5, md5_local)
            pendientes.append(item)
            continue

        hechos.append(item)

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

    def upload_url(self, remote: str, size: int) -> str:  # pragma: no cover
        """URL a la que abrir la sesión resumable."""
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

    def __init__(self, bucket: str, auth, *, base: str | None = None):
        if not bucket:
            raise ValueError("falta el nombre del bucket")
        self.bucket = bucket
        self.auth = auth
        self.base = (base or self.BASE).rstrip("/")

    def upload_url(self, remote: str, size: int) -> str:
        objeto = urllib.parse.quote(remote.lstrip("/"), safe="/")
        return f"{self.base}/{self.bucket}/{objeto}"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.auth.access_token()}"}

    def recover_auth(self) -> bool:
        # Fuerza el refresco; si el usuario revocó el acceso, `access_token`
        # levanta AuthError y la subida falla con un mensaje que se entiende.
        self.auth.access_token(force_refresh=True)
        return True

    def listar_remotos(self, prefix: str) -> dict[str, RemoteObject] | None:
        return listar_objetos_remotos(self.bucket, prefix, self.auth,
                                      base=self.base)


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

    def upload_url(self, remote: str, size: int) -> str:
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

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._done: dict[str, dict] = {}
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
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "done": self._done}, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass  # el manifiesto es una optimización, no puede tumbar la subida

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
            self._flush_locked()

    def hidratar(self, items: Sequence[UploadItem],
                 remotos: dict[str, "RemoteObject"]) -> int:
        """Anota como hechos los objetos que el bucket ya tenía.

        Reconstruye el manifiesto cuando se ha perdido (carpeta copiada a otro
        disco, reinstalación, borrado a mano). No es imprescindible —la
        reconciliación contra el bucket ya funciona sin esto— pero deja el
        registro local coherente con la realidad y guarda el MD5 del bucket,
        que en la siguiente pasada permite verificar sin releer nada.

        Se escribe **una sola vez** al final, no por fichero: son cientos de
        entradas y `mark` reescribe el JSON entero cada vez.

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
                self._flush_locked()
        return nuevas


# --------------------------------------------------------------------------
# Subida de un fichero (protocolo resumable de GCS)
# --------------------------------------------------------------------------

def _open_session(url: str, extra: dict[str, str] | None = None,
                  content_type: str = "application/octet-stream") -> str:
    """Inicia la sesión resumable y devuelve la URI de sesión."""
    req = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={
            "x-goog-resumable": "start",
            "Content-Type": content_type,
            "Content-Length": "0",
            "User-Agent": USER_AGENT,
            **(extra or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        location = resp.headers.get("Location")
    if not location:
        raise RuntimeError("GCS no devolvió Location al abrir la sesión resumable")
    return location


def _committed_offset(session_uri: str, total: int,
                      extra: dict[str, str] | None = None) -> int:
    """Pregunta a GCS cuántos bytes tiene ya confirmados.

    Es lo que permite reanudar: se manda un PUT vacío con `bytes */total` y
    GCS responde 308 con el rango recibido.
    """
    req = urllib.request.Request(
        session_uri,
        data=b"",
        method="PUT",
        headers={
            "Content-Range": f"bytes */{total}",
            "Content-Length": "0",
            "User-Agent": USER_AGENT,
            **(extra or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status in (200, 201):
                return total  # ya estaba completo
            return 0
    except urllib.error.HTTPError as exc:
        if exc.code != 308:
            raise
        # `Range: bytes=0-N` significa que tiene confirmados N+1 bytes. Si no
        # viene la cabecera, no tiene nada.
        rng = exc.headers.get("Range")
        if not rng or "-" not in rng:
            return 0
        return int(rng.rsplit("-", 1)[1]) + 1


def _put_chunk(session_uri: str, chunk: bytes, start: int, total: int,
               extra: dict[str, str] | None = None) -> bool:
    """Sube un trozo. Devuelve True si con esto el objeto quedó completo."""
    if total == 0:
        # Objeto vacío: no hay rango que declarar. `bytes 0--1/0` sería lo que
        # saldría de la fórmula general y GCS lo rechaza; el protocolo pide
        # `bytes */0` para cerrar un objeto de longitud cero.
        content_range = "bytes */0"
    else:
        content_range = f"bytes {start}-{start + len(chunk) - 1}/{total}"
    req = urllib.request.Request(
        session_uri,
        data=chunk,
        method="PUT",
        headers={
            "Content-Range": content_range,
            "Content-Length": str(len(chunk)),
            "User-Agent": USER_AGENT,
            **(extra or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as exc:
        if exc.code == 308:
            return False  # aceptado, faltan trozos
        raise


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


def _needs_new_session(exc: Exception) -> bool:
    """401/403: la autorización de esta sesión ya no vale, hay que pedir otra.

    Con vuelos que tardan horas es esperable que la signed URL o el token del
    proveedor caduquen a mitad. No es un error definitivo: se pide una URL nueva
    y se abre otra sesión resumable.
    """
    return isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        # 408 timeout, 429 rate limit, 5xx del lado servidor. 401/403 también,
        # porque se resuelven reabriendo sesión (ver `_needs_new_session`); sin
        # esto, el reintento con credencial fresca nunca llegaba a ejecutarse.
        return (exc.code in (401, 403, 408, 429)
                or 500 <= exc.code < 600)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))


def upload_file(item: UploadItem, provider: UrlProvider, *,
                on_bytes: Callable[[int], None] | None = None,
                should_stop: Callable[[], bool] | None = None,
                on_retry: Callable[[], None] | None = None) -> str:
    """Sube un fichero completo. Devuelve su MD5 en base64.

    Reintenta con backoff exponencial ante errores de red o 5xx, reanudando
    desde el offset que GCS confirme — no desde el principio.

    `on_retry` se avisa en cada reintento para poder contarlos: son la señal de
    que la red va mal, y sin contarlos un «tardó tres horas» no se distingue de
    un «tardó tres horas porque hubo 400 microcortes».
    """
    total = item.size
    session_uri: str | None = None
    delay = 1.0
    empezado = time.monotonic()

    for attempt in range(MAX_RETRIES):
        try:
            if session_uri is None:
                session_uri = _open_session(provider.upload_url(item.remote, total),
                                            provider.headers())
                offset = 0
            else:
                offset = _committed_offset(session_uri, total, provider.headers())
                _log.info("reanudo %s en el byte %d de %d (%.0f%% ya confirmado "
                          "en el bucket)", item.remote, offset, total,
                          100.0 * offset / total if total else 100.0)

            if total == 0:
                # Un fichero vacío se cierra con un PUT de longitud cero.
                _put_chunk(session_uri, b"", 0, 0, provider.headers())
                return _file_md5_b64(item.local)

            with open(item.local, "rb") as fh:
                fh.seek(offset)
                while offset < total:
                    if should_stop is not None and should_stop():
                        raise InterruptedError("subida cancelada")
                    chunk = fh.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    # Las cabeceras se piden por trozo: en un fichero de varios
                    # GB el token puede caducar entre el primero y el último.
                    _put_chunk(session_uri, chunk, offset, total, provider.headers())
                    offset += len(chunk)
                    if on_bytes is not None:
                        on_bytes(len(chunk))

            duracion = time.monotonic() - empezado
            _log.debug("subido %s (%.1f MB en %.1fs%s)", item.remote,
                       total / 1024 ** 2, duracion,
                       f", {attempt} reintento(s)" if attempt else "")
            return _file_md5_b64(item.local)

        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001 - se reclasifica justo debajo
            if attempt == MAX_RETRIES - 1 or not _is_retryable(exc):
                _log.error("%s FALLA definitivamente tras %d intento(s): %s: %s",
                           item.remote, attempt + 1, type(exc).__name__, exc)
                raise
            if on_retry is not None:
                on_retry()
            _log.warning("%s: %s: %s — reintento %d/%d en %.0fs",
                         item.remote, type(exc).__name__, exc,
                         attempt + 1, MAX_RETRIES - 1, delay)
            if _needs_new_session(exc):
                # 401/403: la autorización murió a mitad. Con OAuth basta
                # refrescar el token y la sesión resumable sigue viva (los
                # bytes confirmados en GCS no se pierden). Con signed URL hay
                # que pedir otra y abrir sesión nueva.
                try:
                    if not provider.recover_auth():
                        session_uri = None
                        _log.info("%s: credencial caducada, abro sesión nueva",
                                  item.remote)
                    else:
                        _log.info("%s: token refrescado, la sesión resumable sigue",
                                  item.remote)
                except Exception as exc2:  # noqa: BLE001 - el error original manda
                    session_uri = None
                    _log.warning("%s: no se pudo recuperar la autorización (%s)",
                                 item.remote, exc2)
            time.sleep(delay)
            delay = min(delay * 2, 30)

    raise RuntimeError(f"no se pudo subir {item.remote}")  # pragma: no cover


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
    # Si se pudo consultar el bucket. False = se subió a ciegas, fiándose solo
    # del manifiesto local, y por tanto el `skipped` puede quedarse corto.
    reconciliado: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def mbps(self) -> float:
        if self.elapsed <= 0:
            return 0.0
        return (self.bytes_sent * 8) / self.elapsed / 1_000_000


def upload_plan(plan: UploadPlan, provider: UrlProvider, *,
                concurrency: int = DEFAULT_CONCURRENCY,
                manifest: Manifest | None = None,
                on_progress: Callable[[str], None] | None = None,
                on_stats: Callable[[dict], None] | None = None,
                should_stop: Callable[[], bool] | None = None,
                remotos: dict[str, RemoteObject] | None = None) -> UploadResult:
    """Sube todos los ficheros del plan, en paralelo, reanudando y sin repetir.

    Antes de mover un byte se pregunta al bucket qué hay ya en el destino y se
    descarta de la lista (ver `reconciliar`). Eso es lo que hace que volver a
    elegir la misma carpeta cueste dos segundos en vez de media hora, incluso
    si el manifiesto local se ha perdido.

    `on_progress` recibe líneas ya formateadas, listas para la UI. No se le
    manda un evento por chunk: con 8,7 GB serían miles de refrescos y la
    interfaz se pasaría el rato repintando en vez de subiendo. El detalle fino
    (fichero a fichero, reintentos, cortes) va al log, no a la UI.

    `remotos` permite inyectar un inventario ya consultado, sobre todo para
    poder probar la reconciliación sin red. La pantalla previa NO lo reutiliza:
    `cloud_prepare` y `cloud_upload` son dos llamadas independientes del bridge
    y entre una y otra el operador puede tardar lo que quiera, así que el
    inventario se vuelve a pedir al empezar de verdad — dos segundos a cambio
    de no decidir sobre una foto caducada del bucket.
    """
    upload_log.get_logger()  # asegura que hay fichero donde escribir
    result = UploadResult()
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
        pending, hechos = reconciliar(plan, remotos, manifest)
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

    sent_lock = threading.Lock()
    counter = {"bytes": 0, "files": 0, "retries": 0, "ultimo_aviso": 0.0}
    started = time.monotonic()

    def _stats(final: bool = False) -> dict:
        """Foto del progreso, pensada para pintarla tal cual."""
        elapsed = max(time.monotonic() - started, 1e-6)
        enviados = counter["bytes"]
        bps = enviados / elapsed
        restantes = max(bytes_pendientes - enviados, 0)
        return {
            "files_done": counter["files"],
            "files_total": len(pending),
            "bytes_done": enviados,
            "bytes_total": bytes_pendientes,
            "elapsed": elapsed,
            # ETA a la velocidad media de lo que va de subida. Se manda `None`
            # hasta tener 3 s de muestra: al principio la media oscila tanto que
            # el número saltaría de «2 min» a «4 horas» y no serviría de nada.
            "eta": (restantes / bps) if (bps > 0 and elapsed > 3) else None,
            "mbps": bps * 8 / 1_000_000,
            "retries": counter["retries"],
            "final": final,
        }

    def _avisar(forzar: bool = False) -> None:
        """Manda el progreso a la UI, como mucho una vez por segundo.

        El throttle es por TIEMPO y no por número de ficheros: un fichero de 2
        GB tarda minutos, y contando ficheros la pantalla se quedaría clavada
        justo cuando el usuario más mira si aquello sigue vivo.

        Reservar el turno y anotar la marca de tiempo ocurre **dentro del
        lock**: los 16 hilos de subida llaman aquí a la vez, y comprobar fuera
        dejaba pasar a varios en la misma ventana. `on_stats` acaba en
        `evaluate_js`, así que un aviso duplicado son dos saltos al motor JS,
        no solo una línea de más.
        """
        if on_stats is None:
            return
        with sent_lock:
            ahora = time.monotonic()
            if not forzar and ahora - counter["ultimo_aviso"] < 1.0:
                return
            counter["ultimo_aviso"] = ahora
            foto = _stats(final=forzar)
        # La llamada, fuera del lock: cruza a la UI y no debe bloquear a los
        # otros 15 hilos mientras tanto.
        on_stats(foto)

    def _bump(n: int) -> None:
        with sent_lock:
            counter["bytes"] += n
        _avisar()

    def _retry() -> None:
        with sent_lock:
            counter["retries"] += 1

    def _one(item: UploadItem) -> tuple[UploadItem, str | None, str | None]:
        try:
            md5 = upload_file(item, provider, on_bytes=_bump,
                              should_stop=should_stop, on_retry=_retry)
            return item, md5, None
        except InterruptedError:
            return item, None, "cancelado"
        except Exception as exc:  # noqa: BLE001 - se reporta, no se traga
            return item, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(_one, item) for item in pending]
        for fut in as_completed(futures):
            item, md5, err = fut.result()
            if err is None and md5 is not None:
                manifest.mark(item, md5)
                result.uploaded += 1
            else:
                result.failed.append((item.remote, err or "error desconocido"))

            counter["files"] += 1
            _avisar()
            if on_progress is not None and counter["files"] % 25 == 0:
                done_gb = counter["bytes"] / 1024 ** 3
                elapsed = max(time.monotonic() - started, 1e-6)
                speed = (counter["bytes"] * 8) / elapsed / 1_000_000
                linea = (f"{counter['files']}/{len(pending)} · "
                         f"{done_gb:.2f} GB · {speed:.0f} Mbps")
                on_progress(linea)
                _log.info("%s · %d reintentos acumulados", linea, counter["retries"])

    _avisar(forzar=True)
    result.bytes_sent = counter["bytes"]
    result.elapsed = time.monotonic() - started
    result.retries = counter["retries"]

    _log.info("FIN · %d subidos · %d omitidos · %.2f GB en %.0fs (%.0f Mbps) · "
              "%d reintentos · %d fallos",
              result.uploaded, result.skipped, result.bytes_sent / 1024 ** 3,
              result.elapsed, result.mbps, result.retries, len(result.failed))
    for objeto, motivo in result.failed:
        _log.error("FALLÓ %s → %s", objeto, motivo)
    return result
