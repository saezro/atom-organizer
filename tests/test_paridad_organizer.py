"""Paridad disco vs `gs://` de la etapa `split` completa (árbol de carpetas).

`tests/test_almacen_rutas.py` y `tests/test_split_almacen.py` fijan el contrato
de la capa `atom_core.almacen` y de `split_image`/`split_one_image` unidad a
unidad. Este fichero sube un nivel: corre la etapa `split` sobre un ÁRBOL de
carpetas completo (recorrido incluido, vía `atom_core.sharding.carpetas_con_imagenes`)
tanto contra disco como contra un bucket GCS FALSO (doble en memoria, sin red
ni SDK real), y compara el resultado fichero a fichero por sha256 + ruta
relativa: si algo diverge entre backends, tiene que fallar aquí.

No se usa el `ProcessPoolExecutor` real (`SplitImages.split_images` vía
`utils.run_batch`): con `fork`, cada worker hereda una COPIA de memoria del
doble de bucket, invisible para el proceso de test (mismo motivo documentado en
`test_split_almacen.py`). Se llama a `pipeline.split_one_image` directamente
por imagen -es la misma unidad de trabajo real, la que ejecuta cada worker-,
recorriendo las carpetas con `sharding.carpetas_con_imagenes`, que es la propia
unidad de reparto de la etapa.
"""
import hashlib
import os
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
import pipeline
import utils as utils_mod
from atom_core import sharding
from atom_core.almacen_gcs import AlmacenGCS

RGB = ("DJI_0001_W.JPG", "DJI_0002_W.JPG")
TERMICAS = ("DJI_0001_T.JPG", "DJI_0002_T.JPG")


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS, mismo estilo que test_split_almacen.py

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

    def delete(self) -> None:
        del self.bucket.objetos[self.name]

    def reload(self) -> None:
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}

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
    """Siembra la caché de `abrir_almacen` con un `AlmacenGCS` de prueba, sin
    SDK real y sin red (mismo mecanismo que `test_split_almacen.py`)."""
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


def _sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


# --- Árbol de fixture: dos carpetas a distinta profundidad, con RGB y térmica

def _construir_arbol(raiz: Path, make_dji_jpeg, *, inflar_rgb: int = 0) -> dict[str, bytes]:
    """Escribe `DJI_A/` (2 RGB + 1 térmica) y `DJI_B/sub/` (1 térmica, sin RGB)
    bajo `raiz`. Devuelve `{ruta_relativa: bytes}` de TODO lo escrito, para
    poder sembrar el bucket falso con el mismo contenido byte a byte.

    `inflar_rgb`, si es > 0, añade ese número de bytes de relleno al final de
    cada fichero RGB (tras el JPEG, que `_copiar_split` copia tal cual sin
    decodificar): deja las RGB deliberadamente MUCHO más grandes que las
    térmicas (que quedan con su tamaño natural de unos pocos KB), para poder
    ejercer de verdad el modo `mode_size=True` de `split_image` con un
    `min_size` que caiga entre ambos tamaños."""
    estructura = {
        "DJI_A": (RGB, (TERMICAS[0],)),
        "DJI_B/sub": ((), (TERMICAS[1],)),
    }
    contenidos: dict[str, bytes] = {}
    for carpeta, (rgb_names, term_names) in estructura.items():
        destino = raiz / carpeta
        destino.mkdir(parents=True, exist_ok=True)
        for nombre in rgb_names:
            ruta = destino / nombre
            make_dji_jpeg(str(ruta))
            if inflar_rgb:
                with open(ruta, "ab") as f:
                    f.write(b"\x00" * inflar_rgb)
            contenidos[f"{carpeta}/{nombre}"] = ruta.read_bytes()
        for nombre in term_names:
            ruta = destino / nombre
            make_dji_jpeg(str(ruta))
            contenidos[f"{carpeta}/{nombre}"] = ruta.read_bytes()
    return contenidos


def _utils_obj():
    return utils_mod.Utils(organizer_logger=None)


def _preparar_salida_local(salida: Path) -> None:
    """En local, `RGB/`/`TERMICA/` no se crean solas para el lado térmico
    (solo `_rgb_destination_folder` hace `os.makedirs`); hay que dejarlas
    listas antes de separar, igual que hace `test_split_almacen.py`."""
    (salida / "RGB").mkdir(parents=True, exist_ok=True)
    (salida / "TERMICA").mkdir(parents=True, exist_ok=True)


