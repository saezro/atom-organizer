"""Migración de tres primitivas de bajo nivel a la capa URI-aware
(`atom_core.almacen`): `Utils.contar_imagenes_or_tmc` (utils.py), `safe_move`
(utils.py) y `sharding.pbs_del_destino`/`vuelos_del_destino`/`peso_de_ruta`
(atom_core/sharding.py).

Mismo estilo de doble en memoria del backend GCS que `tests/test_split_almacen.py`
y `tests/test_almacen_rutas.py`: se siembra la caché de `abrir_almacen` con un
`AlmacenGCS` de prueba, sin SDK real y sin red.
"""
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
from atom_core import sharding
from atom_core.almacen_gcs import AlmacenGCS

import utils as utils_mod


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS ---------------------------------------

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
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


# --- Utils.contar_imagenes_or_tmc -------------------------------------------

def test_contar_imagenes_or_tmc_recursivo_local_y_gcs_dan_igual(tmp_path, make_dji_jpeg):
    utils_obj = utils_mod.Utils(organizer_logger=None)

    raiz_local = tmp_path / "destino"
    (raiz_local / "PB1").mkdir(parents=True)
    (raiz_local / "PB1" / "sub").mkdir()
    (raiz_local / "SIN_ORDENAR").mkdir()
    make_dji_jpeg(str(raiz_local / "PB1" / "DJI_0001_W.JPG"))
    make_dji_jpeg(str(raiz_local / "PB1" / "sub" / "DJI_0002_W.JPG"))
    make_dji_jpeg(str(raiz_local / "SIN_ORDENAR" / "DJI_0003_W.JPG"))
    (raiz_local / "PB1" / "algo.tmc").write_bytes(b"x")

    bucket = _sembrar_almacen_gcs("bucket-contar")
    for nombre_local in ("PB1/DJI_0001_W.JPG", "PB1/sub/DJI_0002_W.JPG",
                         "SIN_ORDENAR/DJI_0003_W.JPG"):
        bucket.objetos[f"destino/{nombre_local}"] = (raiz_local / nombre_local).read_bytes()
    bucket.objetos["destino/PB1/algo.tmc"] = b"x"

    total_local = utils_obj.contar_imagenes_or_tmc(str(raiz_local))
    total_gcs = utils_obj.contar_imagenes_or_tmc("gs://bucket-contar/destino")
    assert total_local == total_gcs == 2  # SIN_ORDENAR siempre excluida

    tmc_local = utils_obj.contar_imagenes_or_tmc(str(raiz_local), tmc=True)
    tmc_gcs = utils_obj.contar_imagenes_or_tmc("gs://bucket-contar/destino", tmc=True)
    assert tmc_local == tmc_gcs == 1


def test_contar_imagenes_or_tmc_no_recursivo_local_y_gcs_dan_igual(tmp_path, make_dji_jpeg):
    utils_obj = utils_mod.Utils(organizer_logger=None)

    raiz_local = tmp_path / "destino"
    (raiz_local / "sub").mkdir(parents=True)
    make_dji_jpeg(str(raiz_local / "DJI_0001_W.JPG"))
    make_dji_jpeg(str(raiz_local / "sub" / "DJI_0002_W.JPG"))

    bucket = _sembrar_almacen_gcs("bucket-contar-plano")
    bucket.objetos["destino/DJI_0001_W.JPG"] = (raiz_local / "DJI_0001_W.JPG").read_bytes()
    bucket.objetos["destino/sub/DJI_0002_W.JPG"] = (raiz_local / "sub" / "DJI_0002_W.JPG").read_bytes()

    total_local = utils_obj.contar_imagenes_or_tmc(str(raiz_local), recursivo=False)
    total_gcs = utils_obj.contar_imagenes_or_tmc("gs://bucket-contar-plano/destino", recursivo=False)
    assert total_local == total_gcs == 1


