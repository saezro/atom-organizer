"""Migración de la etapa `split` a la capa URI-aware (`atom_core.almacen`).

Fija que `SplitImages.split_image` (la unidad que llama cada worker del
`ProcessPoolExecutor`, ver `pipeline.split_one_image`), `SplitImages.split_images`
(vía `Utils.get_images_from_dir`) y `atom_core.sharding.carpetas_con_imagenes`
reparten a RGB/TERMICA igual con rutas de disco y con `gs://…`.

No se prueba a través del `ProcessPoolExecutor` real (`split_images`/`iterate_folders`
en paralelo): con `fork`, cada worker hereda una COPIA de memoria del doble de bucket en
memoria, y lo que sube ahí es invisible para el proceso de test. Se llama a
`split_image` directamente, igual que hace `tests/test_split_paralelo.py` para
comparar contra el bucle secuencial — es la misma unidad de trabajo, solo que sin la
capa de paralelismo por medio.
"""
import hashlib
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
import pipeline
from atom_core import sharding
from atom_core.almacen_gcs import AlmacenGCS

RGB = ("DJI_0001_W.JPG", "DJI_0002_W.JPG")
TERMICAS = ("DJI_0001_T.JPG",)


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS, mismo estilo que test_almacen_rutas.py

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
        self.bucket.descargas[self.name] = self.bucket.descargas.get(self.name, 0) + 1

    def delete(self) -> None:
        del self.bucket.objetos[self.name]

    def reload(self) -> None:
        # Metadatos "reales" en este doble: el tamaño ya está disponible sin
        # descargar el contenido (`almacen.tamano_de` / `Almacen.tamano`).
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}
        # Cuenta las descargas REALES (`download_to_filename`) por objeto, para el
        # test que fija que `split_one_image` descarga cada origen una única vez.
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
    """Mismo mecanismo que `tests/test_almacen_rutas.py`: siembra la caché de
    `abrir_almacen` con un `AlmacenGCS` de prueba, sin SDK real y sin red."""
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sembrar_imagenes(bucket: BucketFalso, tmp_path, make_dji_jpeg, prefijo: str):
    """Escribe RGB+TERMICA con `make_dji_jpeg` a disco (para tener JPEG+EXIF+XMP
    reales) y sube esos bytes al bucket falso bajo `prefijo/<nombre>`."""
    carpeta = tmp_path / "semilla"
    carpeta.mkdir(exist_ok=True)
    for nombre in RGB + TERMICAS:
        ruta_local = carpeta / nombre
        if not ruta_local.exists():
            make_dji_jpeg(str(ruta_local))
        bucket.objetos[f"{prefijo}/{nombre}".strip("/")] = ruta_local.read_bytes()


def _obj(logger, total):
    obj = pipeline.SplitImages(logger)
    obj.total_images_number = total
    obj.current_image_number = 0
    return obj


def _progress():
    import types
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def _separar_una_a_una(obj, entrada, salida, imagenes, nombres_destino):
    """Igual que el bucle secuencial de `test_split_paralelo.py`, pero
    parametrizado por lista de imágenes y con el nombre de destino YA resuelto
    (`nombres_destino`), en vez de volver a calcularlo con `nombre_destino`.

    `nombre_destino` lee el EXIF del ORIGEN vía `exif.get_timestamp_from_image`
    (`pipeline.py`, fuera del alcance de esta migración: la task solo cubre
    `split_image`/`_rgb_destination_folder`/`iterate_folders`/`split_images`, no
    `exif.py`) y ese lector no es URI-aware — con un origen `gs://…` siempre
    devolvería "sin timestamp" y el renombrado no se aplicaría. Se calcula aquí
    una vez sobre el ORIGEN LOCAL (ambos orígenes son el mismo vuelo) y se
    reutiliza para las dos llamadas, así el test compara lo que sí está
    migrado (`split_image`) sin tropezar con ese hueco conocido."""
    progress = _progress()
    for imagen in sorted(imagenes):
        obj.split_image(
            imagen, entrada, salida, False, "5", "_T", "_W", True, 70,
            nombres_destino[imagen], True, progress,
        )


