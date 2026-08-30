"""F4c-1: staging local por imagen en `SplitImages.convert_dji_image_to_tif`.

Cubre que con `input_folder`/`output_folder` en `gs://…` la conversión hace
staging LOCAL de la imagen (descarga -> conversor DJI -> tiff/gris/colormap ->
publicación) y borra el temporal siempre, mientras que con rutas 100% locales
el comportamiento sigue siendo BYTE A BYTE el de antes (sin temporales ni
copias extra).

El conversor DJI real (`_dji_measure_to_raw_linux`) se mockea: escribe un
`.raw` sintético (float32) del tamaño exacto del JPG de prueba, así el test no
depende del SDK de DJI ni de binarios externos. `defer_exif=True` en todos los
casos para no depender de `exiftool` (el batch de EXIF es la fase siguiente de
la migración, fuera de alcance de F4c-1).

Mismo estilo de doble en memoria del backend GCS que
`tests/test_post_listados_almacen.py`/`tests/test_struct_primitivas_almacen.py`.
NO se modifica `BucketFalso.list_blobs` (su `startswith` literal replica a
propósito el comportamiento de GCS real). `_NotFound` imita por NOMBRE a
`google.api_core.exceptions.NotFound`: el SDK real nunca lanza
`FileNotFoundError` al descargar un blob inexistente.
"""
import hashlib
import io
import os
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


# --- Doble en memoria del backend GCS (idéntico a test_post_listados_almacen.py) ---

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


# --- Fixtures del caso de prueba: un JPG 4x2 (8 píxeles) + su .raw sintético ---

_ANCHO, _ALTO = 4, 2
_VALORES_RAW = [10.0 + i for i in range(_ANCHO * _ALTO)]  # 8 "temperaturas" distintas


def _jpg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (_ANCHO, _ALTO), color=(128, 64, 32)).save(buf, format="JPEG")
    return buf.getvalue()


def _raw_bytes() -> bytes:
    import struct
    return b"".join(struct.pack("<f", v) for v in _VALORES_RAW)


