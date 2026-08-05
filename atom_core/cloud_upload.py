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

__all__ = [
    "UploadItem",
    "UploadPlan",
    "UploadResult",
    "UrlProvider",
    "GcsOAuthProvider",
    "SignedUrlProvider",
    "build_plan",
    "upload_plan",
]

# GCS exige que todo chunk intermedio de una subida resumable sea múltiplo de
# 256 KiB. 16 MiB es el compromiso habitual: suficientemente grande para no
# pagar una ida y vuelta por megabyte, suficientemente pequeño para que un
# corte no tire mucho trabajo.
_CHUNK_MULTIPLE = 256 * 1024
CHUNK_SIZE = 16 * 1024 * 1024
assert CHUNK_SIZE % _CHUNK_MULTIPLE == 0

# Subidas simultáneas. Por encima de ~8 la línea doméstica ya está saturada y
# solo se gana contención y timeouts.
DEFAULT_CONCURRENCY = 4

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
                should_stop: Callable[[], bool] | None = None) -> str:
    """Sube un fichero completo. Devuelve su MD5 en base64.

    Reintenta con backoff exponencial ante errores de red o 5xx, reanudando
    desde el offset que GCS confirme — no desde el principio.
    """
    total = item.size
    session_uri: str | None = None
    delay = 1.0

    for attempt in range(MAX_RETRIES):
        try:
            if session_uri is None:
                session_uri = _open_session(provider.upload_url(item.remote, total),
                                            provider.headers())
                offset = 0
            else:
                offset = _committed_offset(session_uri, total, provider.headers())

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

            return _file_md5_b64(item.local)

        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001 - se reclasifica justo debajo
            if attempt == MAX_RETRIES - 1 or not _is_retryable(exc):
                raise
            if _needs_new_session(exc):
                # 401/403: la autorización murió a mitad. Con OAuth basta
                # refrescar el token y la sesión resumable sigue viva (los
                # bytes confirmados en GCS no se pierden). Con signed URL hay
                # que pedir otra y abrir sesión nueva.
                try:
                    if not provider.recover_auth():
                        session_uri = None
                except Exception:  # noqa: BLE001 - el error original manda
                    session_uri = None
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
                should_stop: Callable[[], bool] | None = None) -> UploadResult:
    """Sube todos los ficheros del plan, en paralelo y reanudando.

    `on_progress` recibe líneas ya formateadas, listas para la UI. No se le
    manda un evento por chunk: con 8,7 GB serían miles de refrescos y la
    interfaz se pasaría el rato repintando en vez de subiendo.
    """
    result = UploadResult()
    if manifest is None:
        manifest = Manifest(plan.root / Manifest.FILENAME)

    pending = [i for i in plan.items if not manifest.is_done(i)]
    result.skipped = len(plan.items) - len(pending)

    if on_progress is not None:
        gb = plan.total_bytes / 1024 ** 3
        on_progress(f"{len(plan.items)} ficheros · {gb:.1f} GB · "
                    f"{result.skipped} ya subidos · {len(pending)} pendientes")

    if not pending:
        return result

    sent_lock = threading.Lock()
    counter = {"bytes": 0, "files": 0}
    started = time.monotonic()

    def _bump(n: int) -> None:
        with sent_lock:
            counter["bytes"] += n

    def _one(item: UploadItem) -> tuple[UploadItem, str | None, str | None]:
        try:
            md5 = upload_file(item, provider, on_bytes=_bump,
                              should_stop=should_stop)
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
            if on_progress is not None and counter["files"] % 25 == 0:
                done_gb = counter["bytes"] / 1024 ** 3
                elapsed = max(time.monotonic() - started, 1e-6)
                speed = (counter["bytes"] * 8) / elapsed / 1_000_000
                on_progress(f"{counter['files']}/{len(pending)} · "
                            f"{done_gb:.2f} GB · {speed:.0f} Mbps")

    result.bytes_sent = counter["bytes"]
    result.elapsed = time.monotonic() - started
    return result
