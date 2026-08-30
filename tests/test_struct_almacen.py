"""Migración de la etapa `struct` completa a la capa URI-aware
(`atom_core.almacen`): `GenStructFolder.gen_folder_struct` (con reparto entre
shards) y `checking_results_gen_struct_folder` (barrido a SIN_ORDENAR).

Corre la etapa entera sobre un doble de bucket en memoria y compara el
resultado con la MISMA corrida sobre disco: mismas rutas relativas al destino
y mismo contenido byte a byte (sha256). Mismo estilo de doble que
`tests/test_split_almacen.py` y `tests/test_struct_primitivas_almacen.py`.
"""
import datetime as _dt
import hashlib
import os
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
from atom_core.almacen_gcs import AlmacenGCS
from pipeline import GenStructFolder

RGB = ("DJI_0001_W.JPG", "DJI_0002_W.JPG")
TERMICAS = ("DJI_0001_T.JPG", "DJI_0002_T.JPG")
FUERA = "DJI_9999_W.JPG"

# Timestamps EXIF reales (no mockeados: se escriben con `make_dji_jpeg`) que
# encajan cada imagen en su vuelo, salvo `FUERA`, que no cae en ninguna
# ventana del estadillo y debe quedar suelta -> SIN_ORDENAR.
_TS = {
    "DJI_0001_W.JPG": _dt.datetime(2026, 3, 17, 10, 2, 0),
    "DJI_0002_W.JPG": _dt.datetime(2026, 3, 17, 11, 2, 0),
    "DJI_0001_T.JPG": _dt.datetime(2026, 3, 17, 10, 2, 30),
    "DJI_0002_T.JPG": _dt.datetime(2026, 3, 17, 11, 2, 30),
    FUERA: _dt.datetime(2026, 3, 17, 23, 0, 0),
}


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


# --- Doble en memoria del backend GCS (mismo estilo que test_split_almacen.py)

class BlobFalso:
    def __init__(self, bucket, nombre):
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
        # Cuenta las descargas REALES por objeto, para el test que fija que
        # cada imagen se descarga una única vez (EXIF cacheado por corrida).
        self.bucket.descargas[self.name] = self.bucket.descargas.get(self.name, 0) + 1

    def delete(self) -> None:
        del self.bucket.objetos[self.name]

    def reload(self) -> None:
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}
        self.descargas: dict[str, int] = {}

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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Helpers de estadillo/semilla, mismo patrón que test_gen_struct_folder.py

def _estadillo(path, filas):
    lineas = ["PB;Vuelo;Fecha;Hora_de_inicio;Hora_final"]
    lineas += [";".join(str(c) for c in fila) for fila in filas]
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


def _estadillo_dos_vuelos(tmp_path):
    """PB1_V1: 10:00-10:05, PB2_V1: 11:00-11:05, ambos 2026-03-17."""
    return _estadillo(tmp_path / "est.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
        ("2", "1", "2026:03:17", "11:00:00", "11:05:00"),
    ])


def _semilla_local(tmp_path, make_dji_jpeg, con_fuera=False):
    """Destino plano (RGB/TERMICA) tal y como lo deja `split`, en disco."""
    root = tmp_path / "destino_local"
    (root / "RGB").mkdir(parents=True)
    (root / "TERMICA").mkdir(parents=True)
    for nombre in RGB:
        make_dji_jpeg(str(root / "RGB" / nombre), dt_val=_TS[nombre])
    for nombre in TERMICAS:
        make_dji_jpeg(str(root / "TERMICA" / nombre), dt_val=_TS[nombre])
    if con_fuera:
        make_dji_jpeg(str(root / "RGB" / FUERA), dt_val=_TS[FUERA])
    return root


def _semilla_gcs(bucket, bucket_nombre, prefijo, tmp_path, make_dji_jpeg, con_fuera=False):
    """Mismas imágenes (mismo EXIF), subidas al bucket falso bajo
    `prefijo/RGB` y `prefijo/TERMICA`. Se generan a disco primero (para tener
    JPEG+EXIF reales) en una carpeta de caché compartida entre llamadas, así
    que el mismo nombre siempre produce los mismos bytes."""
    carpeta = tmp_path / "semilla_gcs"
    carpeta.mkdir(exist_ok=True)
    todas = list(RGB) + list(TERMICAS) + ([FUERA] if con_fuera else [])
    for nombre in todas:
        ruta_local = carpeta / nombre
        if not ruta_local.exists():
            make_dji_jpeg(str(ruta_local), dt_val=_TS[nombre])
    rgb_a_subir = list(RGB) + ([FUERA] if con_fuera else [])
    for nombre in rgb_a_subir:
        bucket.objetos[f"{prefijo}/RGB/{nombre}".strip("/")] = (carpeta / nombre).read_bytes()
    for nombre in TERMICAS:
        bucket.objetos[f"{prefijo}/TERMICA/{nombre}".strip("/")] = (carpeta / nombre).read_bytes()
    return f"gs://{bucket_nombre}/{prefijo}"


