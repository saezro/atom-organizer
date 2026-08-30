"""URI-aware de la etapa `post` en `pipeline.py`: listado/existencia/creación de
carpeta/copia de CSV, para que local y `gs://…` den EXACTAMENTE el mismo
resultado (mismo orden de recorrido, mismo contenido).

Cubre:
- `RGBCropping.checking_results_rgb_cropping`: listado de carpetas PB* y de
  vuelos dentro de cada una (`almacen.listar_subcarpetas`), en el mismo orden.
- `GenStructFolder.copy_flight_csvs`: copia de `meta.csv`/`location.csv` a
  `CSVs/` (`almacen.listar_ficheros` + `abrir_para_lectura`/`publicar_en`),
  mismo nombre y contenido.
- `SplitImages.convert_dji_image_to_tif`: skip por TIFF ya existente
  (`almacen.existe_ruta` + `almacen.tamano_de`), sin invocar el conversor.

Mismo estilo de doble en memoria del backend GCS que
`tests/test_struct_primitivas_almacen.py`. NO se modifica `BucketFalso.list_blobs`
(su `startswith` literal replica a propósito el comportamiento de GCS real).
"""
import os
import types

import pytest

import atom_core.almacen as almacen_mod
import pipeline


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


# --- Doble en memoria del backend GCS (idéntico a test_struct_primitivas_almacen.py) ---

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
        from pathlib import Path
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
    from atom_core.almacen_gcs import AlmacenGCS

    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


def _relativa(clave: str, raiz: str) -> str:
    """Quita el prefijo `raiz` (local o `gs://…`) de `clave` y normaliza a `/`."""
    resto = clave[len(raiz):] if clave.startswith(raiz) else clave
    return resto.replace("\\", "/").strip("/")


# --- RGBCropping.checking_results_rgb_cropping: listado de PBs/vuelos ------

def _sembrar_arbol_rgb_cropping(tmp_path, bucket=None, prefijo=""):
    """RGB/PB1/PB1_V1 con 1 imagen sin CROP + 1 con CROP (match), RGB/PB2/PB2_V1
    con solo 1 imagen sin CROP (mismatch), y una carpeta `otros` que NO empieza
    por 'PB' (debe quedar excluida del listado)."""
    raiz = tmp_path / "RGB"
    (raiz / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "PB2" / "PB2_V1").mkdir(parents=True)
    (raiz / "otros").mkdir(parents=True)

    ficheros = {
        "PB1/PB1_V1/DJI_0001_W.JPG": b"a",
        "PB1/PB1_V1/DJI_0001_W_CROP.JPG": b"b",
        "PB2/PB2_V1/DJI_0002_W.JPG": b"c",
        "otros/DJI_9999_W.JPG": b"d",
    }
    for rel, contenido in ficheros.items():
        (raiz / rel).write_bytes(contenido)

    if bucket is not None:
        for rel in ficheros:
            clave = f"{prefijo}/RGB/{rel}".strip("/")
            bucket.objetos[clave] = (raiz / rel).read_bytes()

    return raiz


def test_checking_rgb_cropping_listado_pbs_y_vuelos_local_y_gcs_igual_y_mismo_orden(tmp_path, logger):
    bucket = _sembrar_almacen_gcs("bucket-cropping-listado")
    raiz_local = _sembrar_arbol_rgb_cropping(tmp_path, bucket=bucket, prefijo="dest")

    progress = _noop_progress()

    obj_local = pipeline.RGBCropping(logger)
    resultados_local = obj_local.checking_results_rgb_cropping(str(raiz_local), progress, progress)

    obj_gcs = pipeline.RGBCropping(logger)
    resultados_gcs = obj_gcs.checking_results_rgb_cropping("gs://bucket-cropping-listado/dest/RGB", progress, progress)

    claves_local = [_relativa(k, str(raiz_local)) for k in resultados_local.keys()]
    claves_gcs = [_relativa(k, "gs://bucket-cropping-listado/dest/RGB") for k in resultados_gcs.keys()]

    # Mismo orden de recorrido (PB1/PB1_V1 antes que PB2/PB2_V1, "otros" fuera).
    assert claves_local == claves_gcs == ["PB1/PB1_V1", "PB2/PB2_V1"]

    # Mismo resultado de match/crop/non_crop para cada vuelo.
    valores_local = list(resultados_local.values())
    valores_gcs = list(resultados_gcs.values())
    assert valores_local == valores_gcs == [
        {"crop": 1, "non_crop": 1, "match": True},
        {"crop": 0, "non_crop": 1, "match": False},
    ]
    assert obj_local.error_rgb_cropping == obj_gcs.error_rgb_cropping == 1


