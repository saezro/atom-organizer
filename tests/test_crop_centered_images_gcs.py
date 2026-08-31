"""
Migración `gs://…` de la resolución de modelo en `RGBCropping.crop_centered_images`
(`pipeline.py`, bucle de detección de modelo antes de lanzar el recorte por lotes).

HOY (antes del fix) llama a `self.exif_management_obj.get_model(os.path.join(input_folder,
image), ...)` con la ruta cruda. `get_model` (`exif.py:110-138`) abre el fichero con
`open(filename, 'rb')` a pelo: sobre `gs://…` eso no es un fichero local y revienta.
`get_model` en sí NO se toca (debe seguir recibiendo una ruta de fichero real, igual
que `get_gimbal_yaw_pitch`); el fix es en el LLAMANTE, que ahora resuelve la ruta con
`almacen.abrir_para_lectura(...)` antes de invocarlo.

Mismo doble en memoria del backend GCS que `tests/test_giro_rgb_almacen.py` /
`tests/test_process_one_image_gcs.py`. NO se modifica `BucketFalso.list_blobs` (replica
a propósito el `startswith` literal de GCS) ni el `_NotFound` de
`BlobFalso.download_to_filename`.

`utils.run_batch` (que spawnea un `ProcessPoolExecutor`) se deja stubbeado: lo que se
prueba aquí es la resolución de `get_model`, que ocurre ANTES de construir el lote, no
el procesado en sí (ya cubierto por `test_process_one_image_gcs.py`).
"""
import os

import pytest
from PIL import Image

import atom_core.almacen as almacen_mod
import pipeline
import utils as utils_mod


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


def _noop_progress():
    import types
    return types.SimpleNamespace(emit=lambda *a, **k: None)


# --- Doble en memoria del backend GCS ------------------------------------------

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


def _jpg_bytes(size=(200, 100)) -> bytes:
    import io
    img = Image.new("RGB", size, color=(120, 130, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img.close()
    return buf.getvalue()


def test_get_model_recibe_ruta_local_existente_cuando_el_input_es_gcs(logger, monkeypatch):
    bucket = _sembrar_almacen_gcs("bucket-model-detection")
    bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"] = _jpg_bytes()

    obj = pipeline.RGBCropping(logger)

    llamadas = []
    rutas_existian = []

    def _get_model_spy(filename, progress_callback):
        llamadas.append(filename)
        rutas_existian.append(os.path.isfile(filename))
        return "FC6310"

    monkeypatch.setattr(obj.exif_management_obj, "get_model", _get_model_spy)
    # Se stubbea run_batch: aquí se prueba la resolución de get_model, que ocurre
    # ANTES de construir el lote de process_one_image (ya cubierto en otro test).
    monkeypatch.setattr(
        utils_mod, "run_batch",
        lambda *a, **k: {"results": [], "errors": []},
    )

    obj.crop_centered_images(
        "gs://bucket-model-detection/RGB/PB1_V1",
        _noop_progress(), _noop_progress(),
        {"FC6310": 50},
        percentage_cropping_auto=True,
    )

    assert llamadas, "get_model no se llegó a invocar: crop_centered_images no encontró la imagen en gs://"
    assert len(llamadas) == 1
    ruta_pasada = llamadas[0]
    assert not str(ruta_pasada).startswith("gs://"), (
        f"get_model recibió la URI gs:// cruda ({ruta_pasada!r}) en vez de una ruta local resuelta"
    )
    assert rutas_existian[0], f"la ruta local pasada a get_model no existe en disco: {ruta_pasada!r}"