def test_split_image_reparte_igual_local_y_gcs(tmp_path, logger, make_dji_jpeg):
    """El mismo lote de imágenes, separado con `split_image` sobre disco y sobre
    `gs://…`, tiene que dejar los mismos ficheros (incluidos los que compresión +
    XMP escriben) con el mismo contenido en RGB/ y TERMICA/."""
    # --- local ---
    entrada_local = tmp_path / "in"
    entrada_local.mkdir()
    for nombre in RGB + TERMICAS:
        make_dji_jpeg(str(entrada_local / nombre))
    salida_local = tmp_path / "out_local"
    (salida_local / "RGB").mkdir(parents=True)
    (salida_local / "TERMICA").mkdir(parents=True)

    obj_local = _obj(logger, total=len(RGB) + len(TERMICAS))
    nombres_destino = {
        imagen: obj_local.nombre_destino(imagen, str(entrada_local), rename=True, mismatch_hours=0, mismatch_minutes=0)
        for imagen in RGB + TERMICAS
    }
    _separar_una_a_una(obj_local, str(entrada_local), str(salida_local), RGB + TERMICAS, nombres_destino)

    # --- gs:// ---
    bucket = _sembrar_almacen_gcs("bucket-split")
    _sembrar_imagenes(bucket, tmp_path, make_dji_jpeg, prefijo="vuelo/in")

    obj_gcs = _obj(logger, total=len(RGB) + len(TERMICAS))
    _separar_una_a_una(
        obj_gcs, "gs://bucket-split/vuelo/in", "gs://bucket-split/vuelo/out",
        RGB + TERMICAS, nombres_destino,
    )

    # Mismos nombres de fichero en RGB y en TERMICA a ambos lados.
    rgb_local = {p.name for p in (salida_local / "RGB").iterdir()}
    termica_local = {p.name for p in (salida_local / "TERMICA").iterdir()}
    rgb_gcs = {k.rsplit("/", 1)[-1] for k in bucket.objetos if k.startswith("vuelo/out/RGB/")}
    termica_gcs = {k.rsplit("/", 1)[-1] for k in bucket.objetos if k.startswith("vuelo/out/TERMICA/")}

    assert rgb_gcs == rgb_local
    assert termica_gcs == termica_local
    assert len(rgb_local) == len(RGB)
    assert len(termica_local) == len(TERMICAS)

    # Y mismo contenido byte a byte para cada nombre.
    for nombre in rgb_local:
        contenido_local = (salida_local / "RGB" / nombre).read_bytes()
        contenido_gcs = bucket.objetos[f"vuelo/out/RGB/{nombre}"]
        assert _sha256_bytes(contenido_local) == _sha256_bytes(contenido_gcs), nombre
    for nombre in termica_local:
        contenido_local = (salida_local / "TERMICA" / nombre).read_bytes()
        contenido_gcs = bucket.objetos[f"vuelo/out/TERMICA/{nombre}"]
        assert _sha256_bytes(contenido_local) == _sha256_bytes(contenido_gcs), nombre


def test_split_image_sin_renombrado_gcs_conserva_nombre_original(tmp_path, logger, make_dji_jpeg):
    """Control específico del caso `new_name == ""` (ver comentario de
    `nombre_salida` en `pipeline.split_image`): en `gs://…` no hay "es un
    directorio" para deducir el nombre, así que se calcula explícito."""
    bucket = _sembrar_almacen_gcs("bucket-sinrenombrar")
    _sembrar_imagenes(bucket, tmp_path, make_dji_jpeg, prefijo="in")

    obj = _obj(logger, total=len(RGB) + len(TERMICAS))
    sin_renombrado = {imagen: "" for imagen in RGB + TERMICAS}
    _separar_una_a_una(
        obj, "gs://bucket-sinrenombrar/in", "gs://bucket-sinrenombrar/out",
        RGB + TERMICAS, sin_renombrado,
    )

    subidos = {k.rsplit("/", 1)[-1] for k in bucket.objetos if k.startswith("out/")}
    assert subidos == set(RGB) | set(TERMICAS)