@pytest.fixture(autouse=True)
def _mock_dji_measure(monkeypatch):
    """Sustituye el conversor DJI real por uno que escribe un `.raw` sintético
    del tamaño exacto del JPG de prueba (8 float32 = 4x2), para no depender
    del SDK de DJI ni de binarios externos."""
    def _fake_measure(self, image_path, raw_path, humidity, emissivity, lib_dir):
        with open(raw_path, "wb") as fh:
            fh.write(_raw_bytes())

    monkeypatch.setattr(pipeline.SplitImages, "_dji_measure_to_raw_linux", _fake_measure)
    monkeypatch.setattr(pipeline, "_is_windows", lambda: False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CONVERT_KWARGS = dict(
    exiftool_exe="exiftool",
    dji_utility="/no/existe/dji_irp",
    emissivity=0.9,
    humidity=50.0,
    auto_temp=True,  # sin recorte de umbrales: arr == array_to_normalize, determinista
    up_threshold_temperature=999,
    low_threshold_temperature=-999,
    rotate_90=False,
    rotate_minus_90=False,
    auto_rotate=False,  # así degree_de_giro devuelve 0 sin tocar el CSV de criterio
    just_atom_selection=False,
    generate_gray_scale_images=True,
    generate_colormap_images=True,
    defer_exif=True,  # no depende de exiftool; el batch diferido es la fase siguiente
)


def _run_local(tmp_path, logger, image_name="DJI_0001_T.JPG"):
    raiz = tmp_path / "local_root"
    raiz.mkdir()
    (raiz / "Escala_de_grises").mkdir()
    (raiz / "Color_gradiente").mkdir()
    (raiz / image_name).write_bytes(_jpg_bytes())

    obj = pipeline.SplitImages(logger)
    progress = _noop_progress()
    resultado = obj.convert_dji_image_to_tif(
        str(raiz), str(raiz), image_name, progress_callback=progress, progress_bar=progress,
        **CONVERT_KWARGS,
    )
    return obj, raiz, resultado


def _run_gcs(logger, bucket_nombre, image_name="DJI_0001_T.JPG"):
    bucket = _sembrar_almacen_gcs(bucket_nombre)
    bucket.objetos[f"in/{image_name}"] = _jpg_bytes()

    obj = pipeline.SplitImages(logger)
    progress = _noop_progress()
    resultado = obj.convert_dji_image_to_tif(
        f"gs://{bucket_nombre}/in", f"gs://{bucket_nombre}/out", image_name,
        progress_callback=progress, progress_bar=progress,
        **CONVERT_KWARGS,
    )
    return obj, bucket, resultado


# --- 1) Paridad byte a byte local vs gs:// -----------------------------------

def test_paridad_local_y_gcs_mismo_contenido(tmp_path, logger):
    """El TIFF/gris/colormap finales (CON el EXIF ya aplicado) deben ser
    byte a byte idénticos entre el camino local y el gs://, aunque desde
    F4c-3 apliquen el EXIF por vías distintas: local lo sigue difiriendo
    (aquí se cierra a mano con `_run_exif_batch`, como haría el llamador real
    tras el pool) y gs:// lo aplica ya dentro de `convert_dji_image_to_tif`,
    antes de publicar."""
    image_name = "DJI_0001_T.JPG"

    obj_local, raiz_local, pending_local = _run_local(tmp_path, logger, image_name)
    assert obj_local.error_splitting_images == 0
    obj_local._run_exif_batch(
        [pending_local], exiftool_exe="exiftool", progress_callback=_noop_progress())

    obj_gcs, bucket, resultado_gcs = _run_gcs(logger, "bucket-paridad-tif", image_name)
    assert obj_gcs.error_splitting_images == 0
    assert resultado_gcs is None  # con gs:// nunca se difiere: no hay par que devolver

    tiff_local = (raiz_local / "DJI_0001_T.tiff").read_bytes()
    gris_local = (raiz_local / "Escala_de_grises" / image_name).read_bytes()
    color_local = (raiz_local / "Color_gradiente" / image_name).read_bytes()

    tiff_gcs = bucket.objetos["out/DJI_0001_T.tiff"]
    gris_gcs = bucket.objetos["out/Escala_de_grises/DJI_0001_T.JPG"]
    color_gcs = bucket.objetos["out/Color_gradiente/DJI_0001_T.JPG"]

    assert _sha256(tiff_local) == _sha256(tiff_gcs)
    assert _sha256(gris_local) == _sha256(gris_gcs)
    assert _sha256(color_local) == _sha256(color_gcs)


# --- 2) Nombres publicados en gs:// son los REALES, no el temporal ----------

def test_nombres_publicados_gcs_son_los_reales(logger):
    image_name = "DJI_0002_T.JPG"
    obj, bucket, _ = _run_gcs(logger, "bucket-nombres-reales", image_name)

    assert obj.error_splitting_images == 0
    claves = set(bucket.objetos)
    assert "out/DJI_0002_T.tiff" in claves
    assert "out/Escala_de_grises/DJI_0002_T.JPG" in claves
    assert "out/Color_gradiente/DJI_0002_T.JPG" in claves
    # Ninguna clave debe llevar un nombre aleatorio de directorio temporal.
    for clave in claves:
        assert "tmp" not in clave.lower() or clave.startswith("in/")


# --- 3) El .raw intermedio NO se publica -------------------------------------

def test_raw_no_se_publica_en_destino(logger):
    obj, bucket, _ = _run_gcs(logger, "bucket-raw-no-publica")
    assert obj.error_splitting_images == 0
    assert not any(clave.endswith(".raw") for clave in bucket.objetos)


# --- 4) El staging se borra también si el procesado lanza una excepción -----

def test_staging_se_borra_en_excepcion(logger, monkeypatch):
    rutas_creadas = []
    real_mkdtemp = pipeline.tempfile.mkdtemp

    def _mkdtemp_espia(*args, **kwargs):
        ruta = real_mkdtemp(*args, **kwargs)
        rutas_creadas.append(ruta)
        return ruta

    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", _mkdtemp_espia)

    # Un .raw con un nº de floats que NO casa con el JPG ni con ninguna
    # resolución conocida ni con la relación de aspecto -> `resolucion_desde_raw`
    # lanza ValueError, que NO está capturado dentro de la función: debe
    # propagar, y el `finally` debe limpiar igualmente el staging.
    def _raw_roto(self, image_path, raw_path, humidity, emissivity, lib_dir):
        with open(raw_path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00")  # 9 bytes: ni múltiplo limpio

    monkeypatch.setattr(pipeline.SplitImages, "_dji_measure_to_raw_linux", _raw_roto)

    bucket = _sembrar_almacen_gcs("bucket-staging-excepcion")
    image_name = "DJI_0003_T.JPG"
    bucket.objetos[f"in/{image_name}"] = _jpg_bytes()

    obj = pipeline.SplitImages(logger)
    progress = _noop_progress()

    with pytest.raises(ValueError):
        obj.convert_dji_image_to_tif(
            "gs://bucket-staging-excepcion/in", "gs://bucket-staging-excepcion/out",
            image_name, progress_callback=progress, progress_bar=progress,
            **CONVERT_KWARGS,
        )

    assert len(rutas_creadas) == 1, "Debía crearse exactamente un staging_dir."
    assert not os.path.exists(rutas_creadas[0]), "El staging_dir no se limpió tras la excepción."


# --- 5) Local: sin temporales ni copias extra --------------------------------

def test_local_no_crea_ningun_temporal(tmp_path, logger, monkeypatch):
    def _mkdtemp_prohibido(*args, **kwargs):
        raise AssertionError("No debía crearse ningún directorio de staging en local.")

    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", _mkdtemp_prohibido)

    obj, raiz_local, resultado = _run_local(tmp_path, logger)

    assert obj.error_splitting_images == 0
    assert (raiz_local / "DJI_0001_T.tiff").exists()
    # defer_exif=True: local devuelve el par (src_jpg, dst_tiff) tal cual antes.
    assert resultado == (
        str(raiz_local / "DJI_0001_T.JPG"),
        str(raiz_local / "DJI_0001_T.tiff"),
    )


# --- F4c-2: batch EXIF diferido URI-aware ------------------------------------
#
# Mock de exiftool: en vez del binario real, sustituye `subprocess.run` (a
# nivel de `pipeline`) por uno que simula la edición in-place -- añade un
# marcador de bytes al fichero `dst` que recibe como argumento -- para poder
# verificar sin depender del binario que el EXIF "se aplicó".

_MARCADOR_EXIF = b"__EXIF_APLICADO__"


def _localizar_dst_en_args(args):
    """Extrae la ruta de destino (`dst`) de una invocación de exiftool, tanto
    en forma de lista (POSIX, `_run_exif_batch` por-imagen) como de string
    (Windows, no aplica en estos tests salvo por completitud del parseo)."""
    if isinstance(args, str):
        # '"exe" -tagsfromfile "src" "dst" -overwrite_original_in_place'
        partes = [p for p in args.split('"') if p.strip() and p.strip() != "-overwrite_original_in_place"]
        return partes[-1]
    return args[3]  # [exe, "-tagsfromfile", src, dst, "-overwrite_original_in_place"]


@pytest.fixture
def _mock_exiftool_por_imagen(monkeypatch):
    """Mockea la rama por-imagen (`gs://`) de `_run_exif_batch`: cada
    invocación de `subprocess.run(["exiftool", "-tagsfromfile", src, dst, ...])`
    escribe el marcador al final de `dst` (simula la edición in-place real)."""
    llamadas = []

    def _fake_run(args, *a, **k):
        llamadas.append(args)
        dst = _localizar_dst_en_args(args)
        with open(dst, "ab") as fh:
            fh.write(_MARCADOR_EXIF)
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pipeline.subprocess, "run", _fake_run)
    return llamadas


@pytest.fixture
def _mock_exiftool_batch_local(monkeypatch):
    """Mockea la rama local (`-stay_open` + argfile) de `_run_exif_batch`:
    UNA sola invocación que, por cada par `(src, dst)` que aparece en el
    argfile, escribe el marcador en su `dst`."""
    llamadas = []

    def _fake_run(args, *a, **k):
        llamadas.append(args)
        # args = [exe, "-stay_open", "True", "-@", argfile]
        argfile = args[-1]
        with open(argfile, "r", encoding="utf-8") as f:
            lineas = [l.rstrip("\n") for l in f]
        i = 0
        while i < len(lineas):
            if lineas[i] == "-tagsfromfile":
                dst = lineas[i + 3]  # -tagsfromfile\nsrc\n-overwrite_original_in_place\ndst
                with open(dst, "ab") as fh:
                    fh.write(_MARCADOR_EXIF)
                i += 5
            else:
                i += 1
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pipeline.subprocess, "run", _fake_run)
    return llamadas