def _correr_split_arbol(input_root: str, output_root: str, *, mode: bool,
                        rename: bool, min_size: str = "0,000001",
                        compress_checked: bool = False) -> None:
    """Corre la etapa `split` sobre TODO el árbol de `input_root` (recorrido +
    separación de cada imagen), sin `ProcessPoolExecutor`: llama a
    `pipeline.split_one_image` -la unidad real de cada worker- imagen a
    imagen, igual de válido para una ruta de disco que para una `gs://…`."""
    utils_obj = _utils_obj()
    listar_fuentes = lambda ruta: utils_obj.get_images_from_dir(ruta, solo_fuente=True)
    carpetas = sharding.carpetas_con_imagenes(input_root, listar_fuentes)
    for carpeta in carpetas:
        imagenes = utils_obj.get_images_from_dir(carpeta)
        cfg = pipeline.SplitJobConfig(
            input_folder=carpeta, output_folder=output_root, mode=mode,
            min_size=min_size, thermal_sufix="_T", rgb_sufix="_W",
            compress_checked=compress_checked, quality=70, rename=rename,
            mismatch_hours=0, mismatch_minutes=0,
        )
        for imagen in sorted(imagenes):
            pipeline.split_one_image(imagen, cfg)


def _resultado_local(output_root: Path) -> dict[str, str]:
    """`{ruta_relativa: sha256}` de TODO lo que quedó en `output_root`."""
    salida: dict[str, str] = {}
    for dirpath, _dirs, filenames in os.walk(output_root):
        for nombre in filenames:
            ruta = Path(dirpath) / nombre
            relativo = ruta.relative_to(output_root).as_posix()
            salida[relativo] = _sha256(ruta.read_bytes())
    return salida


def _resultado_gcs(bucket: BucketFalso, prefijo: str) -> dict[str, str]:
    """`{ruta_relativa: sha256}` de todo lo subido al bucket bajo `prefijo`."""
    prefijo = prefijo.strip("/") + "/" if prefijo else ""
    salida: dict[str, str] = {}
    for clave, datos in bucket.objetos.items():
        if clave.startswith(prefijo):
            relativo = clave[len(prefijo):]
            salida[relativo] = _sha256(datos)
    return salida


# --- Paridad completa, parametrizada por modo de reparto y renombrado -------

@pytest.mark.parametrize("mode_size", [False, True], ids=["sufijo", "tamano"])
@pytest.mark.parametrize("rename", [False, True], ids=["sin_renombrar", "renombrado"])
def test_split_arbol_paridad_disco_vs_gcs(tmp_path, make_dji_jpeg, mode_size, rename):
    """El mismo árbol de dos carpetas, separado con disco y con `gs://…`, deja
    exactamente los mismos ficheros (mismas rutas relativas, mismo contenido)
    en ambos backends, para las cuatro combinaciones de reparto/renombrado.

    En el modo `tamano` (`mode_size=True`) las RGB se inflan a propósito muy
    por encima de las térmicas y `min_size` cae justo entre ambos tamaños, de
    forma que el umbral separa de verdad unas de otras (si todo cayera del
    mismo lado, el modo no estaría ejerciéndose)."""
    if mode_size:
        # RGB infladas a ~2 MB; térmicas se quedan en su tamaño natural (KB).
        # Umbral en 1 MB: cae limpiamente entre ambos.
        inflar_rgb = 2_000_000
        min_size = "1,0"
    else:
        inflar_rgb = 0
        min_size = "0,000001"
    contenidos = _construir_arbol(tmp_path / "in_local", make_dji_jpeg, inflar_rgb=inflar_rgb)

    # --- disco ---
    entrada_local = str(tmp_path / "in_local")
    salida_local = tmp_path / "out_local"
    _preparar_salida_local(salida_local)
    _correr_split_arbol(entrada_local, str(salida_local), mode=mode_size, rename=rename,
                        min_size=min_size)
    resultado_local = _resultado_local(salida_local)

    # --- gs:// (mismo árbol, mismos bytes) ---
    nombre_bucket = f"bucket-paridad-{mode_size}-{rename}"
    bucket = _sembrar_almacen_gcs(nombre_bucket)
    for relativo, datos in contenidos.items():
        bucket.objetos[f"vuelo/in/{relativo}"] = datos
    _correr_split_arbol(
        f"gs://{nombre_bucket}/vuelo/in", f"gs://{nombre_bucket}/vuelo/out",
        mode=mode_size, rename=rename, min_size=min_size,
    )
    resultado_gcs = _resultado_gcs(bucket, "vuelo/out")

    assert resultado_local, "la corrida de control no dejó nada en disco"
    assert resultado_gcs == resultado_local

    if mode_size:
        # El umbral tiene que haber separado de verdad: algo en RGB Y algo en
        # TERMICA, o el modo tamaño no se estaría ejerciendo de verdad.
        assert any(ruta.startswith("RGB/") for ruta in resultado_local), \
            "modo tamaño: ningún fichero cayó en RGB/ (umbral mal calibrado)"
        assert any(ruta.startswith("TERMICA/") for ruta in resultado_local), \
            "modo tamaño: ningún fichero cayó en TERMICA/ (umbral mal calibrado)"


