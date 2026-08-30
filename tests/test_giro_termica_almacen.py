"""Migración `gs://…` del giro IN-PLACE de la térmica DJI
(`SplitImages.rotate_thermal_jpgs_in_place` / `_rotate_one_thermal_jpg_in_place`).

⚠️ El `*_T.JPG` es un R-JPEG con payload radiométrico propietario
IRRECUPERABLE si se pierde: no hay copia de respaldo. Por eso el invariante
duro de esta migración no es "gira bien", es "si algo falla a medio camino,
el objeto original NUNCA se toca ni se publica" — en local (byte a byte,
como siempre) y en `gs://…` (el `with almacen.editar_en_sitio(...)` solo
republica si el bloque termina SIN excepción).

Mismo estilo de doble en memoria del backend GCS que
`tests/test_post_dji_tif_almacen.py`. NO se modifica `BucketFalso.list_blobs`
(su `startswith` literal replica a propósito el comportamiento de GCS real,
que filtra por prefijo de TEXTO, no por frontera de carpeta). `_NotFound`
imita por NOMBRE a `google.api_core.exceptions.NotFound`: el SDK real nunca
lanza `FileNotFoundError` al descargar un blob inexistente.
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


def _split(logger):
    obj = pipeline.SplitImages(logger)
    obj.reset_variables()
    return obj


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Doble en memoria del backend GCS (idéntico a test_post_dji_tif_almacen.py) ---

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
    """Misma fixture de entrada (una térmica DJI), procesada por disco y por
    `gs://…`: el resultado debe ser sha256 idéntico fichero a fichero."""
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path)

    # -- local --
    raiz_local = tmp_path / "local" / "TERMICA" / "PB1" / "PB1_V1"
    raiz_local.mkdir(parents=True)
    (raiz_local / "DJI_0001_T.JPG").write_bytes(contenido)
    obj_local = _split(logger)
    giradas_local = obj_local.rotate_thermal_jpgs_in_place(
        str(tmp_path / "local" / "TERMICA"), _noop_progress(), _noop_progress(), rotate_90=True)
    assert giradas_local == 1
    resultado_local = (raiz_local / "DJI_0001_T.JPG").read_bytes()

    # -- gs:// --
    bucket = _sembrar_almacen_gcs("bucket-paridad-giro")
    bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = contenido
    obj_gcs = _split(logger)
    giradas_gcs = obj_gcs.rotate_thermal_jpgs_in_place(
        "gs://bucket-paridad-giro/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)
    assert giradas_gcs == 1
    resultado_gcs = bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"]

    assert _sha256(resultado_local) == _sha256(resultado_gcs)
    assert resultado_local != contenido, "el giro debía cambiar el contenido"


# --- 2) Idempotencia en gs://: segunda pasada no vuelve a girar ---------------

def test_idempotencia_en_gcs_segunda_pasada_no_gira(logger, make_dji_jpeg, tmp_path):
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path)
    bucket = _sembrar_almacen_gcs("bucket-idempotencia-giro")
    bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = contenido

    obj = _split(logger)
    primera = obj.rotate_thermal_jpgs_in_place(
        "gs://bucket-idempotencia-giro/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)
    assert primera == 1
    tras_primera = bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"]

    segunda = obj.rotate_thermal_jpgs_in_place(
        "gs://bucket-idempotencia-giro/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)
    assert segunda == 0, "la segunda pasada no debe volver a girar (guard height>width)"
    assert bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] == tras_primera


def test_idempotencia_en_gcs_segunda_pasada_no_sube_nada(logger, make_dji_jpeg, tmp_path):
    """La `"ya_girada"` no debe republicar el objeto: el `editar_en_sitio` de
    `_rotate_one_thermal_jpg_in_place` pasa `publicar_solo_si_cambia=True` para
    no resubir decenas de GB de térmicas sin cambios en cada reejecución."""
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path)
    bucket = _sembrar_almacen_gcs("bucket-idempotencia-sin-upload")
    bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = contenido

    obj = _split(logger)
    primera = obj.rotate_thermal_jpgs_in_place(
        "gs://bucket-idempotencia-sin-upload/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)
    assert primera == 1
    tras_primera = bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"]
    uploads_tras_primera = bucket.uploads

    segunda = obj.rotate_thermal_jpgs_in_place(
        "gs://bucket-idempotencia-sin-upload/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)
    assert segunda == 0
    assert bucket.uploads == uploads_tras_primera, (
        "la segunda pasada (ya_girada) no debe subir nada al bucket")
    assert bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] == tras_primera


# --- 3) El origen sobrevive intacto si el giro falla a medio camino ----------

def _forzar_fallo_en_save(monkeypatch):
    """Sustituye `PIL.Image.Image.save` para que reviente SIEMPRE: simula el
    guardado del `.rot.tmp` cortándose a medias."""
    def _save_que_falla(self, fp, *a, **k):
        raise RuntimeError("boom: guardado simulado roto")

    monkeypatch.setattr(Image.Image, "save", _save_que_falla)


def test_origen_sobrevive_intacto_si_falla_el_giro_local(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    raiz = tmp_path / "TERMICA" / "PB1" / "PB1_V1"
    raiz.mkdir(parents=True)
    make_dji_jpeg(str(raiz / "DJI_0001_T.JPG"))
    antes = (raiz / "DJI_0001_T.JPG").read_bytes()

    _forzar_fallo_en_save(monkeypatch)
    obj = _split(logger)
    giradas = obj.rotate_thermal_jpgs_in_place(
        str(tmp_path / "TERMICA"), _noop_progress(), _noop_progress(), rotate_90=True)

    assert giradas == 0
    assert (raiz / "DJI_0001_T.JPG").read_bytes() == antes, (
        "un fallo a mitad de guardado no puede dejar el original tocado")
    assert not list(raiz.glob("*.tmp")), "no puede quedar temporal huérfano"


def test_origen_sobrevive_intacto_y_nada_se_publica_si_falla_el_giro_en_gcs(
    logger, make_dji_jpeg, tmp_path, monkeypatch
):
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path)
    bucket = _sembrar_almacen_gcs("bucket-fallo-giro")
    bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = contenido
    claves_antes = dict(bucket.objetos)

    _forzar_fallo_en_save(monkeypatch)
    obj = _split(logger)
    giradas = obj.rotate_thermal_jpgs_in_place(
        "gs://bucket-fallo-giro/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)

    assert giradas == 0
    assert bucket.objetos == claves_antes, (
        "NADA se publica en gs:// si el bloque `editar_en_sitio` termina con excepción")


# --- 4) El recorrido encuentra las mismas imágenes por disco y por gs:// -----

def test_recorrido_encuentra_las_mismas_imagenes_local_y_gcs(
    tmp_path, logger, make_dji_jpeg
):
    """Estructura con subcarpetas anidadas (dos vuelos bajo dos PBs): el
    listado de imágenes giradas debe coincidir entre disco y gs://."""
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path)
    relativos = [
        "PB1/PB1_V1/DJI_0001_T.JPG",
        "PB1/PB1_V2/DJI_0001_T.JPG",
        "PB2/PB2_V1/DJI_0001_T.JPG",
    ]

    raiz_local = tmp_path / "local_termica"
    for rel in relativos:
        destino = raiz_local / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
    obj_local = _split(logger)
    giradas_local = obj_local.rotate_thermal_jpgs_in_place(
        str(raiz_local), _noop_progress(), _noop_progress(), rotate_90=True)

    bucket = _sembrar_almacen_gcs("bucket-recorrido-giro")
    for rel in relativos:
        bucket.objetos[f"TERMICA/{rel}"] = contenido
    obj_gcs = _split(logger)
    giradas_gcs = obj_gcs.rotate_thermal_jpgs_in_place(
        "gs://bucket-recorrido-giro/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)

    assert giradas_local == len(relativos)
    assert giradas_gcs == len(relativos)
    for rel in relativos:
        girada_local = (raiz_local / rel).read_bytes()
        girada_gcs = bucket.objetos[f"TERMICA/{rel}"]
        assert _sha256(girada_local) == _sha256(girada_gcs), rel


# --- 5) Fallo en una imagen no impide procesar las demás (gs://) -------------

def test_fallo_en_una_imagen_no_impide_procesar_las_demas_en_gcs(
    logger, make_dji_jpeg, tmp_path
):
    contenido = _jpg_bytes(make_dji_jpeg, tmp_path)
    bucket = _sembrar_almacen_gcs("bucket-fallo-parcial-giro")
    bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = contenido
    bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0002_T.JPG"] = b"esto no es un JPEG"

    obj = _split(logger)
    giradas = obj.rotate_thermal_jpgs_in_place(
        "gs://bucket-fallo-parcial-giro/TERMICA", _noop_progress(), _noop_progress(), rotate_90=True)

    assert giradas == 1, "la buena sí se gira aunque la corrupta falle"
    assert bucket.objetos["TERMICA/PB1/PB1_V1/DJI_0002_T.JPG"] == b"esto no es un JPEG", (
        "la corrupta no puede quedar tocada ni truncada")


def test_read_auto_rotate_degree_lee_el_criterio_desde_gcs(logger):
    """El criterio de giro (`CSVs/_criterio/<vuelo>_Videofiles.csv`) se lee con
    `pd.read_csv`, y pandas no lleva fsspec/gcsfs: con la planta en `gs://` eso
    reventaba con `ImportError: Import fsspec failed` en CADA térmica y tumbaba
    la etapa post (operación 19 en producción, conversión DJI->TIFF)."""
    import utils

    bucket = _sembrar_almacen_gcs("bucket-criterio-giro")
    bucket.objetos[f"CSVs/{utils.CRITERIO_DIRNAME}/PB1_V1_Videofiles.csv"] = (
        b"New Name,Original Name,Degree\na,b,270\n")

    grados = _split(logger).read_auto_rotate_degree(
        "gs://bucket-criterio-giro/TERMICA/PB1/PB1_V1", _noop_progress())

    assert grados == 270


def test_read_auto_rotate_degree_sin_criterio_en_gcs_no_revienta(logger):
    """Sin CSV de criterio no se rota (0) y el vuelo continúa: el
    `FileNotFoundError` que ahora llega de `abrir_para_lectura` tiene que seguir
    cayendo en el `except` de siempre, no propagarse."""
    _sembrar_almacen_gcs("bucket-sin-criterio")

    grados = _split(logger).read_auto_rotate_degree(
        "gs://bucket-sin-criterio/TERMICA/PB1/PB1_V1", _noop_progress())

    assert grados == 0
