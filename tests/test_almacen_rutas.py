"""Capa de rutas URI-aware sobre `Almacen` (`atom_core.almacen`, sección final).

Fija el contrato de `es_uri_gcs`/`abrir_almacen`/`unir`/`abrir_para_lectura`/
`publicar_en`/`listar_ficheros`/`listar_subcarpetas`/`existe_ruta`: con rutas
normales deben comportarse EXACTAMENTE como el `os.path`/`shutil` de siempre;
con `gs://…` despachan al backend `AlmacenGCS`.

Para el camino GCS se reutiliza el mismo estilo de doble en memoria que
`tests/test_almacen_gcs.py` (imita solo la parte de la API de
`google.cloud.storage` que usa `AlmacenGCS`), pero en vez de construir
`AlmacenGCS` a través de `abrir_almacen` (que solo sabe crearlo sin `cliente`,
y por tanto exigiría el SDK real), se siembra directamente la caché
`_ALMACENES` con la instancia de prueba: es el mismo mecanismo que en
producción evita reconstruir el cliente por cada fichero dentro de un proceso
del `ProcessPoolExecutor`.
"""
import os
from pathlib import Path

import pytest

import atom_core.almacen as almacen_mod
from atom_core.almacen import (
    abrir_almacen,
    abrir_para_lectura,
    es_uri_gcs,
    existe_ruta,
    listar_ficheros,
    listar_subcarpetas,
    nombre_de,
    publicar_en,
    tamano_de,
    unir,
)
from atom_core.almacen_gcs import AlmacenGCS


@pytest.fixture(autouse=True)
def _cache_limpia():
    """La caché de almacenes es a nivel de módulo (a propósito, ver docstring
    de `_ALMACENES`); hay que vaciarla entre tests para que no se filtren
    instancias de un test a otro."""
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria para el backend GCS, mismo estilo que test_almacen_gcs.py

class BlobFalso:
    def __init__(self, bucket: "BucketFalso", nombre: str):
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
        # Metadatos "reales" en este doble: el tamaño ya está disponible en
        # `self.bucket.objetos` sin necesidad de bajarse el contenido.
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}

    def blob(self, nombre: str) -> BlobFalso:
        return BlobFalso(self, nombre)

    def list_blobs(self, prefix: str = ""):
        for nombre in self.objetos:
            if nombre.startswith(prefix):
                yield BlobFalso(self, nombre)

    def copy_blob(self, blob_origen: BlobFalso, bucket_destino: "BucketFalso", nombre_destino: str):
        bucket_destino.objetos[nombre_destino] = self.objetos[blob_origen.name]
        return BlobFalso(bucket_destino, nombre_destino)


class ClienteFalso:
    def __init__(self, bucket: BucketFalso):
        self._bucket = bucket

    def bucket(self, nombre: str) -> BucketFalso:
        return self._bucket


def _sembrar_almacen_gcs(bucket_nombre: str) -> BucketFalso:
    """Crea un `AlmacenGCS` de prueba (sin SDK real) y lo mete en la caché de
    `abrir_almacen` bajo la clave que usaría `gs://<bucket_nombre>/...`, para
    que las funciones públicas lo encuentren sin intentar construir uno real."""
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


# --- es_uri_gcs --------------------------------------------------------------

def test_es_uri_gcs_true_para_esquema_gs():
    assert es_uri_gcs("gs://mi-bucket/carpeta") is True


def test_es_uri_gcs_case_insensitive():
    assert es_uri_gcs("GS://mi-bucket/carpeta") is True


def test_es_uri_gcs_false_para_ruta_local():
    assert es_uri_gcs("/home/atom/carpeta") is False
    assert es_uri_gcs("C:\\datos\\carpeta") is False


# --- abrir_almacen -----------------------------------------------------------