def test_contar_imagenes_or_tmc_exclude_folders_local_y_gcs_dan_igual(tmp_path, make_dji_jpeg):
    utils_obj = utils_mod.Utils(organizer_logger=None)

    raiz_local = tmp_path / "destino"
    (raiz_local / "excluida").mkdir(parents=True)
    (raiz_local / "buena").mkdir()
    make_dji_jpeg(str(raiz_local / "excluida" / "DJI_0001_W.JPG"))
    make_dji_jpeg(str(raiz_local / "buena" / "DJI_0002_W.JPG"))

    bucket = _sembrar_almacen_gcs("bucket-contar-excl")
    bucket.objetos["destino/excluida/DJI_0001_W.JPG"] = (raiz_local / "excluida" / "DJI_0001_W.JPG").read_bytes()
    bucket.objetos["destino/buena/DJI_0002_W.JPG"] = (raiz_local / "buena" / "DJI_0002_W.JPG").read_bytes()

    total_local = utils_obj.contar_imagenes_or_tmc(str(raiz_local), exclude_folders=["excluida"])
    total_gcs = utils_obj.contar_imagenes_or_tmc("gs://bucket-contar-excl/destino", exclude_folders=["excluida"])
    assert total_local == total_gcs == 1


# --- safe_move ---------------------------------------------------------------

def test_safe_move_gcs_mismo_bucket_usa_mover(tmp_path, make_dji_jpeg):
    """Origen y destino en el mismo bucket: usa `Almacen.mover` (rename de blob,
    sin bajar/subir el contenido dos veces)."""
    bucket = _sembrar_almacen_gcs("bucket-move-mismo")
    contenido = b"contenido-de-prueba"
    bucket.objetos["in/a.JPG"] = contenido

    destino = utils_mod.safe_move("gs://bucket-move-mismo/in/a.JPG", "gs://bucket-move-mismo/out/a.JPG")

    assert destino == "gs://bucket-move-mismo/out/a.JPG"
    assert "in/a.JPG" not in bucket.objetos
    assert bucket.objetos["out/a.JPG"] == contenido


def test_safe_move_local_a_gcs_copia_y_borra_origen_solo_si_ok(tmp_path, make_dji_jpeg):
    """Origen local, destino `gs://…`: distinto almacén -> copia (lectura local +
    `publicar_en`) y solo entonces borra el origen."""
    origen = tmp_path / "a.JPG"
    make_dji_jpeg(str(origen))
    contenido = origen.read_bytes()

    bucket = _sembrar_almacen_gcs("bucket-move-mixto")

    destino = utils_mod.safe_move(str(origen), "gs://bucket-move-mixto/out/a.JPG")

    assert destino == "gs://bucket-move-mixto/out/a.JPG"
    assert not origen.exists()
    assert bucket.objetos["out/a.JPG"] == contenido


def test_safe_move_falla_al_publicar_no_borra_el_origen(tmp_path, make_dji_jpeg):
    """Si `publicar_en` (la escritura del destino) falla, el origen NO se borra:
    perder una imagen por un fallo a medio camino es inaceptable."""
    origen = tmp_path / "a.JPG"
    make_dji_jpeg(str(origen))

    bucket = _sembrar_almacen_gcs("bucket-move-falla")
    bucket.fallar_upload = True

    with pytest.raises(OSError):
        utils_mod.safe_move(str(origen), "gs://bucket-move-falla/out/a.JPG")

    assert origen.exists()
    assert "out/a.JPG" not in bucket.objetos


def test_safe_move_gcs_modo_obviar_destino_existente_no_mueve(tmp_path, make_dji_jpeg):
    bucket = _sembrar_almacen_gcs("bucket-move-obviar")
    bucket.objetos["in/a.JPG"] = b"origen"
    bucket.objetos["out/a.JPG"] = b"ya-estaba"

    resultado = utils_mod.safe_move(
        "gs://bucket-move-obviar/in/a.JPG", "gs://bucket-move-obviar/out/a.JPG",
        modo=utils_mod.MODO_OBVIAR,
    )

    assert resultado is None
    assert bucket.objetos["in/a.JPG"] == b"origen"  # el origen no se toca (opt-in via descartar_origen_si_existe)
    assert bucket.objetos["out/a.JPG"] == b"ya-estaba"


def test_safe_move_gcs_modo_unico_sufija_si_colisiona(tmp_path, make_dji_jpeg):
    bucket = _sembrar_almacen_gcs("bucket-move-unico")
    bucket.objetos["in/a.JPG"] = b"nuevo"
    bucket.objetos["out/a.JPG"] = b"ya-estaba"

    resultado = utils_mod.safe_move("gs://bucket-move-unico/in/a.JPG", "gs://bucket-move-unico/out/a.JPG")

    assert resultado == "gs://bucket-move-unico/out/a_1.JPG"
    assert bucket.objetos["out/a_1.JPG"] == b"nuevo"
    assert bucket.objetos["out/a.JPG"] == b"ya-estaba"
    assert "in/a.JPG" not in bucket.objetos