# --- GenStructFolder.copy_flight_csvs: copia de meta/location a CSVs/ ------

def test_copy_flight_csvs_local_y_gcs_mismo_nombre_y_contenido(tmp_path):
    from utils import OrganizerLogger

    logger_local = OrganizerLogger(name="test_copy_csv_local", log_dir=str(tmp_path / "Logs1"), create_file_handler=False)
    logger_gcs = OrganizerLogger(name="test_copy_csv_gcs", log_dir=str(tmp_path / "Logs2"), create_file_handler=False)

    contenido_meta = b"New Name,Original Name,Degree\nPB1_V1_0001.JPG,DJI_0001_T.JPG,90\n"
    contenido_location = b"lat,lon\n40.1,-3.7\n"

    # --- Local ---
    raiz_local = tmp_path / "local_root"
    (raiz_local / "TERMICA" / "PB1_V1").mkdir(parents=True)
    (raiz_local / "RGB" / "PB1_V1").mkdir(parents=True)
    (raiz_local / "TERMICA" / "PB1_V1" / "PB1_V1_meta.csv").write_bytes(contenido_meta)
    (raiz_local / "RGB" / "PB1_V1" / "PB1_V1_location.csv").write_bytes(contenido_location)

    obj_local = pipeline.GenStructFolder(logger_local)
    obj_local.root_folder = str(raiz_local)
    obj_local.csvs_root_folder = almacen_mod.unir(str(raiz_local), "CSVs")
    obj_local.copy_flight_csvs(str(raiz_local / "TERMICA" / "PB1_V1"), _noop_progress())

    ruta_meta_local = raiz_local / "CSVs" / "PB1_V1_meta.csv"
    ruta_location_local = raiz_local / "CSVs" / "PB1_V1_location.csv"
    assert ruta_meta_local.read_bytes() == contenido_meta
    assert ruta_location_local.read_bytes() == contenido_location

    # --- gs:// ---
    bucket = _sembrar_almacen_gcs("bucket-copy-csv")
    bucket.objetos["dest/TERMICA/PB1_V1/PB1_V1_meta.csv"] = contenido_meta
    bucket.objetos["dest/RGB/PB1_V1/PB1_V1_location.csv"] = contenido_location

    obj_gcs = pipeline.GenStructFolder(logger_gcs)
    obj_gcs.root_folder = "gs://bucket-copy-csv/dest"
    obj_gcs.csvs_root_folder = almacen_mod.unir(obj_gcs.root_folder, "CSVs")
    obj_gcs.copy_flight_csvs("gs://bucket-copy-csv/dest/TERMICA/PB1_V1", _noop_progress())

    assert bucket.objetos["dest/CSVs/PB1_V1_meta.csv"] == contenido_meta
    assert bucket.objetos["dest/CSVs/PB1_V1_location.csv"] == contenido_location

    # Mismo resultado en ambos: ni errores registrados (los CSVs sí existían).
    assert obj_local.error_gen_struct_folder == obj_gcs.error_gen_struct_folder == 0