def test_exif_aplicado_antes_de_publicar_en_gcs(logger, _mock_exiftool_por_imagen):
    """F4c-3: con destino gs:// el EXIF NUNCA se difiere -- se fuerza
    `defer_exif = False` dentro de `convert_dji_image_to_tif` en cuanto hay
    staging, así que exiftool corre sobre el TIFF de STAGING LOCAL antes de
    publicarlo. El TIFF que llega al bucket ya lleva el EXIF aplicado, y la
    función devuelve `None` (no hay par que diferir para gs://)."""
    bucket_nombre = "bucket-exif-diferido"
    image_name = "DJI_0010_T.JPG"
    obj, bucket, resultado = _run_gcs(logger, bucket_nombre, image_name)
    assert obj.error_splitting_images == 0

    assert resultado is None
    assert bucket.objetos["out/DJI_0010_T.tiff"].endswith(_MARCADOR_EXIF)
    assert len(_mock_exiftool_por_imagen) == 1


def test_par_devuelto_son_rutas_reales_gcs_y_existen(logger):
    """F4c-3: con destino gs:// `convert_dji_image_to_tif` no difiere nada, así
    que NO hay par que devolver -- retorna `None`. Lo que sí debe cumplirse es
    que el TIFF ya quede publicado, con su EXIF, en la URI REAL del almacén
    (no en una ruta de staging ya borrada) en el momento en que la función
    retorna."""
    bucket_nombre = "bucket-rutas-reales"
    image_name = "DJI_0011_T.JPG"
    obj, bucket, resultado = _run_gcs(logger, bucket_nombre, image_name)

    assert resultado is None
    src = f"gs://{bucket_nombre}/in/{image_name}"
    dst = f"gs://{bucket_nombre}/out/DJI_0011_T.tiff"
    assert almacen_mod.existe_ruta(src)
    assert almacen_mod.existe_ruta(dst)


