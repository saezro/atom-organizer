"""Regresión de dos fallos silenciosos con destino `gs://…` en `phases.py`:

- BUG C: el bloque `post` de `split_images` (reparto por PB, `mis_pbs`)
  llamaba a `os.path.isdir` crudo sobre rutas construidas con `os.path.join`
  para decidir si un PB del reparto existe. Sobre `gs://…` eso da SIEMPRE
  `False` (no hay directorios reales que comprobar), así que con reparto
  activo los PBs de esta tarea se descartaban como "inexistentes" sin error:
  `total_images_number` salía mal y esos PB quedaban sin procesar en
  silencio.
- BUG D: `_resolve_dron_selector` recorría `termica_folder` con `os.walk`
  para sacar una muestra térmica y detectar el modelo de dron por EXIF.
  Sobre `gs://…` `os.walk` no itera nada (no lanza), así que `sample` se
  quedaba en `None` y el aviso de "no se encontró imagen térmica de muestra"
  salía siempre, aunque hubiera imágenes de sobra en el bucket.

Reutiliza el doble en memoria del backend GCS (mismo patrón que
`tests/test_post_dji_tif_almacen.py`/`tests/test_almacen_gcs.py`) y el host
de prueba de `tests/test_etapas_pipeline.py` para BUG C.
"""
import types

import pytest

import atom_core.almacen as almacen_mod
from atom_core.phases import PipelinePhasesMixin, _primera_imagen_almacen
from tests.test_etapas_pipeline import _HostDePrueba, _SignalFalsa, _cfg


@pytest.fixture(autouse=True)
def _cache_limpia():
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS (idéntico a los otros tests de almacen) ---
# AVISO: `list_blobs` replica A PROPÓSITO el `startswith` LITERAL de GCS real:
# no se debe "arreglar" para cortar por "/".

_NotFound = type("NotFound", (Exception,), {})


class BlobFalso:
    def __init__(self, bucket, nombre):
        self.bucket = bucket
        self.name = nombre

    def exists(self) -> bool:
        return self.name in self.bucket.objetos

    def upload_from_filename(self, ruta_local: str) -> None:
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


# --- BUG C: reparto por PB en el bloque `post` de `split_images` ------------