def test_copy_flight_csvs_avisa_igual_si_falta_el_location(tmp_path, logger):
    """El meta existe pero el location no: en ambos backends debe registrarse
    el mismo aviso (`error_gen_struct_folder` += 1) y la copia del meta debe
    seguir haciéndose (no aborta por el location que falta)."""
    contenido_meta = b"New Name,Original Name,Degree\n"

    raiz_local = tmp_path / "root"
    (raiz_local / "TERMICA" / "PB1_V1").mkdir(parents=True)
    (raiz_local / "RGB" / "PB1_V1").mkdir(parents=True)
    (raiz_local / "TERMICA" / "PB1_V1" / "PB1_V1_meta.csv").write_bytes(contenido_meta)

    obj_local = pipeline.GenStructFolder(logger)
    obj_local.root_folder = str(raiz_local)
    obj_local.csvs_root_folder = almacen_mod.unir(str(raiz_local), "CSVs")
    obj_local.copy_flight_csvs(str(raiz_local / "TERMICA" / "PB1_V1"), _noop_progress())

    assert (raiz_local / "CSVs" / "PB1_V1_meta.csv").read_bytes() == contenido_meta
    assert not (raiz_local / "CSVs" / "PB1_V1_location.csv").exists()
    assert obj_local.error_gen_struct_folder == 1

    bucket = _sembrar_almacen_gcs("bucket-copy-csv-falta")
    bucket.objetos["dest/TERMICA/PB1_V1/PB1_V1_meta.csv"] = contenido_meta

    obj_gcs = pipeline.GenStructFolder(logger)
    obj_gcs.root_folder = "gs://bucket-copy-csv-falta/dest"
    obj_gcs.csvs_root_folder = almacen_mod.unir(obj_gcs.root_folder, "CSVs")
    obj_gcs.copy_flight_csvs("gs://bucket-copy-csv-falta/dest/TERMICA/PB1_V1", _noop_progress())

    assert bucket.objetos["dest/CSVs/PB1_V1_meta.csv"] == contenido_meta
    assert "dest/CSVs/PB1_V1_location.csv" not in bucket.objetos
    assert obj_gcs.error_gen_struct_folder == 1


# --- SplitImages.convert_dji_image_to_tif: skip por TIFF ya existente -------

def test_convert_dji_image_skip_por_tiff_existente_local_y_gcs(tmp_path, logger, monkeypatch):
    """Con el `.tiff` de destino ya presente (tamaño > 0), NO debe invocarse el
    conversor externo (`subprocess.run`) ni en local ni en `gs://…`: el `if`
    de `convert_dji_image_to_tif` tiene que cortar antes, vía
    `almacen.existe_ruta` + `almacen.tamano_de`."""
    llamadas = []

    def fake_run(*args, **kwargs):
        llamadas.append(args)
        raise AssertionError("No debía invocarse el conversor: el TIFF ya existía.")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    progress = _noop_progress()

    # --- Local ---
    output_local = tmp_path / "TIFF_local"
    output_local.mkdir()
    (output_local / "DJI_0001_T.tiff").write_bytes(b"contenido-tiff-no-vacio")

    obj_local = pipeline.SplitImages(logger)
    obj_local.convert_dji_image_to_tif(
        str(tmp_path / "TERMICA_local"), str(output_local), "DJI_0001_T.JPG",
        "exiftool", "dji_utility", progress, progress,
    )
    assert obj_local.tiff_already_converted == 1
    assert obj_local.error_splitting_images == 0

    # --- gs:// ---
    bucket = _sembrar_almacen_gcs("bucket-skip-tiff")
    bucket.objetos["out/DJI_0001_T.tiff"] = b"contenido-tiff-no-vacio"

    obj_gcs = pipeline.SplitImages(logger)
    obj_gcs.convert_dji_image_to_tif(
        "gs://bucket-skip-tiff/in", "gs://bucket-skip-tiff/out", "DJI_0001_T.JPG",
        "exiftool", "dji_utility", progress, progress,
    )
    assert obj_gcs.tiff_already_converted == 1
    assert obj_gcs.error_splitting_images == 0

    assert not llamadas, "El conversor DJI se invocó pese a que el TIFF ya existía."


