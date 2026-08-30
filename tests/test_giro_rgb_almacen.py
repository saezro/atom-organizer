"""
Migración `gs://…` del giro IN-PLACE de la RGB (`CompressImage.rotate_and_save`,
`pipeline.py:737-826`).

HOY esta función está rota para `gs://…`: construye `ruta_original`/`ruta_crop`
con `os.path.join(input_folder, image_name)` y las abre con `PIL.Image.open` /
`os.path.exists` / `pyexiv2.Image` directamente, sin pasar por
`atom_core.almacen`. Cuando `input_folder` es un prefijo `gs://bucket/…`,
`os.path.exists` siempre da `False` y `Image.open` revienta con
`FileNotFoundError` (PIL no entiende el esquema `gs://`) — la función lo
atrapa en su propio `except FileNotFoundError` y devuelve `False` sin haber
tocado nada, real ni falso.

Este fichero prueba PARIDAD disco/`gs://…` para el estado post-fix, y por
tanto hoy FALLA por esa razón: la rama `gs://…` nunca llega a rotar nada.

Mismo estilo de doble en memoria del backend GCS que
`tests/test_giro_termica_almacen.py` (plantilla de esta migración) y
`tests/test_post_dji_tif_almacen.py`. NO se modifica `BucketFalso.list_blobs`.
`_NotFound` imita por NOMBRE a `google.api_core.exceptions.NotFound`.
"""
import hashlib
import types

import pytest
from PIL import Image

import atom_core.almacen as almacen_mod
import pipeline


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def _compress(logger):
    obj = pipeline.CompressImage(logger)
    obj.reset_variables()
    return obj


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _es_vertical(data: bytes) -> bool:
    import io
    with Image.open(io.BytesIO(data)) as im:
        return im.height > im.width


# --- Doble en memoria del backend GCS (idéntico a test_giro_termica_almacen.py) ---

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


# --- Fixtures de imágenes -----------------------------------------------------

def _jpg_bytes(make_dji_jpeg, tmp_path, nombre="fuente.JPG"):
    ruta = tmp_path / nombre
    make_dji_jpeg(str(ruta))
    return ruta.read_bytes()


# --- 1) Paridad byte a byte: mismo giro sobre disco y sobre gs:// -------------

def test_paridad_local_y_gcs_mismo_contenido(tmp_path, logger, make_dji_jpeg):
    """Misma fixture RGB apaisada (+ su `_CROP`), montada en disco y en
    `gs://…`: el resultado tras `rotate_and_save` debe ser sha256 idéntico
    fichero a fichero, tanto el original girado como el `_CROP` girado."""
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path, "original.JPG")
    contenido_crop = _jpg_bytes(make_dji_jpeg, tmp_path, "crop.JPG")

    # -- local --
    raiz_local = tmp_path / "local" / "RGB" / "PB1_V1"
    raiz_local.mkdir(parents=True)
    (raiz_local / "DJI_0001.JPG").write_bytes(contenido)
    (raiz_local / "DJI_0001_CROP.JPG").write_bytes(contenido_crop)
    ci_local = _compress(logger)
    tenia_crop_local = ci_local.rotate_and_save(
        "DJI_0001.JPG", str(raiz_local), Image.ROTATE_90, 85, _noop_progress())
    resultado_local = (raiz_local / "DJI_0001.JPG").read_bytes()
    resultado_local_crop = (raiz_local / "DJI_0001_CROP.JPG").read_bytes()

    # -- gs:// --
    bucket = _sembrar_almacen_gcs("bucket-paridad-rgb")
    bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"] = contenido
    bucket.objetos["RGB/PB1_V1/DJI_0001_CROP.JPG"] = contenido_crop
    ci_gcs = _compress(logger)
    tenia_crop_gcs = ci_gcs.rotate_and_save(
        "DJI_0001.JPG", "gs://bucket-paridad-rgb/RGB/PB1_V1", Image.ROTATE_90, 85, _noop_progress())
    resultado_gcs = bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"]
    resultado_gcs_crop = bucket.objetos["RGB/PB1_V1/DJI_0001_CROP.JPG"]

    assert tenia_crop_gcs == tenia_crop_local, (
        "rotate_and_save() en gs:// no devuelve lo mismo que en local "
        f"(local={tenia_crop_local!r}, gcs={tenia_crop_gcs!r}): hoy la rama gs:// "
        "no resuelve la ruta y cae en el except FileNotFoundError sin girar nada."
    )
    assert resultado_local != contenido, "el giro debía cambiar el original en local"
    assert resultado_local_crop != contenido_crop, "el giro debía cambiar el _CROP en local"
    assert _sha256(resultado_local) == _sha256(resultado_gcs), "original: local vs gs:// difieren"
    assert _sha256(resultado_local_crop) == _sha256(resultado_gcs_crop), "_CROP: local vs gs:// difieren"


