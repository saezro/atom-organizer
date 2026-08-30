"""Migración a URI-aware (`atom_core.almacen`) de `MetaLocation` (exif.py):
`_walk_varias`, `checking_results_meta_location`, `gen_meta_location` (escritura
del csv), `iterate_folders`/`check_input_folder_and_iterate` y
`leerLatitudLongitudAltitud_exif_DJI`.

Mismo estilo de doble en memoria del backend GCS que
`tests/test_struct_primitivas_almacen.py`: se siembra la caché de
`abrir_almacen` con un `AlmacenGCS` de prueba, sin SDK real y sin red.

⚠️ `BucketFalso.list_blobs` usa `startswith` LITERAL a propósito, replicando el
comportamiento real de GCS (ver test de regresión PB1/PB10 en el fichero de
referencia) -- no tocar esa parte del doble.
"""
import os
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
import exif as exif_mod
from atom_core.almacen_gcs import AlmacenGCS
from utils import OrganizerLogger as ol


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


def _make_meta_location(tmp_path, nombre="ml"):
    logger = ol(f"test_post_exif_almacen_{nombre}", log_dir=str(tmp_path / f"Logs_{nombre}"), create_file_handler=False)
    return exif_mod.MetaLocation(logger)


# --- Doble en memoria del backend GCS (copiado de test_struct_primitivas_almacen.py) ---

_NotFound = type("NotFound", (Exception,), {})
"""Doble mínimo de `google.api_core.exceptions.NotFound`: mismo nombre de
clase (`AlmacenGCS.abrir_local` lo reconoce por nombre cuando el SDK real no
está instalado, que es el caso de este entorno de tests). El SDK real NUNCA
lanza `FileNotFoundError` al descargar un blob inexistente."""


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
        self.bucket.descargas += 1
        if self.name not in self.bucket.objetos:
            raise _NotFound(self.name)
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
        self.descargas = 0

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


# --- 1) `_walk_varias`: misma estructura y poda en local y gs:// -----------

def _normalizar_walk(resultados, raiz: str):
    """dict `ruta_relativa -> (dirnames ordenados, filenames ordenados)`, para
    comparar la salida de `_walk_varias` entre backends sin depender de la
    raíz concreta de cada uno."""
    out = {}
    raiz_norm = str(raiz).rstrip("/")
    for dirpath, dirnames, filenames in resultados:
        if exif_mod.es_uri_gcs(raiz):
            rel = dirpath[len(raiz_norm):].strip("/")
        else:
            rel = os.path.relpath(dirpath, raiz_norm)
            rel = "" if rel == "." else rel.replace(os.sep, "/")
        out[rel] = (sorted(dirnames), sorted(filenames))
    return out


def test_walk_varias_local_y_gcs_dan_igual_estructura_y_poda(tmp_path, make_dji_jpeg):
    raiz_local = tmp_path / "arbol_walk"
    (raiz_local / "RGB" / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz_local / "CSVs" / "sub").mkdir(parents=True)
    (raiz_local / "ESTADILLOS").mkdir(parents=True)
    make_dji_jpeg(str(raiz_local / "RGB" / "PB1" / "PB1_V1" / "DJI_0001_W.JPG"))
    (raiz_local / "RGB" / "PB1" / "PB1_V1" / "vuelo.csv").write_text("a,b\n")
    (raiz_local / "CSVs" / "sub" / "nope.csv").write_text("x\n")

    bucket = _sembrar_almacen_gcs("bucket-walk")
    for rel in ("RGB/PB1/PB1_V1/DJI_0001_W.JPG", "RGB/PB1/PB1_V1/vuelo.csv", "CSVs/sub/nope.csv"):
        bucket.objetos[f"dest/{rel}"] = (raiz_local / rel).read_bytes()

    excluded_folders = {"CSVs", "ESTADILLOS", "MINIATURAS"}

    resultados_local = list(exif_mod.MetaLocation._walk_varias([str(raiz_local)], excluded_folders))
    resultados_gcs = list(exif_mod.MetaLocation._walk_varias(["gs://bucket-walk/dest"], excluded_folders))

    normal_local = _normalizar_walk(resultados_local, str(raiz_local))
    normal_gcs = _normalizar_walk(resultados_gcs, "gs://bucket-walk/dest")

    assert normal_local == normal_gcs
    assert normal_local == {
        "": (["RGB"], []),
        "RGB": (["PB1"], []),
        "RGB/PB1": (["PB1_V1"], []),
        "RGB/PB1/PB1_V1": ([], ["DJI_0001_W.JPG", "vuelo.csv"]),
    }
    # Poda confirmada: nunca se desciende a las carpetas excluidas.
    assert "CSVs" not in normal_local and "CSVs/sub" not in normal_local
    assert "ESTADILLOS" not in normal_local


