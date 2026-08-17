"""I/O contra la API JSON de GCS, con el token del metadata server.

Solo stdlib, igual que `atom_core/cloud_upload.py`: añadir
`google-cloud-storage` a la imagen del Job por dos llamadas HTTP no compensa.

Credenciales: la SA del Job
(`217557350193-compute@developer.gserviceaccount.com`) tiene
`roles/storage.admin` a nivel proyecto, asi que puede escribir en
`plantas_pv_nl` aunque su binding directo en el bucket sea solo
`objectViewer` (los roles de proyecto son aditivos).

Todo aqui es FAIL-OPEN hacia el pipeline: publicar el estadillo es un extra,
y ningun fallo de red puede tumbar una organizacion de 40 000 imagenes. Pero
es FAIL-CLOSED hacia el bucket: ante la duda, no se escribe.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

_log = logging.getLogger(__name__)

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_TIMEOUT = 30


def token_metadata(*, abrir_url=None) -> str | None:
    """Access token de la SA del contenedor. `None` fuera de GCP."""
    abrir = abrir_url or urllib.request.urlopen
    req = urllib.request.Request(_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
    try:
        with abrir(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")).get("access_token")
    except Exception as exc:  # noqa: BLE001 - sin token no se publica, y ya
        _log.warning("gcs_publicar: sin token del metadata server (%s)", exc)
        return None


def _cabeceras(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def prefijo_existe(bucket: str, prefijo: str, *, abrir_url=None, token: str | None = None) -> bool:
    """¿Hay al menos un objeto bajo `prefijo`?

    Es el GUARD del bucket de plantas: `gs://plantas_pv_nl` es solo para
    plantas, y una planta cuyo nombre no haga round-trip limpio desde el
    prefijo del organizer (`OCAÑA` -> `OCANA`) crearia una carpeta basura al
    lado de la buena. Si el prefijo no existe ya, no se escribe.

    Cualquier fallo -> `False`. No escribir de mas es mas barato que limpiar.
    """
    abrir = abrir_url or urllib.request.urlopen
    params = urllib.parse.urlencode({"prefix": prefijo, "maxResults": 1})
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?{params}"
    req = urllib.request.Request(url, headers=_cabeceras(token))
    try:
        with abrir(req, timeout=_TIMEOUT) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("items"))
    except Exception as exc:  # noqa: BLE001
        _log.warning("gcs_publicar: no se pudo comprobar %s/%s (%s)", bucket, prefijo, exc)
        return False


def subir_objeto(bucket: str, objeto: str, datos: bytes, *,
                 abrir_url=None, token: str | None = None) -> bool:
    """Sube `datos` como `objeto`. `True` si la API lo acepto."""
    abrir = abrir_url or urllib.request.urlopen
    params = urllib.parse.urlencode({"uploadType": "media", "name": objeto})
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?{params}"
    cabeceras = {**_cabeceras(token), "Content-Type": "application/octet-stream"}
    req = urllib.request.Request(url, data=datos, headers=cabeceras, method="POST")
    try:
        with abrir(req, timeout=_TIMEOUT) as r:
            json.loads(r.read().decode("utf-8"))
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning("gcs_publicar: fallo subiendo %s/%s (%s)", bucket, objeto, exc)
        return False