def _arbol_local(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in root.rglob("*") if p.is_file()
    }


def _arbol_gcs(bucket: BucketFalso, prefijo: str) -> dict[str, bytes]:
    pref = prefijo.strip("/") + "/"
    return {clave[len(pref):]: contenido for clave, contenido in bucket.objetos.items()
            if clave.startswith(pref)}


# --- gen_folder_struct: local vs gs://, mismo árbol de salida --------------

def test_gen_folder_struct_local_y_gcs_dan_el_mismo_arbol(tmp_path, logger, make_dji_jpeg):
    """Misma corrida (mismo estadillo, mismas imágenes/EXIF) sobre disco y
    sobre `gs://…`: mismas rutas de salida y mismo contenido byte a byte,
    incluida la copia del estadillo a ESTADILLOS/ y la imagen que se queda
    fuera de toda ventana horaria (sin organizar, a la espera del barrido)."""
    estadillo = _estadillo_dos_vuelos(tmp_path)
    cb = FakeSignal()

    root = _semilla_local(tmp_path, make_dji_jpeg, con_fuera=True)
    gsf_local = GenStructFolder(logger)
    gsf_local.total_images_number = 5
    gsf_local.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0, cb, cb)
    arbol_local = _arbol_local(root)

    bucket = _sembrar_almacen_gcs("bucket-struct-arbol")
    base = _semilla_gcs(bucket, "bucket-struct-arbol", "dest", tmp_path, make_dji_jpeg, con_fuera=True)
    gsf_gcs = GenStructFolder(logger)
    gsf_gcs.total_images_number = 5
    gsf_gcs.gen_folder_struct(estadillo, base, base, True, 0, 0, 0, cb, cb)
    arbol_gcs = _arbol_gcs(bucket, "dest")

    assert set(arbol_local) == set(arbol_gcs)
    for ruta in arbol_local:
        assert _sha256(arbol_local[ruta]) == _sha256(arbol_gcs[ruta]), ruta

    # Control expreso de los sitios clave, no solo el conjunto de claves.
    assert "RGB/PB1/PB1_V1/DJI_0001_W.JPG" in arbol_gcs
    assert "RGB/PB2/PB2_V1/DJI_0002_W.JPG" in arbol_gcs
    assert "TERMICA/PB1/PB1_V1/DJI_0001_T.JPG" in arbol_gcs
    assert "ESTADILLOS/est.csv" in arbol_gcs
    # La que no cae en ninguna ventana se queda suelta en la raíz de RGB.
    assert f"RGB/{FUERA}" in arbol_gcs


# --- Reparto entre shards: la unión da lo mismo que sin repartir ----------

@pytest.mark.parametrize("shard_count", [2, 3])
def test_reparto_en_shards_gcs_da_lo_mismo_que_sin_repartir(tmp_path, logger, make_dji_jpeg, shard_count):
    estadillo = _estadillo_dos_vuelos(tmp_path)
    cb = FakeSignal()

    # --- baseline: una sola tarea ---
    bucket_base = _sembrar_almacen_gcs(f"bucket-shard-base-{shard_count}")
    base = _semilla_gcs(bucket_base, f"bucket-shard-base-{shard_count}", "dest", tmp_path, make_dji_jpeg)
    gsf = GenStructFolder(logger)
    gsf.total_images_number = 4
    gsf.gen_folder_struct(estadillo, base, base, True, 0, 0, 0, cb, cb)
    arbol_base = _arbol_gcs(bucket_base, "dest")

    # --- repartido en N shards, cada uno mueve solo lo suyo sobre el MISMO
    # destino lógico (bucket distinto para no mezclarse con el baseline) ---
    bucket_rep = _sembrar_almacen_gcs(f"bucket-shard-rep-{shard_count}")
    rep = _semilla_gcs(bucket_rep, f"bucket-shard-rep-{shard_count}", "dest", tmp_path, make_dji_jpeg)
    for i in range(shard_count):
        gsf_i = GenStructFolder(logger)
        gsf_i.total_images_number = 4
        gsf_i.gen_folder_struct(estadillo, rep, rep, True, 0, 0, 0, cb, cb,
                                shard_index=i, shard_count=shard_count)
    arbol_rep = _arbol_gcs(bucket_rep, "dest")

    assert arbol_rep == arbol_base


# --- checking_results_gen_struct_folder: barrido a SIN_ORDENAR ------------