# --- 2) `gen_meta_location`: mismo csv (destino + contenido) en ambos backends --

def test_gen_meta_location_csv_mismo_destino_y_contenido_local_y_gcs(tmp_path, make_dji_jpeg):
    # --- local: comportamiento de siempre ---
    carpeta_local = tmp_path / "PB1_V01"
    carpeta_local.mkdir()
    imagen_local = carpeta_local / "DJI_0001_W.JPG"
    make_dji_jpeg(str(imagen_local))
    csv_folder_local = tmp_path / "csv_dest_local"
    csv_folder_local.mkdir()

    ml_local = _make_meta_location(tmp_path, "local")
    ml_local.total_images_number = 1
    ml_local.gen_meta_location(str(carpeta_local), "meta.csv", FakeSignal(), FakeSignal(), str(csv_folder_local), 50.0, False)

    nombre_csv = "PB1_V01_meta.csv"
    csv_en_carpeta_local = carpeta_local / nombre_csv
    csv_en_destino_local = csv_folder_local / nombre_csv
    assert csv_en_carpeta_local.exists()
    assert csv_en_destino_local.exists()
    contenido_local = csv_en_carpeta_local.read_bytes()
    assert csv_en_destino_local.read_bytes() == contenido_local

    # --- gs://: mismo árbol de imagen, servido desde un bucket falso ---
    bucket = _sembrar_almacen_gcs("bucket-meta-csv")
    bucket.objetos["dest/PB1_V01/DJI_0001_W.JPG"] = imagen_local.read_bytes()

    ml_gcs = _make_meta_location(tmp_path, "gcs")
    ml_gcs.total_images_number = 1
    ml_gcs.gen_meta_location(
        "gs://bucket-meta-csv/dest/PB1_V01", "meta.csv", FakeSignal(), FakeSignal(),
        "gs://bucket-meta-csv/csv_dest", 50.0, False,
    )

    clave_en_carpeta = "dest/PB1_V01/PB1_V01_meta.csv"
    clave_en_destino = "csv_dest/PB1_V01_meta.csv"
    assert clave_en_carpeta in bucket.objetos
    assert clave_en_destino in bucket.objetos
    assert bucket.objetos[clave_en_carpeta] == contenido_local
    assert bucket.objetos[clave_en_destino] == contenido_local


# --- 3) `leerLatitudLongitudAltitud_exif_DJI`: UNA sola descarga por imagen en gs:// --

def test_leer_exif_gcs_hace_una_sola_descarga_por_imagen(tmp_path, make_dji_jpeg):
    imagen_local = tmp_path / "DJI_0001_W.JPG"
    make_dji_jpeg(str(imagen_local), lat=40.0, lon=-3.0)

    ml = _make_meta_location(tmp_path, "descargas")
    resultado_local = ml.leerLatitudLongitudAltitud_exif_DJI(str(imagen_local), FakeSignal())
    assert resultado_local is not None

    bucket = _sembrar_almacen_gcs("bucket-una-descarga")
    bucket.objetos["dest/DJI_0001_W.JPG"] = imagen_local.read_bytes()
    assert bucket.descargas == 0

    resultado_gcs = ml.leerLatitudLongitudAltitud_exif_DJI("gs://bucket-una-descarga/dest/DJI_0001_W.JPG", FakeSignal())

    assert bucket.descargas == 1
    assert resultado_gcs is not None
    # Nombre, lat, lon, alt -- mismo resultado que en local (misma imagen).
    assert resultado_gcs[0] == "DJI_0001_W.JPG"
    assert resultado_gcs[1:] == resultado_local[1:]