def test_get_images_from_dir_lista_gcs_como_listdir_local(tmp_path, make_dji_jpeg):
    """`Utils.get_images_from_dir` (usada por `split_images`) tiene que devolver
    el mismo conjunto de nombres, ordenados igual, para disco y para `gs://…`."""
    entrada_local = tmp_path / "in"
    entrada_local.mkdir()
    for nombre in RGB + TERMICAS:
        make_dji_jpeg(str(entrada_local / nombre))

    bucket = _sembrar_almacen_gcs("bucket-listado")
    _sembrar_imagenes(bucket, tmp_path, make_dji_jpeg, prefijo="in")
    # Fichero "en una subcarpeta": no debe salir en el listado (no recursivo),
    # igual que `os.listdir` tampoco baja a subcarpetas.
    bucket.objetos["in/sub/otra.JPG"] = b"no cuenta"

    import utils as utils_mod
    utils_obj = utils_mod.Utils(organizer_logger=None)

    esperado = utils_obj.get_images_from_dir(str(entrada_local))
    obtenido = utils_obj.get_images_from_dir("gs://bucket-listado/in")
    assert obtenido == esperado


def test_carpetas_con_imagenes_recorre_gcs_igual_que_os_walk(tmp_path, make_dji_jpeg):
    """`sharding.carpetas_con_imagenes` (unidad de reparto de `split`) tiene que
    encontrar las mismas carpetas-con-imágenes recorriendo un árbol `gs://…` de
    dos niveles que recorriendo el equivalente en disco."""
    import utils as utils_mod
    utils_obj = utils_mod.Utils(organizer_logger=None)
    listar_fuentes = lambda ruta: utils_obj.get_images_from_dir(ruta, solo_fuente=True)

    # --- local: DJI_A/ con imágenes, DJI_B/vacia/ con imágenes en un subnivel ---
    raiz_local = tmp_path / "origen"
    (raiz_local / "DJI_A").mkdir(parents=True)
    (raiz_local / "DJI_B" / "sub").mkdir(parents=True)
    make_dji_jpeg(str(raiz_local / "DJI_A" / RGB[0]))
    make_dji_jpeg(str(raiz_local / "DJI_B" / "sub" / RGB[1]))

    carpetas_local = sharding.carpetas_con_imagenes(str(raiz_local), listar_fuentes)

    # --- gs://: mismo árbol ---
    bucket = _sembrar_almacen_gcs("bucket-sharding")
    bucket.objetos["origen/DJI_A/" + RGB[0]] = (raiz_local / "DJI_A" / RGB[0]).read_bytes()
    bucket.objetos["origen/DJI_B/sub/" + RGB[1]] = (raiz_local / "DJI_B" / "sub" / RGB[1]).read_bytes()

    carpetas_gcs = sharding.carpetas_con_imagenes("gs://bucket-sharding/origen", listar_fuentes)

    sufijos_local = {str(Path(c).relative_to(raiz_local)).replace("\\", "/") for c in carpetas_local}
    sufijos_gcs = {c[len("gs://bucket-sharding/origen"):].strip("/") for c in carpetas_gcs}
    assert sufijos_gcs == sufijos_local == {"DJI_A", "DJI_B/sub"}


# --- Auditoría: doble descarga + renombrado roto en gs:// -------------------
#
# Los tres tests siguientes cierran los defectos de la auditoría sobre esta
# migración: la rama `mode_size=True` de `split_image` no se ejercitaba nunca
# sobre `gs://…` (defecto 1), y `nombre_destino` le pasaba a PIL el string
# `gs://…` literal en vez de una ruta local, desactivando el renombrado en
# silencio (defecto 2). El arreglo es el mismo para los dos: `split_one_image`
# resuelve el origen UNA vez y reparte esa ruta local aguas abajo.