def test_abrir_almacen_local_devuelve_almacenlocal_sin_prefijo(tmp_path):
    almacen, prefijo = abrir_almacen(str(tmp_path))
    assert isinstance(almacen, almacen_mod.AlmacenLocal)
    assert almacen.raiz == tmp_path
    assert prefijo == ""


def test_abrir_almacen_local_cachea_misma_instancia(tmp_path):
    almacen1, _ = abrir_almacen(str(tmp_path))
    almacen2, _ = abrir_almacen(str(tmp_path))
    assert almacen1 is almacen2


def test_abrir_almacen_gcs_parsea_bucket_y_prefijo():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    del bucket  # solo hacía falta para sembrar la caché
    almacen, prefijo = abrir_almacen("gs://mi-bucket/pre/fijo")
    assert isinstance(almacen, AlmacenGCS)
    assert prefijo == "pre/fijo"


def test_abrir_almacen_gcs_sin_prefijo():
    _sembrar_almacen_gcs("mi-bucket")
    almacen, prefijo = abrir_almacen("gs://mi-bucket")
    assert isinstance(almacen, AlmacenGCS)
    assert prefijo == ""


def test_abrir_almacen_gcs_cachea_misma_instancia_por_bucket():
    _sembrar_almacen_gcs("mi-bucket")
    almacen1, _ = abrir_almacen("gs://mi-bucket/a")
    almacen2, _ = abrir_almacen("gs://mi-bucket/b/c")
    assert almacen1 is almacen2


# --- unir ---------------------------------------------------------------------

def test_unir_local_equivale_a_os_path_join(tmp_path):
    assert unir(str(tmp_path), "sub", "fichero.txt") == os.path.join(str(tmp_path), "sub", "fichero.txt")


def test_unir_gcs_concatena_con_slash_sin_dobles_ni_backslash():
    resultado = unir("gs://mi-bucket/raiz", "sub", "fichero.txt")
    assert resultado == "gs://mi-bucket/raiz/sub/fichero.txt"
    assert "\\" not in resultado
    assert "//" not in resultado.replace("gs://", "")


def test_unir_gcs_sin_partes_extra_devuelve_la_ruta():
    assert unir("gs://mi-bucket/raiz") == "gs://mi-bucket/raiz"


def test_unir_gcs_ignora_barras_sobrantes_en_las_partes():
    resultado = unir("gs://mi-bucket/raiz/", "/sub/", "/fichero.txt")
    assert resultado == "gs://mi-bucket/raiz/sub/fichero.txt"


# --- abrir_para_lectura --------------------------------------------------------

def test_abrir_para_lectura_local_devuelve_la_ruta_real_sin_copiar(tmp_path):
    fichero = tmp_path / "a.txt"
    fichero.write_text("contenido")
    with abrir_para_lectura(str(fichero)) as ruta:
        assert ruta == fichero
        assert ruta.read_text() == "contenido"
    # Sigue existiendo tal cual: no se ha movido/borrado nada (coste cero).
    assert fichero.exists()


def test_abrir_para_lectura_gcs_descarga_a_temporal_y_lo_limpia():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["carpeta/a.txt"] = b"contenido remoto"
    ruta_capturada = None
    with abrir_para_lectura("gs://mi-bucket/carpeta/a.txt") as ruta:
        ruta_capturada = ruta
        assert ruta.read_bytes() == b"contenido remoto"
    assert not ruta_capturada.exists()


# --- publicar_en ----------------------------------------------------------------

def test_publicar_en_local_copia_el_contenido_a_fichero_destino(tmp_path):
    origen = tmp_path / "origen.txt"
    origen.write_text("hola")
    destino = tmp_path / "destino.txt"
    publicar_en(origen, str(destino))
    assert destino.read_text() == "hola"
    # No destructivo: el origen sigue existiendo (es copia, no move).
    assert origen.exists()