def test_split_arbol_paridad_disco_vs_gcs_con_compresion(tmp_path, make_dji_jpeg):
    """Con `compress_checked=True` la imagen RGB pasa por un temporal local
    antes de publicarse (`CompressImage.compress_image`): confirma que ese
    camino, con su propio fichero intermedio, también deja el mismo resultado
    en disco y en `gs://…`."""
    contenidos = _construir_arbol(tmp_path / "in_local", make_dji_jpeg)

    entrada_local = str(tmp_path / "in_local")
    salida_local = tmp_path / "out_local"
    _preparar_salida_local(salida_local)
    _correr_split_arbol(entrada_local, str(salida_local), mode=False, rename=True,
                        compress_checked=True)
    resultado_local = _resultado_local(salida_local)

    nombre_bucket = "bucket-paridad-compresion"
    bucket = _sembrar_almacen_gcs(nombre_bucket)
    for relativo, datos in contenidos.items():
        bucket.objetos[f"vuelo/in/{relativo}"] = datos
    _correr_split_arbol(
        f"gs://{nombre_bucket}/vuelo/in", f"gs://{nombre_bucket}/vuelo/out",
        mode=False, rename=True, compress_checked=True,
    )
    resultado_gcs = _resultado_gcs(bucket, "vuelo/out")

    assert resultado_local
    assert resultado_gcs == resultado_local


# --- Unión de shards == corrida sin repartir ---------------------------------

@pytest.mark.parametrize("backend", ["local", "gcs"])
def test_union_de_shards_igual_a_corrida_sin_repartir(tmp_path, make_dji_jpeg, backend):
    """Repartir el mismo árbol entre 3 tareas (`sharding.repartir_imagenes`,
    la unidad de reparto real de la etapa `split`) y fusionar sus salidas debe
    dar EXACTAMENTE el mismo conjunto de rutas+sha256 que una corrida sin
    repartir: es el invariante que garantiza que Cloud Run con N tareas no
    pierde ni duplica ninguna imagen. Parametrizado por backend: el escenario
    real de Cloud Run con N tareas reparte sobre `gs://…`, no solo disco."""
    contenidos = _construir_arbol(tmp_path / "in", make_dji_jpeg)

    if backend == "local":
        entrada = str(tmp_path / "in")
    else:
        nombre_bucket = "bucket-union-shards"
        bucket = _sembrar_almacen_gcs(nombre_bucket)
        for relativo, datos in contenidos.items():
            bucket.objetos[f"vuelo/in/{relativo}"] = datos
        entrada = f"gs://{nombre_bucket}/vuelo/in"

    # --- corrida de control, sin repartir ---
    if backend == "local":
        salida_completa = tmp_path / "out_completo"
        _preparar_salida_local(salida_completa)
        salida_completa_str = str(salida_completa)
    else:
        salida_completa_str = f"gs://{nombre_bucket}/vuelo/out_completo"
    _correr_split_arbol(entrada, salida_completa_str, mode=False, rename=True)
    if backend == "local":
        resultado_completo = _resultado_local(salida_completa)
    else:
        resultado_completo = _resultado_gcs(bucket, "vuelo/out_completo")
    assert resultado_completo, "la corrida de control no dejó nada en disco/bucket"

    # --- 3 shards, cada uno a su propia carpeta de salida ---
    utils_obj = _utils_obj()
    listar_fuentes = lambda ruta: utils_obj.get_images_from_dir(ruta, solo_fuente=True)
    carpetas = sharding.carpetas_con_imagenes(entrada, listar_fuentes)

    fusion: dict[str, str] = {}
    for shard_index in range(3):
        reparto = sharding.repartir_imagenes(
            carpetas, utils_obj.get_images_from_dir, shard_index, 3)
        if backend == "local":
            salida_shard = tmp_path / f"out_shard_{shard_index}"
            _preparar_salida_local(salida_shard)
            salida_shard_str = str(salida_shard)
        else:
            salida_shard_str = f"gs://{nombre_bucket}/vuelo/out_shard_{shard_index}"
        for carpeta, imagenes in reparto.items():
            cfg = pipeline.SplitJobConfig(
                input_folder=carpeta, output_folder=salida_shard_str, mode=False,
                min_size="0,000001", thermal_sufix="_T", rgb_sufix="_W",
                compress_checked=False, quality=70, rename=True,
                mismatch_hours=0, mismatch_minutes=0,
            )
            for imagen in sorted(imagenes):
                pipeline.split_one_image(imagen, cfg)

        if backend == "local":
            resultado_shard = _resultado_local(salida_shard)
        else:
            resultado_shard = _resultado_gcs(bucket, f"vuelo/out_shard_{shard_index}")
        for relativo, sha in resultado_shard.items():
            assert relativo not in fusion, f"colisión de ruta entre shards: {relativo}"
            fusion[relativo] = sha

    assert fusion == resultado_completo