# --- 2) Idempotencia: segunda pasada no vuelve a girar, en ambos backends ----

def test_idempotencia_local_segunda_pasada_no_gira(tmp_path, logger, make_dji_jpeg):
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path, "original.JPG")
    raiz = tmp_path / "RGB" / "PB1_V1"
    raiz.mkdir(parents=True)
    (raiz / "DJI_0001.JPG").write_bytes(contenido)

    ci = _compress(logger)
    primera = ci.rotate_and_save("DJI_0001.JPG", str(raiz), Image.ROTATE_90, 85, _noop_progress())
    tras_primera = (raiz / "DJI_0001.JPG").read_bytes()
    assert _es_vertical(tras_primera), "tras la primera pasada debía quedar vertical"

    segunda = ci.rotate_and_save("DJI_0001.JPG", str(raiz), Image.ROTATE_90, 85, _noop_progress())
    tras_segunda = (raiz / "DJI_0001.JPG").read_bytes()

    assert primera is False, "sin _CROP, rotate_and_save devuelve False"
    assert segunda is False
    assert _sha256(tras_segunda) == _sha256(tras_primera), (
        "el guard _ya_girada debía impedir un segundo giro sobre una imagen ya vertical")


def test_idempotencia_en_gcs_segunda_pasada_no_gira(tmp_path, logger, make_dji_jpeg):
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path, "original.JPG")
    bucket = _sembrar_almacen_gcs("bucket-idempotencia-rgb")
    bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"] = contenido

    ci = _compress(logger)
    primera = ci.rotate_and_save(
        "DJI_0001.JPG", "gs://bucket-idempotencia-rgb/RGB/PB1_V1", Image.ROTATE_90, 85, _noop_progress())
    tras_primera = bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"]
    assert _es_vertical(tras_primera), (
        "tras la primera pasada en gs:// debía quedar vertical (girada); hoy la ruta "
        "gs:// no se resuelve y el objeto del bucket falso queda intacto/apaisado."
    )

    segunda = ci.rotate_and_save(
        "DJI_0001.JPG", "gs://bucket-idempotencia-rgb/RGB/PB1_V1", Image.ROTATE_90, 85, _noop_progress())
    tras_segunda = bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"]

    assert primera is False
    assert segunda is False
    assert _sha256(tras_segunda) == _sha256(tras_primera)


# --- 3) El origen sobrevive intacto si el giro falla a medio camino ----------

def _forzar_fallo_en_save(monkeypatch):
    """Sustituye `PIL.Image.Image.save` para que reviente SIEMPRE: simula el
    guardado de la imagen girada cortándose a medias."""
    def _save_que_falla(self, fp, *a, **k):
        raise RuntimeError("boom: guardado simulado roto")

    monkeypatch.setattr(Image.Image, "save", _save_que_falla)