def test_publicar_en_local_respeta_destino_directorio(tmp_path):
    origen = tmp_path / "origen.txt"
    origen.write_text("hola")
    carpeta_destino = tmp_path / "carpeta"
    carpeta_destino.mkdir()
    publicar_en(origen, str(carpeta_destino))
    assert (carpeta_destino / "origen.txt").read_text() == "hola"


def test_publicar_en_gcs_sube_al_prefijo_correcto(tmp_path):
    bucket = _sembrar_almacen_gcs("mi-bucket")
    origen = tmp_path / "origen.txt"
    origen.write_text("hola")
    publicar_en(origen, "gs://mi-bucket/carpeta/destino.txt")
    assert bucket.objetos["carpeta/destino.txt"] == b"hola"


def test_publicar_en_admite_origen_en_gcs(tmp_path):
    """El origen ya no es siempre local: desde 3790 F5 el Job recibe el
    estadillo como `gs://…` y `gen_folder_struct` lo publica en ESTADILLOS/ del
    destino. Pasarlo por `Path()` colapsaba `gs://` a `gs:/` y acababa
    abriéndolo como fichero local -> `FileNotFoundError: 'gs:/bucket/…'`, que es
    exactamente lo que tumbó el shard 0 de la etapa struct en producción."""
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["SUBIDAS/ESTADILLOS/e.csv"] = b"pb;vuelo\n1;1\n"
    publicar_en("gs://mi-bucket/SUBIDAS/ESTADILLOS/e.csv",
                "gs://mi-bucket/SALIDA/ESTADILLOS/e.csv")
    assert bucket.objetos["SALIDA/ESTADILLOS/e.csv"] == b"pb;vuelo\n1;1\n"
    # No destructivo: publicar es copiar, el original sigue en su sitio.
    assert "SUBIDAS/ESTADILLOS/e.csv" in bucket.objetos


def test_publicar_en_admite_origen_en_gcs_con_destino_local(tmp_path):
    """El caso simétrico: rollback a destino LOCAL (`/gcs/...` por gcsfuse, o
    el escritorio) con el estadillo todavía en `gs://`. Mismo colapso de la
    doble barra si el origen no se resuelve antes de copiar."""
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["SUBIDAS/e.csv"] = b"contenido"
    destino = tmp_path / "e.csv"
    publicar_en("gs://mi-bucket/SUBIDAS/e.csv", str(destino))
    assert destino.read_bytes() == b"contenido"


def test_publicar_en_origen_gcs_a_carpeta_local_conserva_el_nombre(tmp_path):
    """Con destino CARPETA, el nombre lo pone el origen. Al resolver una URI se
    pasa por un temporal `tmpXXXXXX.csv`, así que el nombre final hay que
    fijarlo desde la URI o el fichero acabaría llamándose como el temporal."""
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["SUBIDAS/e.csv"] = b"contenido"
    carpeta = tmp_path / "carpeta"
    carpeta.mkdir()
    publicar_en("gs://mi-bucket/SUBIDAS/e.csv", str(carpeta))
    assert (carpeta / "e.csv").read_bytes() == b"contenido"


# --- listar_ficheros / listar_subcarpetas ---------------------------------------

def test_listar_ficheros_local_no_recursivo_y_ordenado(tmp_path):
    # Subcarpeta aislada de "Logs-subidas/", que el fixture autouse de
    # conftest.py escribe en tmp_path (ver tests/test_almacen.py::_raiz).
    raiz = tmp_path / "almacen"
    raiz.mkdir()
    (raiz / "b.txt").write_text("b")
    (raiz / "a.txt").write_text("a")
    (raiz / "sub").mkdir()
    (raiz / "sub" / "hondo.txt").write_text("hondo")
    assert listar_ficheros(str(raiz)) == ["a.txt", "b.txt"]


def test_listar_subcarpetas_local_no_recursivo_y_ordenado(tmp_path):
    raiz = tmp_path / "almacen"
    raiz.mkdir()
    (raiz / "z").mkdir()
    (raiz / "a").mkdir()
    (raiz / "fichero.txt").write_text("x")
    assert listar_subcarpetas(str(raiz)) == ["a", "z"]