# --- sharding: pbs_del_destino / vuelos_del_destino / peso_de_ruta ----------

def _sembrar_arbol_post(tmp_path, make_dji_jpeg, bucket=None, prefijo=""):
    """PB1/PBV1 con RGB (1 imagen) y TERMICA (1 imagen), PB2 sin subcarpetas
    (imágenes directo en RGB/PB2). Igual en disco y, si se pasa `bucket`, en el
    bucket falso bajo `prefijo`."""
    raiz = tmp_path / "arbol"
    (raiz / "RGB" / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "TERMICA" / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "RGB" / "PB2").mkdir(parents=True)
    make_dji_jpeg(str(raiz / "RGB" / "PB1" / "PB1_V1" / "DJI_0001_W.JPG"))
    make_dji_jpeg(str(raiz / "TERMICA" / "PB1" / "PB1_V1" / "DJI_0001_T.JPG"))
    make_dji_jpeg(str(raiz / "RGB" / "PB2" / "DJI_0002_W.JPG"))

    if bucket is not None:
        for rel in ("RGB/PB1/PB1_V1/DJI_0001_W.JPG", "TERMICA/PB1/PB1_V1/DJI_0001_T.JPG",
                    "RGB/PB2/DJI_0002_W.JPG"):
            clave = f"{prefijo}/{rel}".strip("/")
            bucket.objetos[clave] = (raiz / rel).read_bytes()

    return raiz


def test_pbs_y_vuelos_del_destino_local_y_gcs_dan_igual(tmp_path, make_dji_jpeg):
    bucket = _sembrar_almacen_gcs("bucket-sharding-post")
    raiz_local = _sembrar_arbol_post(tmp_path, make_dji_jpeg, bucket=bucket, prefijo="dest")

    pbs_local = sharding.pbs_del_destino(str(raiz_local))
    pbs_gcs = sharding.pbs_del_destino("gs://bucket-sharding-post/dest")
    assert pbs_local == pbs_gcs == ["PB1", "PB2"]

    vuelos_local = sharding.vuelos_del_destino(str(raiz_local))
    vuelos_gcs = sharding.vuelos_del_destino("gs://bucket-sharding-post/dest")
    assert vuelos_local == vuelos_gcs == ["PB1/PB1_V1", "PB2"]


def test_peso_de_ruta_local_y_gcs_dan_igual(tmp_path, make_dji_jpeg):
    utils_obj = utils_mod.Utils(organizer_logger=None)
    contar = lambda ruta: utils_obj.contar_imagenes_or_tmc(ruta)

    bucket = _sembrar_almacen_gcs("bucket-sharding-peso")
    raiz_local = _sembrar_arbol_post(tmp_path, make_dji_jpeg, bucket=bucket, prefijo="dest")

    peso_local_pbv1 = sharding.peso_de_ruta(str(raiz_local), "PB1/PB1_V1", contar)
    peso_gcs_pbv1 = sharding.peso_de_ruta("gs://bucket-sharding-peso/dest", "PB1/PB1_V1", contar)
    assert peso_local_pbv1 == peso_gcs_pbv1 == 2  # 1 RGB + 1 TERMICA

    peso_local_pb2 = sharding.peso_de_ruta(str(raiz_local), "PB2", contar)
    peso_gcs_pb2 = sharding.peso_de_ruta("gs://bucket-sharding-peso/dest", "PB2", contar)
    assert peso_local_pb2 == peso_gcs_pb2 == 1


# --- BUG regresión: `AlmacenGCS.listar` con prefijo LITERAL (PB1 vs PB10) --