def test_fallo_en_una_imagen_no_impide_exif_de_las_demas(logger, monkeypatch):
    """Un fallo de exiftool en UNA imagen del lote gs:// no debe impedir que
    las demás reciban su EXIF: se registra el fallo y se sigue con el resto.

    Con el fix F4c-3, `convert_dji_image_to_tif` ya no alimenta
    `_run_exif_batch` con pares gs:// (nunca difiere con staging). Pero la
    rama gs:// de `_run_exif_batch` sigue viva -- es API pública para otros
    llamadores -- así que se prueba DIRECTAMENTE, con pares construidos a
    mano sobre TIFFs ya publicados (simulando el estado que dejaría un
    llamador ajeno a `convert_dji_image_to_tif`)."""
    bucket_nombre = "bucket-fallo-parcial"
    bucket = _sembrar_almacen_gcs(bucket_nombre)
    bucket.objetos["in/DJI_0020_T.JPG"] = _jpg_bytes()
    bucket.objetos["in/DJI_0021_T.JPG"] = _jpg_bytes()
    bucket.objetos["out/DJI_0020_T.tiff"] = b"tiff-sin-exif-0020"
    bucket.objetos["out/DJI_0021_T.tiff"] = b"tiff-sin-exif-0021"

    pending1 = (f"gs://{bucket_nombre}/in/DJI_0020_T.JPG", f"gs://{bucket_nombre}/out/DJI_0020_T.tiff")
    pending2 = (f"gs://{bucket_nombre}/in/DJI_0021_T.JPG", f"gs://{bucket_nombre}/out/DJI_0021_T.tiff")

    llamadas = []

    def _fake_run_con_fallo(args, *a, **k):
        llamadas.append(args)
        dst = _localizar_dst_en_args(args)
        # `editar_en_sitio` descarga a un temporal con nombre ALEATORIO (no
        # conserva el basename de la imagen), así que no se puede distinguir
        # el par por el nombre de `dst`: se falla en la PRIMERA invocación
        # (0020, procesado antes por ir primero en la lista pasada a
        # `_run_exif_batch`) por orden de llamada.
        if len(llamadas) == 1:
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")
        with open(dst, "ab") as fh:
            fh.write(_MARCADOR_EXIF)
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pipeline.subprocess, "run", _fake_run_con_fallo)

    obj = pipeline.SplitImages(logger)
    # No debe lanzar, aunque el primer par falle.
    obj._run_exif_batch(
        [pending1, pending2], exiftool_exe="exiftool", progress_callback=_noop_progress())

    assert not bucket.objetos["out/DJI_0020_T.tiff"].endswith(_MARCADOR_EXIF)
    assert bucket.objetos["out/DJI_0021_T.tiff"].endswith(_MARCADOR_EXIF)
    assert len(llamadas) == 2


