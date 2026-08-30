"""Backend `Almacen` sobre GCS por API (sin gcsfuse de por medio).

Implementa el mismo contrato que `atom_core.almacen.Almacen`, pero contra un
bucket de Google Cloud Storage vía `google-cloud-storage`. Es el backend que
sustituye al mount gcsfuse del Cloud Run Job: solo la imagen del Job trae ese
SDK instalado, así que el import se hace de forma perezosa dentro del
constructor para que el resto del código (y sus tests) puedan importar este
módulo sin arrastrar la dependencia.

Las rutas de la API pública son siempre RELATIVAS a `prefijo_raiz` y usan `/`
como separador; internamente se componen con `prefijo_raiz` para formar la
clave completa del objeto en el bucket.
"""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path


def _normalizar(ruta: str) -> str:
    """Quita separadores sobrantes y normaliza a `/`."""
    return str(ruta).replace("\\", "/").strip("/")


class AlmacenGCS:
    """`Almacen` sobre un bucket GCS, vía `google-cloud-storage`.

    `cliente` es inyectable (para tests con un doble en memoria); si no se
    pasa, se crea un `storage.Client()` real.
    """

    def __init__(self, bucket: str, prefijo_raiz: str = "", cliente=None):
        if cliente is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise ImportError(
                    "Falta 'google-cloud-storage': solo la imagen del Cloud "
                    "Run Job la incluye. Instálala o inyecta un `cliente` "
                    "de prueba para usar AlmacenGCS fuera de esa imagen."
                ) from exc
            cliente = storage.Client()
        self.cliente = cliente
        self.bucket = self.cliente.bucket(bucket)
        self.prefijo_raiz = _normalizar(prefijo_raiz)

    def _clave(self, relativo: str) -> str:
        relativo = _normalizar(relativo)
        if not self.prefijo_raiz:
            return relativo
        if not relativo:
            return self.prefijo_raiz
        return f"{self.prefijo_raiz}/{relativo}"

    def _relativa(self, clave: str) -> str:
        clave = _normalizar(clave)
        if self.prefijo_raiz and clave.startswith(self.prefijo_raiz + "/"):
            return clave[len(self.prefijo_raiz) + 1:]
        if clave == self.prefijo_raiz:
            return ""
        return clave

    def listar(self, prefijo: str) -> list[str]:
        clave = self._clave(prefijo)
        encontradas = [
            self._relativa(blob.name)
            for blob in self.bucket.list_blobs(prefix=clave)
        ]
        return sorted(encontradas)

    def existe(self, ruta: str) -> bool:
        blob = self.bucket.blob(self._clave(ruta))
        return blob.exists()

    @contextmanager
    def abrir_local(self, ruta: str):
        blob = self.bucket.blob(self._clave(ruta))
        sufijo = Path(ruta).suffix
        descriptor, nombre_temporal = tempfile.mkstemp(suffix=sufijo)
        import os

        os.close(descriptor)
        ruta_temporal = Path(nombre_temporal)
        try:
            blob.download_to_filename(str(ruta_temporal))
            yield ruta_temporal
        finally:
            ruta_temporal.unlink(missing_ok=True)

    def publicar(self, ruta_local: Path, destino: str) -> None:
        blob = self.bucket.blob(self._clave(destino))
        blob.upload_from_filename(str(ruta_local))

    def mover(self, origen: str, destino: str) -> None:
        blob_origen = self.bucket.blob(self._clave(origen))
        self.bucket.copy_blob(blob_origen, self.bucket, self._clave(destino))
        blob_origen.delete()

    def borrar(self, ruta: str) -> None:
        blob = self.bucket.blob(self._clave(ruta))
        blob.delete()