def _sembrar_arbol_pb1_pb10(tmp_path, make_dji_jpeg, bucket=None, prefijo=""):
    """PB1 y PB10 (con sus `_V1`), a propósito para que "PB1" sea prefijo DE
    TEXTO de "PB10". Antes del fix, `AlmacenGCS.listar` usaba `startswith`
    sin frontera de segmento y mezclaba ambos PBs."""
    raiz = tmp_path / "arbol_pb1_pb10"
    (raiz / "RGB" / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "RGB" / "PB10" / "PB10_V1").mkdir(parents=True)
    (raiz / "TERMICA" / "PB1" / "PB1_V1").mkdir(parents=True)
    (raiz / "TERMICA" / "PB10" / "PB10_V1").mkdir(parents=True)
    make_dji_jpeg(str(raiz / "RGB" / "PB1" / "PB1_V1" / "DJI_0001_W.JPG"))
    make_dji_jpeg(str(raiz / "RGB" / "PB10" / "PB10_V1" / "DJI_0010_W.JPG"))
    make_dji_jpeg(str(raiz / "TERMICA" / "PB1" / "PB1_V1" / "DJI_0001_T.JPG"))
    make_dji_jpeg(str(raiz / "TERMICA" / "PB10" / "PB10_V1" / "DJI_0010_T.JPG"))

    if bucket is not None:
        for rel in ("RGB/PB1/PB1_V1/DJI_0001_W.JPG", "RGB/PB10/PB10_V1/DJI_0010_W.JPG",
                    "TERMICA/PB1/PB1_V1/DJI_0001_T.JPG", "TERMICA/PB10/PB10_V1/DJI_0010_T.JPG"):
            clave = f"{prefijo}/{rel}".strip("/")
            bucket.objetos[clave] = (raiz / rel).read_bytes()

    return raiz


def test_pb1_pb10_no_se_mezclan_local_y_gcs_dan_igual(tmp_path, make_dji_jpeg):
    """Regresión del bug de prefijo literal: sin el fix, `pbs_del_destino`
    seguiría dando ["PB1", "PB10"] (viene de `startswith("PB")` sobre el
    nombre ya trinchado), pero `listar_subcarpetas`, `vuelos_del_destino`,
    `peso_de_ruta` y `contar_imagenes_or_tmc` se inventaban una subcarpeta
    "0" y mezclaban las imágenes de PB10 dentro de PB1 en el lado `gs://`."""
    utils_obj = utils_mod.Utils(organizer_logger=None)
    contar = lambda ruta: utils_obj.contar_imagenes_or_tmc(ruta)

    bucket = _sembrar_almacen_gcs("bucket-pb1-pb10")
    raiz_local = _sembrar_arbol_pb1_pb10(tmp_path, make_dji_jpeg, bucket=bucket, prefijo="dest")
    destino_gcs = "gs://bucket-pb1-pb10/dest"
    destino_local = str(raiz_local)

    pbs_local = sharding.pbs_del_destino(destino_local)
    pbs_gcs = sharding.pbs_del_destino(destino_gcs)
    assert pbs_local == pbs_gcs == ["PB1", "PB10"]

    vuelos_local = sharding.vuelos_del_destino(destino_local)
    vuelos_gcs = sharding.vuelos_del_destino(destino_gcs)
    assert vuelos_local == vuelos_gcs == ["PB1/PB1_V1", "PB10/PB10_V1"]

    for vuelo, esperado in (("PB1/PB1_V1", 2), ("PB10/PB10_V1", 2)):
        peso_local = sharding.peso_de_ruta(destino_local, vuelo, contar)
        peso_gcs = sharding.peso_de_ruta(destino_gcs, vuelo, contar)
        assert peso_local == peso_gcs == esperado

    sub_local = almacen_mod.listar_subcarpetas(f"{destino_local}/RGB")
    sub_gcs = almacen_mod.listar_subcarpetas(f"{destino_gcs}/RGB")
    assert sub_local == sub_gcs == ["PB1", "PB10"]

    ficheros_local = almacen_mod.listar_ficheros(f"{destino_local}/RGB/PB1/PB1_V1")
    ficheros_gcs = almacen_mod.listar_ficheros(f"{destino_gcs}/RGB/PB1/PB1_V1")
    assert ficheros_local == ficheros_gcs == ["DJI_0001_W.JPG"]

    total_local = utils_obj.contar_imagenes_or_tmc(f"{destino_local}/RGB/PB1")
    total_gcs = utils_obj.contar_imagenes_or_tmc(f"{destino_gcs}/RGB/PB1")
    assert total_local == total_gcs == 1


# --- BUG regresión: `Almacen.mover` con origen==destino no debe borrar -----