def test_convert_dji_image_no_skip_si_tiff_vacio_gcs(monkeypatch):
    """Un TIFF de 0 bytes (subida cortada a medias) NO cuenta como "ya
    convertido": `tamano_de` debe leerlo y el `and` debe seguir evaluando
    False, igual que hace `os.path.getsize(...) > 0` en local."""
    bucket = _sembrar_almacen_gcs("bucket-skip-tiff-vacio")
    bucket.objetos["out/DJI_0001_T.tiff"] = b""  # tamaño 0

    almacen, prefijo = almacen_mod.abrir_almacen("gs://bucket-skip-tiff-vacio/out")
    tiff_path = almacen_mod.unir("gs://bucket-skip-tiff-vacio/out", "DJI_0001_T.tiff")
    assert almacen_mod.existe_ruta(tiff_path) is True
    assert almacen_mod.tamano_de(tiff_path) == 0


# --- GenStructFolder.write_videofiles_csv: mismo nombre y contenido local/gs:// (FIX 2) ---

def test_write_videofiles_csv_local_y_gcs_mismo_nombre_y_contenido(tmp_path, logger):
    import pandas as pd
    import utils as utils_module

    df_videofiles = pd.DataFrame(
        {"New Name": ["PB1_V1_0001.JPG"], "Original Name": ["DJI_0001_T.JPG"], "Degree": [90]}
    )

    # --- Local ---
    raiz_local = tmp_path / "root"
    obj_local = pipeline.GenStructFolder(logger)
    obj_local.csvs_root_folder = str(raiz_local / "CSVs")
    obj_local.write_videofiles_csv(str(raiz_local / "TERMICA" / "PB1_V1"), df_videofiles)

    ruta_local = raiz_local / "CSVs" / utils_module.CRITERIO_DIRNAME / "PB1_V1_Videofiles.csv"
    assert ruta_local.exists()
    contenido_local = ruta_local.read_bytes()
    assert b"PB1_V1_0001.JPG" in contenido_local

    # --- gs:// ---
    bucket = _sembrar_almacen_gcs("bucket-videofiles-csv")

    obj_gcs = pipeline.GenStructFolder(logger)
    obj_gcs.csvs_root_folder = "gs://bucket-videofiles-csv/dest/CSVs"
    obj_gcs.write_videofiles_csv("gs://bucket-videofiles-csv/dest/TERMICA/PB1_V1", df_videofiles)

    clave_esperada = f"dest/CSVs/{utils_module.CRITERIO_DIRNAME}/PB1_V1_Videofiles.csv"
    assert clave_esperada in bucket.objetos
    assert bucket.objetos[clave_esperada] == contenido_local
    # El nombre publicado NUNCA es el del temporal: ninguna otra clave del bucket
    # contiene "Videofiles.csv" aparte de la esperada.
    claves_videofiles = [k for k in bucket.objetos if "Videofiles.csv" in k]
    assert claves_videofiles == [clave_esperada]


# --- pipeline._reflink_or_copy: origen inexistente no desactiva el reflink (FIX 3) ---

def test_reflink_or_copy_origen_inexistente_no_desactiva_reflink_global(tmp_path):
    pipeline._REFLINK_SOPORTADO = None
    try:
        origen_inexistente = str(tmp_path / "no_existe.jpg")
        destino = str(tmp_path / "destino.jpg")

        with pytest.raises(FileNotFoundError):
            pipeline._reflink_or_copy(origen_inexistente, destino)

        assert pipeline._REFLINK_SOPORTADO is None, (
            "Un origen ausente (caso rutinario: vuelo RGB sin location.csv) no debe "
            "desactivar el reflink CoW para el resto del run."
        )
    finally:
        pipeline._REFLINK_SOPORTADO = None