def test_pb_existente_en_gcs_se_reconoce_con_reparto_activo():
    """Con destino `gs://…` y reparto activo (`shard_count > 1`), el único PB
    que le toca a esta tarea SÍ existe en el bucket falso: antes del fix,
    `os.path.isdir` crudo lo descartaba siempre (False sobre `gs://…`) y
    `iterate_folders_for_rgb_cropping` nunca se llamaba. Tras el fix, se
    llama con la ruta real del PB."""
    bucket_nombre = "bucket-bug-c"
    bucket = _sembrar_almacen_gcs(bucket_nombre)
    # Un único vuelo -> con 2 shards y peso 0 en ambos, `repartir` lo asigna
    # determinísticamente al shard 0 (empate roto por índice de shard).
    bucket.objetos["dest/RGB/PB1/PB1_V1/a.jpg"] = b"img"

    host = _HostDePrueba(etapa="post", shard_index=0, shard_count=2)
    cfg = _cfg(
        output_folder=f"gs://{bucket_nombre}/dest",
        organize_images=False,
        cropping_rgb=True,
        gen_meta_location=False,
        gen_thumbnails=False,
        convert_to_tif=False,
    )
    host.split_images(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    rutas_convertidas = [
        args[0] for metodo, args, _kw in host.detalle
        if metodo == "iterate_folders_for_rgb_cropping"
    ]
    assert rutas_convertidas == [f"gs://{bucket_nombre}/dest/RGB/PB1/PB1_V1"], (
        "El PB existente en el bucket debía reconocerse y procesarse; "
        f"detalle completo: {host.detalle}"
    )


def test_pb_existente_en_gcs_se_cuenta_en_meta_location():
    """Mismo bug, tercer sitio (línea ~616): el total de `meta_location_obj`
    con reparto activo debía incluir el PB real del bucket, no descartarlo."""
    bucket_nombre = "bucket-bug-c-meta"
    bucket = _sembrar_almacen_gcs(bucket_nombre)
    bucket.objetos["dest/RGB/PB1/PB1_V1/a.jpg"] = b"img"

    host = _HostDePrueba(etapa="post", shard_index=0, shard_count=2)
    # `contar_imagenes_or_tmc` (falso) siempre devuelve 0: lo que se prueba
    # aquí no es la cifra, sino que la carpeta SE LLEGA A CONTAR (se llama
    # con su ruta) en vez de quedar filtrada por el `isdir` roto.
    cfg = _cfg(
        output_folder=f"gs://{bucket_nombre}/dest",
        organize_images=False,
        cropping_rgb=False,
        gen_meta_location=True,
        gen_thumbnails=False,
        convert_to_tif=False,
    )
    host.split_images(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    carpetas_contadas = [
        folder for folder, _filtro in host.utils_obj.filtros_recibidos
    ]
    assert f"gs://{bucket_nombre}/dest/RGB/PB1/PB1_V1" in carpetas_contadas, (
        "El PB existente debía entrar en el cómputo de meta_location; "
        f"carpetas contadas: {carpetas_contadas}"
    )


# --- BUG D: `_resolve_dron_selector` sobre `gs://…` --------------------------

class _HostResolveDron(PipelinePhasesMixin):
    """Host mínimo para `_resolve_dron_selector`: solo necesita
    `meta_location_obj.exif_management_obj.get_model` y `organizer_logger_obj`
    (usado por el GUARD final de binarios, que en este entorno de tests
    normalmente falla -- se captura fuera)."""

    def __init__(self, get_model):
        self.meta_location_obj = types.SimpleNamespace(
            exif_management_obj=types.SimpleNamespace(get_model=get_model))


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def test_primera_imagen_almacen_encuentra_muestra_en_gcs_y_corta_pronto():
    """`_primera_imagen_almacen` (el helper que sustituye a `os.walk` para
    `gs://…`) encuentra la primera imagen bajo el prefijo, sin necesidad de
    listar el subárbol entero."""
    bucket_nombre = "bucket-bug-d"
    bucket = _sembrar_almacen_gcs(bucket_nombre)
    bucket.objetos["dest/TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = b"jpg"
    bucket.objetos["dest/TERMICA/PB1/PB1_V1/DJI_0002_T.JPG"] = b"jpg"

    encontrada = _primera_imagen_almacen(f"gs://{bucket_nombre}/dest/TERMICA")
    assert encontrada == f"gs://{bucket_nombre}/dest/TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"


def test_resolve_dron_selector_gcs_encuentra_muestra_y_pasa_ruta_local(tmp_path):
    """Con `termica_folder` en `gs://…` y una imagen real en el bucket falso,
    `_resolve_dron_selector` debe encontrar la muestra (antes del fix,
    `os.walk` no iteraba nada sobre `gs://…` y `sample` quedaba `None`) y la
    ruta que le pasa al lector de EXIF debe ser LOCAL (descargada) y existir,
    no la URI `gs://…` cruda."""
    bucket_nombre = "bucket-bug-d-resolve"
    bucket = _sembrar_almacen_gcs(bucket_nombre)
    bucket.objetos["dest/TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"] = b"contenido-jpg"

    import os as _os

    rutas_recibidas = []
    existia_en_el_momento_de_la_llamada = []

    def _get_model_falso(ruta, progress_callback):
        rutas_recibidas.append(ruta)
        # Se comprueba AQUÍ, dentro del `with abrir_para_lectura`: el
        # temporal se borra al salir del bloque, así que fuera de esta
        # llamada ya no existiría aunque el fix fuese correcto.
        existia_en_el_momento_de_la_llamada.append(_os.path.exists(ruta))
        return "Matrice 4T"

    host = _HostResolveDron(_get_model_falso)
    # El GUARD de binarios DJI corre tras resolver el modelo; no es parte de
    # este bug y en este entorno de test no hay SDK instalado, así que se
    # espera que aborte con ValueError DESPUÉS de haber resuelto la muestra.
    with pytest.raises(ValueError):
        host._resolve_dron_selector(
            "", f"gs://{bucket_nombre}/dest/TERMICA", _noop_progress())

    assert len(rutas_recibidas) == 1, "no se encontró ninguna muestra térmica"
    ruta_pasada_a_exif = rutas_recibidas[0]
    assert not str(ruta_pasada_a_exif).startswith("gs://"), (
        "la ruta pasada al lector de EXIF debe ser LOCAL, no la URI gs:// cruda"
    )
    assert existia_en_el_momento_de_la_llamada == [True]