def test_checking_results_barre_sobrantes_a_sin_ordenar_local_y_gcs(tmp_path, logger, make_dji_jpeg):
    estadillo = _estadillo_dos_vuelos(tmp_path)
    cb = FakeSignal()

    # --- local ---
    root = _semilla_local(tmp_path, make_dji_jpeg, con_fuera=True)
    gsf = GenStructFolder(logger)
    gsf.total_images_number = 5
    gsf.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0, cb, cb)
    apartadas_t, apartadas_r = gsf.checking_results_gen_struct_folder(str(root), cb)
    assert (apartadas_t, apartadas_r) == (0, 1)
    assert os.listdir(root / "SIN_ORDENAR" / "RGB") == [FUERA]
    assert not os.path.isdir(root / "SIN_ORDENAR" / "TERMICA")

    # --- gs:// ---
    bucket = _sembrar_almacen_gcs("bucket-struct-barrido")
    base = _semilla_gcs(bucket, "bucket-struct-barrido", "dest", tmp_path, make_dji_jpeg, con_fuera=True)
    gsf2 = GenStructFolder(logger)
    gsf2.total_images_number = 5
    gsf2.gen_folder_struct(estadillo, base, base, True, 0, 0, 0, cb, cb)
    apartadas_t2, apartadas_r2 = gsf2.checking_results_gen_struct_folder(base, cb)
    assert (apartadas_t2, apartadas_r2) == (0, 1)

    claves_sin_ordenar = {k for k in bucket.objetos if k.startswith("dest/SIN_ORDENAR/")}
    assert claves_sin_ordenar == {f"dest/SIN_ORDENAR/RGB/{FUERA}"}


# --- Auditoría: una sola descarga por imagen -------------------------------

def test_gen_folder_struct_descarga_cada_imagen_una_sola_vez(tmp_path, logger, make_dji_jpeg):
    """El EXIF de cada imagen se lee UNA vez (precarga en paralelo + caché de
    la corrida), no dos: el mismo defecto que se corrigió en `split` (F2) para
    `struct` sería una descarga por precarga y otra al asignar el vuelo."""
    estadillo = _estadillo_dos_vuelos(tmp_path)
    cb = FakeSignal()

    bucket = _sembrar_almacen_gcs("bucket-struct-una-descarga")
    base = _semilla_gcs(bucket, "bucket-struct-una-descarga", "dest", tmp_path, make_dji_jpeg)
    gsf = GenStructFolder(logger)
    gsf.total_images_number = 4
    gsf.gen_folder_struct(estadillo, base, base, True, 0, 0, 0, cb, cb)

    fuentes = [f"dest/RGB/{n}" for n in RGB] + [f"dest/TERMICA/{n}" for n in TERMICAS]
    for clave in fuentes:
        assert bucket.descargas.get(clave, 0) == 1, f"{clave}: {bucket.descargas.get(clave, 0)} descargas"

    # Y el renombrado/organización por vuelo sí se aplicó (no quedó desactivado
    # en silencio): cada imagen terminó en la carpeta de su vuelo real.
    assert bucket.objetos.get("dest/RGB/PB1/PB1_V1/DJI_0001_W.JPG") is not None
    assert bucket.objetos.get("dest/RGB/PB2/PB2_V1/DJI_0002_W.JPG") is not None


# --- precargar_timestamps: un fallo por imagen no aborta el lote -----------

def test_precargar_timestamps_una_imagen_rota_no_aborta_el_lote(tmp_path, logger, make_dji_jpeg):
    """Una imagen que revienta al leer su EXIF (blob borrado, timeout GCS,
    5xx...) no debe tirar abajo `precargar_timestamps` entero: el resto de
    imágenes del lote tiene que quedar cacheado igual."""
    root = tmp_path / "carpeta"
    root.mkdir()
    for nombre in RGB:
        make_dji_jpeg(str(root / nombre), dt_val=_TS[nombre])
    rota = "DJI_ROTA_W.JPG"
    make_dji_jpeg(str(root / rota), dt_val=_TS["DJI_0001_W.JPG"])

    gsf = GenStructFolder(logger)
    ruta_rota = str(root / rota)
    original = gsf.exif_management_obj.get_timestamp_from_image

    def _get_timestamp_from_image_falla_en_rota(ruta_local, *args, **kwargs):
        if os.path.basename(str(ruta_local)) == rota:
            raise RuntimeError("blob borrado / timeout GCS simulado")
        return original(ruta_local, *args, **kwargs)

    gsf.exif_management_obj.get_timestamp_from_image = _get_timestamp_from_image_falla_en_rota

    gsf.precargar_timestamps([str(root)])

    for nombre in RGB:
        ruta = str(root / nombre)
        assert ruta in gsf._timestamps_cache
        assert gsf._timestamps_cache[ruta] is not None
    # La rota NO queda cacheada: cae por la ruta lenta/contada como error de
    # siempre (el try/except por-imagen de gen_folder_struct), no se da por
    # buena con un None en caché.
    assert ruta_rota not in gsf._timestamps_cache