def test_listar_ficheros_gcs_no_recursivo():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["raiz/a.txt"] = b"a"
    bucket.objetos["raiz/b.txt"] = b"b"
    bucket.objetos["raiz/sub/hondo.txt"] = b"hondo"
    assert listar_ficheros("gs://mi-bucket/raiz") == ["a.txt", "b.txt"]


def test_listar_subcarpetas_gcs_no_recursivo_deduplicado():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["raiz/a.txt"] = b"a"
    bucket.objetos["raiz/sub/b.txt"] = b"b"
    bucket.objetos["raiz/sub/hondo/c.txt"] = b"c"
    bucket.objetos["raiz/otra/d.txt"] = b"d"
    assert listar_subcarpetas("gs://mi-bucket/raiz") == ["otra", "sub"]


def test_listar_ficheros_gcs_raiz_del_bucket_sin_prefijo():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["a.txt"] = b"a"
    bucket.objetos["sub/b.txt"] = b"b"
    assert listar_ficheros("gs://mi-bucket") == ["a.txt"]
    assert listar_subcarpetas("gs://mi-bucket") == ["sub"]


# --- existe_ruta -----------------------------------------------------------------

def test_existe_ruta_local_true_false(tmp_path):
    fichero = tmp_path / "a.txt"
    fichero.write_text("a")
    assert existe_ruta(str(fichero)) is True
    assert existe_ruta(str(tmp_path / "no_existe.txt")) is False


def test_existe_ruta_gcs_fichero_exacto():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["raiz/a.txt"] = b"a"
    assert existe_ruta("gs://mi-bucket/raiz/a.txt") is True
    assert existe_ruta("gs://mi-bucket/raiz/no_existe.txt") is False


def test_existe_ruta_gcs_carpeta_sin_objeto_propio():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["raiz/sub/a.txt"] = b"a"
    # "raiz/sub" no es un objeto en sí, pero sí hay algo colgando de ese
    # prefijo: debe contar como que existe, igual que un directorio local.
    assert existe_ruta("gs://mi-bucket/raiz/sub") is True
    assert existe_ruta("gs://mi-bucket/raiz/no_existe") is False


# --- tamano_de ---------------------------------------------------------------

def test_tamano_de_local_igual_que_stat(tmp_path):
    fichero = tmp_path / "a.txt"
    fichero.write_bytes(b"doce_bytes.")
    assert tamano_de(str(fichero)) == fichero.stat().st_size


def test_tamano_de_gcs_lee_metadatos_sin_descargar():
    bucket = _sembrar_almacen_gcs("mi-bucket")
    bucket.objetos["raiz/a.txt"] = b"contenido de sobra"
    assert tamano_de("gs://mi-bucket/raiz/a.txt") == len(b"contenido de sobra")


# --- nombre_de -----------------------------------------------------------------
#
# Último segmento de una ruta, URI-aware: `Path(...).name` revienta con un
# `str` (`AttributeError`, el bug real de `organize_cli.py`), y
# `os.path.basename` deja `""` cuando la URI acaba en `/`. No requiere
# ningún doble de GCS: es lógica de string pura, sin tocar `abrir_almacen`.

def test_nombre_de_gcs_con_prefijo_multinivel():
    assert nombre_de("gs://b/x/y") == "y"


def test_nombre_de_gcs_con_barra_final_recorta_antes_de_partir():
    assert nombre_de("gs://b/x/") == "x"


def test_nombre_de_gcs_solo_bucket_devuelve_el_nombre_del_bucket():
    assert nombre_de("gs://b") == "b"


def test_nombre_de_local():
    assert nombre_de("/home/a/b") == "b"


def test_nombre_de_local_con_barra_final():
    assert nombre_de("/home/a/b/") == "b"