def test_origen_sobrevive_intacto_si_falla_el_giro_local(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    raiz = tmp_path / "RGB" / "PB1_V1"
    raiz.mkdir(parents=True)
    make_dji_jpeg(str(raiz / "DJI_0001.JPG"))
    antes = (raiz / "DJI_0001.JPG").read_bytes()

    _forzar_fallo_en_save(monkeypatch)
    ci = _compress(logger)
    resultado = ci.rotate_and_save(
        "DJI_0001.JPG", str(raiz), Image.ROTATE_90, 85, _noop_progress())

    assert resultado is False
    assert (raiz / "DJI_0001.JPG").read_bytes() == antes, (
        "un fallo a mitad de guardado no puede dejar el original tocado (se escribe "
        "in-place, sin fichero temporal intermedio)")


def test_origen_sobrevive_intacto_y_nada_se_publica_si_falla_el_giro_en_gcs(
    logger, make_dji_jpeg, tmp_path, monkeypatch
):
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path, "original.JPG")
    bucket = _sembrar_almacen_gcs("bucket-fallo-giro-rgb")
    bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"] = contenido
    claves_antes = dict(bucket.objetos)

    _forzar_fallo_en_save(monkeypatch)
    ci = _compress(logger)
    resultado = ci.rotate_and_save(
        "DJI_0001.JPG", "gs://bucket-fallo-giro-rgb/RGB/PB1_V1", Image.ROTATE_90, 85, _noop_progress())

    assert resultado is False
    assert bucket.objetos == claves_antes, (
        "NADA se publica en gs:// si el giro falla a medio camino")


# --- 4) XMP/EXIF preservado tras el giro, y coincide entre backends ----------

def test_xmp_gimbal_preservado_tras_girar_y_coincide_entre_backends(
    tmp_path, logger, make_dji_jpeg
):
    """Tras el giro, el GimbalYawDegree del XMP DJI sigue presente y con el
    mismo valor que antes de girar; y el resultado de `gs://…` debe tener los
    mismos metadatos y la misma orientación que el de local (hoy la rama
    gs:// no gira nada, así que la orientación no puede coincidir)."""
    ruta_fuente = tmp_path / "fuente.JPG"
    make_dji_jpeg(str(ruta_fuente), gimbal_yaw=37.5)
    contenido = ruta_fuente.read_bytes()

    import exif as exif_management
    exif_obj = exif_management.GeneralInformationFromImage(logger)
    yaw_antes, _pitch_antes = exif_obj.get_gimbal_yaw_pitch(str(ruta_fuente))

    # -- local --
    raiz_local = tmp_path / "local" / "RGB" / "PB1_V1"
    raiz_local.mkdir(parents=True)
    (raiz_local / "DJI_0001.JPG").write_bytes(contenido)
    ci_local = _compress(logger)
    ci_local.rotate_and_save("DJI_0001.JPG", str(raiz_local), Image.ROTATE_90, 85, _noop_progress())
    ruta_resultado_local = raiz_local / "DJI_0001.JPG"
    yaw_local, _ = exif_obj.get_gimbal_yaw_pitch(str(ruta_resultado_local))
    assert yaw_local == yaw_antes, "el GimbalYawDegree no debe cambiar al girar (local)"
    assert _es_vertical(ruta_resultado_local.read_bytes()), "el original local debía quedar vertical"

    # -- gs:// --
    bucket = _sembrar_almacen_gcs("bucket-xmp-rgb")
    bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"] = contenido
    ci_gcs = _compress(logger)
    ci_gcs.rotate_and_save(
        "DJI_0001.JPG", "gs://bucket-xmp-rgb/RGB/PB1_V1", Image.ROTATE_90, 85, _noop_progress())
    resultado_gcs = bucket.objetos["RGB/PB1_V1/DJI_0001.JPG"]

    # Materializamos el resultado de gs:// a un fichero temporal (bajo tmp_path)
    # para poder leer su XMP con las mismas funciones que en local.
    ruta_resultado_gcs = tmp_path / "resultado_gcs.JPG"
    ruta_resultado_gcs.write_bytes(resultado_gcs)
    yaw_gcs, _ = exif_obj.get_gimbal_yaw_pitch(str(ruta_resultado_gcs))

    assert yaw_gcs == yaw_antes, "el GimbalYawDegree debía conservarse también en gs://"
    assert _es_vertical(resultado_gcs), (
        "el resultado de gs:// debía quedar vertical igual que el local; hoy la ruta "
        "gs:// no se resuelve y el objeto del bucket falso queda apaisado sin girar."
    )
    assert _sha256(resultado_gcs) == _sha256(ruta_resultado_local.read_bytes()), (
        "local y gs:// deben producir el mismo fichero girado")