def test_local_una_sola_invocacion_exiftool_para_n_pares(
    tmp_path, logger, _mock_exiftool_batch_local
):
    """Camino 100% local: N pares deferidos siguen resolviéndose con UNA sola
    invocación de exiftool -stay_open (no una por imagen)."""
    obj1, raiz1, r1 = _run_local(tmp_path, logger, "DJI_0030_T.JPG")
    # Misma carpeta de vuelo que arriba: se escribe una segunda imagen a mano
    # (`_run_local` haría un `mkdir()` que ya existe) y se convierte con el
    # mismo objeto, tal y como hace `convert_dji_images_to_tif` con un lote.
    (raiz1 / "DJI_0031_T.JPG").write_bytes(_jpg_bytes())
    r2 = obj1.convert_dji_image_to_tif(
        str(raiz1), str(raiz1), "DJI_0031_T.JPG",
        progress_callback=_noop_progress(), progress_bar=_noop_progress(),
        **CONVERT_KWARGS,
    )
    assert obj1.error_splitting_images == 0

    obj1._run_exif_batch(
        [r1, r2], exiftool_exe="exiftool", progress_callback=_noop_progress())

    assert len(_mock_exiftool_batch_local) == 1
    assert (raiz1 / "DJI_0030_T.tiff").read_bytes().endswith(_MARCADOR_EXIF)
    assert (raiz1 / "DJI_0031_T.tiff").read_bytes().endswith(_MARCADOR_EXIF)


# --- F4c-3: staging no queda huérfano si falla la descarga del origen -------

def test_staging_no_queda_huerfano_si_falla_la_descarga(logger, monkeypatch):
    """Si la descarga del JPG de origen falla (p.ej. la imagen no existe en el
    bucket -> `abrir_para_lectura` lanza `FileNotFoundError`) con destino
    gs://, el directorio de staging NO debe quedar huérfano en /tmp: el
    try/except BaseException que envuelve el mkdtemp/makedirs/descarga debe
    borrarlo antes de relanzar la excepción."""
    rutas_creadas = []
    real_mkdtemp = pipeline.tempfile.mkdtemp

    def _mkdtemp_espia(*args, **kwargs):
        ruta = real_mkdtemp(*args, **kwargs)
        rutas_creadas.append(ruta)
        return ruta

    monkeypatch.setattr(pipeline.tempfile, "mkdtemp", _mkdtemp_espia)

    bucket_nombre = "bucket-descarga-falla"
    _sembrar_almacen_gcs(bucket_nombre)  # bucket vacío: la imagen NO existe

    obj = pipeline.SplitImages(logger)
    progress = _noop_progress()

    with pytest.raises(FileNotFoundError):
        obj.convert_dji_image_to_tif(
            f"gs://{bucket_nombre}/in", f"gs://{bucket_nombre}/out",
            "DJI_9999_T.JPG", progress_callback=progress, progress_bar=progress,
            **CONVERT_KWARGS,
        )

    assert len(rutas_creadas) == 1, "Debía crearse exactamente un staging_dir."
    assert not os.path.exists(rutas_creadas[0]), (
        "El staging_dir no se limpió tras el fallo de descarga del origen.")