def test_split_image_mode_size_gcs_reparte_por_tamano(tmp_path, logger, make_dji_jpeg):
    """La rama `mode_size=True` (separar por tamaño de fichero) tiene que
    funcionar igual sobre `gs://…` que en local: lee el tamaño por metadatos
    (`almacen.tamano_de`, sin descargar) y copia al lado correcto según el
    umbral."""
    bucket = _sembrar_almacen_gcs("bucket-mode-size")
    _sembrar_imagenes(bucket, tmp_path, make_dji_jpeg, prefijo="in")

    obj = _obj(logger, total=len(RGB) + len(TERMICAS))
    progress = _progress()
    # Umbral ínfimo (en MB): hasta el JPEG de prueba más pequeño lo supera, así
    # que las tres imágenes caen del lado RGB (sin comprimir: solo copia).
    for imagen in sorted(RGB + TERMICAS):
        obj.split_image(
            imagen, "gs://bucket-mode-size/in", "gs://bucket-mode-size/out",
            True, "0,000001", "_T", "_W", False, 70, "", True, progress,
        )

    subidos_rgb = {k.rsplit("/", 1)[-1] for k in bucket.objetos if k.startswith("out/RGB/")}
    assert subidos_rgb == set(RGB) | set(TERMICAS)
    for imagen in RGB + TERMICAS:
        assert bucket.objetos[f"out/RGB/{imagen}"] == bucket.objetos[f"in/{imagen}"]


def test_split_one_image_descarga_el_origen_una_sola_vez(tmp_path, logger, make_dji_jpeg):
    """DEFECTO 1 (crítico): con `mode_size=True`, antes se abría el origen dos
    veces (una para leer `stat().st_size`, otra para copiar/comprimir) -> dos
    descargas GCS por imagen. `split_one_image` (la unidad real que corre cada
    worker del `ProcessPoolExecutor`) tiene que descargar cada origen UNA sola
    vez."""
    bucket = _sembrar_almacen_gcs("bucket-una-descarga")
    _sembrar_imagenes(bucket, tmp_path, make_dji_jpeg, prefijo="in")

    imagen = RGB[0]
    cfg = pipeline.SplitJobConfig(
        input_folder="gs://bucket-una-descarga/in",
        output_folder="gs://bucket-una-descarga/out",
        mode=True, min_size="0,000001", thermal_sufix="_T", rgb_sufix="_W",
        compress_checked=False, quality=70, rename=True,
        mismatch_hours=0, mismatch_minutes=0,
    )
    pipeline.split_one_image(imagen, cfg)

    assert bucket.descargas.get(f"in/{imagen}", 0) == 1


def test_split_one_image_rename_gcs_usa_timestamp_exif(tmp_path, logger, make_dji_jpeg):
    """DEFECTO 2 (importante): `nombre_destino` pasaba el string `gs://…`
    literal a `exif.get_timestamp_from_image`; PIL no lo puede abrir, el
    `FileNotFoundError` lo traga `exif.py` y devuelve None, así que con
    `rename=True` el renombrado quedaba desactivado EN SILENCIO. Con el origen
    ya resuelto a una ruta local, el nombre de salida debe llevar el
    timestamp EXIF real."""
    bucket = _sembrar_almacen_gcs("bucket-rename")
    _sembrar_imagenes(bucket, tmp_path, make_dji_jpeg, prefijo="in")

    imagen = RGB[0]
    cfg = pipeline.SplitJobConfig(
        input_folder="gs://bucket-rename/in",
        output_folder="gs://bucket-rename/out",
        mode=True, min_size="0,000001", thermal_sufix="_T", rgb_sufix="_W",
        compress_checked=False, quality=70, rename=True,
        mismatch_hours=0, mismatch_minutes=0,
    )
    resultado = pipeline.split_one_image(imagen, cfg)

    # Con el defecto activo, `new_name` habría quedado "" (sin timestamp) y el
    # fichero se habría subido con su nombre original.
    assert resultado["new_name"] != ""
    assert resultado["new_name"].endswith("_" + imagen)
    subidos = {k.rsplit("/", 1)[-1] for k in bucket.objetos if k.startswith("out/")}
    assert resultado["new_name"] in subidos
