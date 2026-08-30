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


def _es_no_encontrado(exc: BaseException) -> bool:
    """True si `exc` es el "no existe" que lanza el SDK real de GCS al borrar
    un objeto que ya no está (`google.api_core.exceptions.NotFound`), NO un
    `FileNotFoundError` de Python. Import perezoso: el módulo `google` solo
    lo trae la imagen del Cloud Run Job, igual que en el constructor. Si el
    SDK no está instalado (tests, escritorio sin GCS) no se puede hacer el
    `isinstance`, así que se recurre al nombre de la clase — es lo único que
    un doble de pruebas puede imitar sin arrastrar la dependencia real."""
    try:
        from google.api_core.exceptions import NotFound
    except ImportError:
        NotFound = None
    if NotFound is not None and isinstance(exc, NotFound):
        return True
    return type(exc).__name__ == "NotFound"


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
        # `list_blobs(prefix=clave)` filtra por prefijo LITERAL de texto: pedir
        # ".../PB1" devuelve TAMBIÉN ".../PB10/..." (ambas empiezan por "PB1").
        # Sin cortar por frontera de segmento, `listar_subcarpetas` se inventa
        # una subcarpeta "0" (de recortar "PB10/foo") y consumidores como
        # `vuelos_del_destino`/`contar_imagenes_or_tmc` mezclan PBs distintos.
        # Filtrar aquí, después del `list_blobs`, para arreglarlo una vez para
        # todos los consumidores sin tocar el ahorro de red del prefijo real.
        encontradas = [
            self._relativa(blob.name)
            for blob in self.bucket.list_blobs(prefix=clave)
            if not clave or blob.name == clave or blob.name.startswith(clave + "/")
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
        clave_origen = self._clave(origen)
        clave_destino = self._clave(destino)
        if clave_origen == clave_destino:
            # Origen y destino son la MISMA clave: un no-op. Si se ejecutara
            # el copy_blob+delete de todos modos, el `delete()` borraría el
            # objeto recién "copiado" sobre sí mismo y el fichero desaparecería
            # sin dejar rastro (a diferencia de `os.replace`, que es atómico
            # en el camino local y no tiene este problema).
            return
        blob_origen = self.bucket.blob(clave_origen)
        self.bucket.copy_blob(blob_origen, self.bucket, clave_destino)
        blob_origen.delete()

    def borrar(self, ruta: str) -> None:
        blob = self.bucket.blob(self._clave(ruta))
        try:
            blob.delete()
        except FileNotFoundError:
            raise
        except Exception as exc:
            if _es_no_encontrado(exc):
                raise FileNotFoundError(ruta) from exc
            raise

    def tamano(self, ruta: str) -> int:
        """Tamaño en bytes leído de los METADATOS del blob (`reload()`), sin
        descargar el objeto. Es justo lo que faltaba para que `split_image`
        pudiera decidir RGB/térmica por tamaño sin bajarse la imagen entera
        dos veces (ver el `TODO 3790 F4` que este método cierra en pipeline.py)."""
        blob = self.bucket.blob(self._clave(ruta))
        blob.reload()
        return blob.size
