"""`atom_core.almacen.editar_en_sitio`: primitiva para operar sobre un fichero
con un binario externo (exiftool, dji_irp, pyexiv2, PIL...) que EXIGE una ruta
de fichero real y lo modifica in-place.

Mismo estilo de doble en memoria del backend GCS que
`tests/test_struct_primitivas_almacen.py`: se siembra la caché de
`abrir_almacen` con un `AlmacenGCS` de prueba, sin SDK real y sin red.
"""
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
from atom_core.almacen_gcs import AlmacenGCS


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS (idéntico al de
# test_struct_primitivas_almacen.py; NO tocar `list_blobs`, su `startswith`
# literal replica A PROPÓSITO el comportamiento de GCS real) -----------------

class BlobFalso:
    def __init__(self, bucket, nombre):
        self.bucket = bucket
        self.name = nombre

    def exists(self) -> bool:
        return self.name in self.bucket.objetos

    def upload_from_filename(self, ruta_local: str) -> None:
        if self.bucket.fallar_upload:
            raise OSError(f"fallo simulado subiendo {self.name}")
        self.bucket.objetos[self.name] = Path(ruta_local).read_bytes()

    def download_to_filename(self, ruta_local: str) -> None:
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        Path(ruta_local).write_bytes(self.bucket.objetos[self.name])

    def delete(self) -> None:
        del self.bucket.objetos[self.name]

    def reload(self) -> None:
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}
        self.fallar_upload = False

    def blob(self, nombre: str) -> BlobFalso:
        return BlobFalso(self, nombre)

    def list_blobs(self, prefix: str = ""):
        for nombre in list(self.objetos):
            if nombre.startswith(prefix):
                yield BlobFalso(self, nombre)

    def copy_blob(self, blob_origen, bucket_destino, nombre_destino):
        bucket_destino.objetos[nombre_destino] = self.objetos[blob_origen.name]
        return BlobFalso(bucket_destino, nombre_destino)


class ClienteFalso:
    def __init__(self, bucket: BucketFalso):
        self._bucket = bucket

    def bucket(self, nombre: str) -> BucketFalso:
        return self._bucket


def _sembrar_almacen_gcs(bucket_nombre: str) -> BucketFalso:
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


# --- local -------------------------------------------------------------------

def test_editar_en_sitio_local_edita_el_fichero_original(tmp_path):
    ruta = tmp_path / "a.JPG"
    ruta.write_bytes(b"original")

    with almacen_mod.editar_en_sitio(str(ruta)) as ruta_local:
        ruta_local.write_bytes(b"editado")

    assert ruta.read_bytes() == b"editado"


def test_editar_en_sitio_local_cede_la_ruta_original_sin_copias(tmp_path):
    ruta = tmp_path / "a.JPG"
    ruta.write_bytes(b"original")

    with almacen_mod.editar_en_sitio(str(ruta)) as ruta_local:
        assert ruta_local == Path(str(ruta))  # identidad de path, no una copia

    # Ningún fichero extra ha aparecido junto al original (no hay copias/temporales).
    assert [p.name for p in tmp_path.glob("*.JPG")] == ["a.JPG"]


# --- gs:// ---------------------------------------------------------------

def test_editar_en_sitio_gcs_republica_al_salir_sin_excepcion(tmp_path):
    bucket = _sembrar_almacen_gcs("bucket-editar-ok")
    bucket.objetos["a.JPG"] = b"original"

    with almacen_mod.editar_en_sitio("gs://bucket-editar-ok/a.JPG") as ruta_local:
        assert ruta_local.read_bytes() == b"original"  # una sola descarga
        ruta_local.write_bytes(b"editado")

    assert bucket.objetos["a.JPG"] == b"editado"  # una sola subida, misma clave
    assert len(bucket.objetos) == 1  # sin claves temporales


def test_editar_en_sitio_gcs_si_el_bloque_lanza_no_publica(tmp_path):
    bucket = _sembrar_almacen_gcs("bucket-editar-excepcion")
    bucket.objetos["a.JPG"] = b"original"

    with pytest.raises(ValueError):
        with almacen_mod.editar_en_sitio("gs://bucket-editar-excepcion/a.JPG") as ruta_local:
            ruta_local.write_bytes(b"editado-pero-falla")
            raise ValueError("fallo simulado del binario externo")

    assert bucket.objetos["a.JPG"] == b"original"  # el remoto queda intacto


def test_editar_en_sitio_gcs_temporal_borrado_no_publica_y_lanza(tmp_path):
    bucket = _sembrar_almacen_gcs("bucket-editar-borrado")
    bucket.objetos["a.JPG"] = b"original"

    with pytest.raises(RuntimeError):
        with almacen_mod.editar_en_sitio("gs://bucket-editar-borrado/a.JPG") as ruta_local:
            ruta_local.unlink()  # el binario externo borra el fichero por error

    assert bucket.objetos["a.JPG"] == b"original"  # no se publica un objeto truncado


def test_editar_en_sitio_gcs_temporal_vacio_no_publica_y_lanza(tmp_path):
    bucket = _sembrar_almacen_gcs("bucket-editar-vacio")
    bucket.objetos["a.JPG"] = b"original"

    with pytest.raises(RuntimeError):
        with almacen_mod.editar_en_sitio("gs://bucket-editar-vacio/a.JPG") as ruta_local:
            ruta_local.write_bytes(b"")  # el binario externo trunca el fichero

    assert bucket.objetos["a.JPG"] == b"original"  # no se publica un objeto vacío


def test_editar_en_sitio_gcs_falla_publicar_no_pierde_el_original(tmp_path):
    """Si `almacen.publicar` (el upload del temporal editado) falla, el
    objeto remoto NO se pisa: perder una imagen por un fallo a medio camino
    es inaceptable."""
    bucket = _sembrar_almacen_gcs("bucket-editar-falla-upload")
    bucket.objetos["a.JPG"] = b"original"
    bucket.fallar_upload = True

    with pytest.raises(OSError):
        with almacen_mod.editar_en_sitio("gs://bucket-editar-falla-upload/a.JPG") as ruta_local:
            ruta_local.write_bytes(b"editado-pero-falla")

    assert bucket.objetos["a.JPG"] == b"original"  # el remoto queda intacto
