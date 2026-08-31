"""
Migración `gs://…` de `process_one_image` (`pipeline.py:200-...`).

HOY (antes del fix) hace `Image.open(path)` y `img.save(cfg.output_path, ...)`
con rutas crudas: sobre `gs://` `Image.open` revienta con `FileNotFoundError`
en TODAS las imágenes y el `_CROP` nunca llega al bucket.

Mismo doble en memoria del backend GCS que `tests/test_giro_rgb_almacen.py`
(plantilla). NO se modifica `BucketFalso.list_blobs` (replica a propósito el
`startswith` literal de GCS) ni el `_NotFound` que lanza
`BlobFalso.download_to_filename` cuando el objeto no existe.

IMPORTANTE: `process_one_image` es una función de MÓDULO que en producción
viaja picklada a un `ProcessPoolExecutor` con `spawn` (`utils.run_batch`). Un
proceso hijo `spawn` NO hereda el registro `_ALMACENES` del padre, así que un
test que sembrara el bucket falso y pasara por `run_batch`/el pool de verdad
NO vería ese almacén falso dentro del hijo. Por eso aquí se llama a
`process_one_image` DIRECTAMENTE en el proceso del test (donde el registro
falso sí está activo), sin pasar por `run_batch`.
"""
import io

import pytest
from PIL import Image

import atom_core.almacen as almacen_mod
from pipeline import process_one_image, ImageProcessConfig


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS (idéntico a test_giro_rgb_almacen.py) ---

_NotFound = type("NotFound", (Exception,), {})


class BlobFalso:
    def __init__(self, bucket, nombre):
        self.bucket = bucket
        self.name = nombre

    def exists(self) -> bool:
        return self.name in self.bucket.objetos

    def upload_from_filename(self, ruta_local: str) -> None:
        from pathlib import Path
        self.bucket.uploads += 1
        self.bucket.objetos[self.name] = Path(ruta_local).read_bytes()

    def download_to_filename(self, ruta_local: str) -> None:
        if self.name not in self.bucket.objetos:
            raise _NotFound(self.name)
        from pathlib import Path
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
        self.uploads = 0

    def blob(self, nombre: str) -> BlobFalso:
        return BlobFalso(self, nombre)

    def list_blobs(self, prefix: str = ""):
        for nombre in list(self.objetos):
            if nombre.startswith(prefix):
                yield BlobFalso(self, nombre)


class ClienteFalso:
    def __init__(self, bucket: BucketFalso):
        self._bucket = bucket

    def bucket(self, nombre: str) -> BucketFalso:
        return self._bucket


def _sembrar_almacen_gcs(bucket_nombre: str) -> BucketFalso:
    from atom_core.almacen_gcs import AlmacenGCS

    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


def _jpg_bytes(size=(200, 100), color=(120, 130, 140)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img.close()
    return buf.getvalue()


def _tamano(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


# --- Bug A: entrada y salida en gs:// ------------------------------------------

def test_process_one_image_entrada_y_salida_en_gcs_publica_el_recorte():
    bucket = _sembrar_almacen_gcs("bucket-process-one-image")
    bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"] = _jpg_bytes(size=(200, 100))

    cfg = ImageProcessConfig(
        output_path="gs://bucket-process-one-image/RGB/PB1_V1/DJI_0001_CROP.JPG",
        quality=80,
        crop_box=(50, 25, 150, 75),  # 100x50
    )
    resultado = process_one_image(
        "gs://bucket-process-one-image/RGB/PB1_V1/DJI_0001.JPG", cfg
    )

    assert resultado == cfg.output_path
    assert "RGB/PB1_V1/DJI_0001_CROP.JPG" in bucket.objetos, (
        "el _CROP nunca llegó a publicarse en el bucket: process_one_image "
        "sigue guardando contra la ruta gs:// cruda"
    )
    assert _tamano(bucket.objetos["RGB/PB1_V1/DJI_0001_CROP.JPG"]) == (100, 50), (
        "el recorte no se aplicó correctamente antes de publicar")
    # El original de entrada no se toca.
    assert _tamano(bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"]) == (200, 100)


def test_process_one_image_local_sigue_escribiendo_el_fichero_directamente(
    tmp_path, monkeypatch
):
    """Camino local: paridad exacta con la app de escritorio, sin temporales
    ni copias intermedias. Si `cfg.output_path` no es `gs://`, no debe pasar
    por `tempfile.TemporaryDirectory` en absoluto."""
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    src.write_bytes(_jpg_bytes(size=(200, 100)))

    import tempfile as tempfile_mod

    def _boom(*args, **kwargs):
        raise AssertionError(
            "process_one_image no debe usar tempfile.TemporaryDirectory "
            "cuando la salida es una ruta local"
        )

    monkeypatch.setattr(tempfile_mod, "TemporaryDirectory", _boom)

    cfg = ImageProcessConfig(output_path=str(dst), quality=80)
    resultado = process_one_image(str(src), cfg)

    assert resultado == str(dst)
    assert dst.exists()
    assert _tamano(dst.read_bytes()) == (200, 100)