def test_safe_move_gcs_origen_igual_destino_sobrescribir_no_borra(tmp_path, make_dji_jpeg):
    """Sin el fix, `AlmacenGCS.mover` hacía copy_blob(x, x) + delete(x): el
    objeto desaparecía. `safe_move` en modo `sobrescribir` con origen==destino
    debe dejar el objeto intacto."""
    bucket = _sembrar_almacen_gcs("bucket-move-idem-sobrescribir")
    bucket.objetos["out/a.JPG"] = b"contenido"

    resultado = utils_mod.safe_move(
        "gs://bucket-move-idem-sobrescribir/out/a.JPG",
        "gs://bucket-move-idem-sobrescribir/out/a.JPG",
        modo=utils_mod.MODO_SOBRESCRIBIR,
    )

    assert resultado == "gs://bucket-move-idem-sobrescribir/out/a.JPG"
    assert bucket.objetos["out/a.JPG"] == b"contenido"


def test_safe_move_gcs_origen_igual_destino_unico_no_pierde_el_contenido(tmp_path, make_dji_jpeg):
    """Modo `unico`: como el destino "ya existe" (es el propio origen),
    `unique_dest` sufija a `a_1.JPG` -- un movimiento real, con claves
    distintas, así que el contenido debe seguir accesible en algún sitio (no
    perderse), aunque ya no en la ruta original."""
    bucket = _sembrar_almacen_gcs("bucket-move-idem-unico")
    bucket.objetos["out/a.JPG"] = b"contenido"

    resultado = utils_mod.safe_move(
        "gs://bucket-move-idem-unico/out/a.JPG",
        "gs://bucket-move-idem-unico/out/a.JPG",
        modo=utils_mod.MODO_UNICO,
    )

    assert resultado == "gs://bucket-move-idem-unico/out/a_1.JPG"
    assert bucket.objetos["out/a_1.JPG"] == b"contenido"
    assert sum(1 for v in bucket.objetos.values() if v == b"contenido") == 1


def test_almacen_gcs_mover_mismo_origen_y_destino_es_noop():
    """Unidad directa sobre `AlmacenGCS.mover`, sin pasar por `safe_move`."""
    bucket = _sembrar_almacen_gcs("bucket-mover-directo")
    bucket.objetos["a.JPG"] = b"contenido"
    almacen = almacen_mod._ALMACENES["gs://bucket-mover-directo"]

    almacen.mover("a.JPG", "a.JPG")

    assert bucket.objetos["a.JPG"] == b"contenido"


# --- BUG regresión: `AlmacenGCS.borrar` debe traducir NotFound del SDK -----

NotFound = type("NotFound", (Exception,), {})
"""Doble mínimo de `google.api_core.exceptions.NotFound`: mismo nombre de
clase (`AlmacenGCS.borrar` lo reconoce por nombre cuando el SDK real no está
instalado, que es el caso de este entorno de tests)."""


class _BlobBorrarInexistente:
    """Simula el blob real de GCS: borrar algo que no existe lanza
    `google.api_core.exceptions.NotFound`, NUNCA `FileNotFoundError`."""

    def delete(self) -> None:
        raise NotFound("objeto inexistente")


class _BucketBorrarInexistente:
    def blob(self, nombre: str):
        return _BlobBorrarInexistente()


class _ClienteBorrarInexistente:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, nombre: str):
        return self._bucket


def test_almacen_gcs_borrar_inexistente_se_comporta_como_local(tmp_path):
    """Local: `os.remove` de algo inexistente da `FileNotFoundError`, y
    `_borrar_en_almacen` lo traga (varios shards pueden borrar lo mismo).
    `AlmacenGCS.borrar` debe dar el mismo contrato: `FileNotFoundError`, no
    la excepción cruda del SDK (`NotFound`), para que `_borrar_en_almacen`
    la trague igual en el lado `gs://`."""
    ruta_local = tmp_path / "no_existe.JPG"
    utils_mod._borrar_en_almacen(str(ruta_local))  # no debe lanzar

    bucket = _BucketBorrarInexistente()
    cliente = _ClienteBorrarInexistente(bucket)
    almacen = AlmacenGCS("bucket-borrar-inexistente", prefijo_raiz="", cliente=cliente)

    with pytest.raises(FileNotFoundError):
        almacen.borrar("no_existe.JPG")

    almacen_mod._ALMACENES["gs://bucket-borrar-inexistente"] = almacen
    utils_mod._borrar_en_almacen("gs://bucket-borrar-inexistente/no_existe.JPG")  # no debe lanzar
