"""Capa de almacenamiento intercambiable (backend GCS por API).

Estos tests fijan el contrato de `Almacen` sobre `AlmacenGCS`, usando un doble
de prueba en memoria que imita SOLO la parte de la API de
`google.cloud.storage` que usa `AlmacenGCS` (`Client.bucket`, `Bucket.blob`,
`Bucket.list_blobs`, `Bucket.copy_blob`, `Blob.exists/upload_from_filename/
download_to_filename/delete`). No se usa `unittest.mock.patch` sobre el SDK
real ni se toca red: el test pasa aunque `google-cloud-storage` no esté
instalado, igual que el propio `AlmacenGCS` (import perezoso).
"""
from pathlib import Path

import pytest

from atom_core.almacen import AlmacenLocal
from atom_core.almacen_gcs import AlmacenGCS


class BlobFalso:
    def __init__(self, bucket: "BucketFalso", nombre: str):
        self.bucket = bucket
        self.name = nombre

    def exists(self) -> bool:
        return self.name in self.bucket.objetos

    def upload_from_filename(self, ruta_local: str) -> None:
        self.bucket.objetos[self.name] = Path(ruta_local).read_bytes()

    def download_to_filename(self, ruta_local: str) -> None:
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        Path(ruta_local).write_bytes(self.bucket.objetos[self.name])

    def delete(self) -> None:
        del self.bucket.objetos[self.name]

    def reload(self) -> None:
        # Metadatos "reales" en este doble: el tamaño está disponible sin
        # descargar el contenido (a diferencia de `download_to_filename`).
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.bucket.reloads = getattr(self.bucket, "reloads", 0) + 1
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}
        self.copias_server_side = 0
        self.descargas = 0

    def blob(self, nombre: str) -> BlobFalso:
        return BlobFalso(self, nombre)

    def list_blobs(self, prefix: str = ""):
        for nombre in self.objetos:
            if nombre.startswith(prefix):
                yield BlobFalso(self, nombre)

    def copy_blob(self, blob_origen: BlobFalso, bucket_destino: "BucketFalso", nombre_destino: str):
        self.copias_server_side += 1
        bucket_destino.objetos[nombre_destino] = self.objetos[blob_origen.name]
        return BlobFalso(bucket_destino, nombre_destino)


class ClienteFalso:
    def __init__(self, bucket: BucketFalso):
        self._bucket = bucket

    def bucket(self, nombre: str) -> BucketFalso:
        return self._bucket


def _almacen(prefijo_raiz: str = "") -> tuple[AlmacenGCS, BucketFalso]:
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS("bucket-falso", prefijo_raiz=prefijo_raiz, cliente=cliente)
    return almacen, bucket


def test_import_sin_sdk_instalado():
    # Si esto se pudo importar arriba, el import perezoso ya está probado:
    # este proceso no tiene google-cloud-storage instalado.
    with pytest.raises(ImportError):
        import google.cloud.storage  # noqa: F401


def test_constructor_sin_cliente_lanza_error_claro_si_falta_sdk():
    with pytest.raises(ImportError, match="google-cloud-storage"):
        AlmacenGCS("un-bucket")


def test_listar_recursivo_rutas_relativas_y_ordenadas():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/sub/hondo/c.txt"] = b"c"
    bucket.objetos["raiz/a.txt"] = b"a"
    bucket.objetos["raiz/sub/b.txt"] = b"b"
    assert almacen.listar("") == ["a.txt", "sub/b.txt", "sub/hondo/c.txt"]


def test_listar_respeta_prefijo():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/a.txt"] = b"a"
    bucket.objetos["raiz/sub/b.txt"] = b"b"
    bucket.objetos["raiz/sub/hondo/c.txt"] = b"c"
    assert almacen.listar("sub") == ["sub/b.txt", "sub/hondo/c.txt"]


def test_existe_true_false():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/a.txt"] = b"a"
    assert almacen.existe("a.txt") is True
    assert almacen.existe("no_existe.txt") is False


def test_abrir_local_descarga_y_borra_temporal(tmp_path):
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/a.txt"] = b"contenido"
    with almacen.abrir_local("a.txt") as ruta:
        assert isinstance(ruta, Path)
        assert ruta.exists()
        assert ruta.read_bytes() == b"contenido"
        ruta_capturada = ruta
    assert not ruta_capturada.exists()


def test_abrir_local_borra_temporal_incluso_si_el_bloque_lanza():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/a.txt"] = b"contenido"
    ruta_capturada = None
    with pytest.raises(RuntimeError):
        with almacen.abrir_local("a.txt") as ruta:
            ruta_capturada = ruta
            raise RuntimeError("boom")
    assert ruta_capturada is not None
    assert not ruta_capturada.exists()


def test_publicar_sube_con_clave_completa_incluyendo_prefijo_raiz(tmp_path):
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    origen = tmp_path / "origen.txt"
    origen.write_text("hola")
    almacen.publicar(origen, "sub/destino.txt")
    assert bucket.objetos["raiz/sub/destino.txt"] == b"hola"


def test_mover_es_server_side_no_descarga_bytes_al_cliente():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/origen.txt"] = b"contenido"
    almacen.mover("origen.txt", "sub/destino.txt")
    assert "raiz/origen.txt" not in bucket.objetos
    assert bucket.objetos["raiz/sub/destino.txt"] == b"contenido"
    assert bucket.copias_server_side == 1


def test_borrar():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/a.txt"] = b"a"
    almacen.borrar("a.txt")
    assert "raiz/a.txt" not in bucket.objetos


def test_tamano_lee_metadatos_sin_descargar():
    almacen, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/a.txt"] = b"contenido de sobra"
    assert almacen.tamano("a.txt") == len(b"contenido de sobra")
    assert bucket.descargas == 0
    assert getattr(bucket, "reloads", 0) == 1


def test_paridad_semantica_listar_existe_local_vs_gcs(tmp_path):
    """Mismo contenido inicial, mismos resultados de listar/existe en ambos
    backends: la frontera `Almacen` es intercambiable de verdad."""
    raiz_local = tmp_path / "almacen"
    raiz_local.mkdir()
    contenido = {
        "a.txt": b"a",
        "sub/b.txt": b"b",
        "sub/hondo/c.txt": b"c",
    }
    for relativo, datos in contenido.items():
        ruta = raiz_local / relativo
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(datos)

    almacen_local = AlmacenLocal(raiz_local)

    almacen_gcs, bucket = _almacen(prefijo_raiz="raiz")
    for relativo, datos in contenido.items():
        bucket.objetos[f"raiz/{relativo}"] = datos

    assert almacen_local.listar("") == almacen_gcs.listar("")
    assert almacen_local.listar("sub") == almacen_gcs.listar("sub")
    for relativo in list(contenido) + ["no_existe.txt"]:
        assert almacen_local.existe(relativo) == almacen_gcs.existe(relativo)
