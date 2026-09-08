"""Tests del apply de térmicas (`atom_core.apply.aplicar_termicas`).

Una térmica no es CPU del intérprete: es espera a dos procesos externos
(`dji_irp`/`libdirp.so` para el TIFF, `exiftool` para el EXIF). Lo que estos
tests sujetan:

1. Que el TIFF se gire con EL MISMO ángulo que su JPG hermano de RGB — el
   invariante nº1 del proyecto: `fila["angulo_giro"]` sale del mismo
   diccionario por (pb, vuelo) que usa el índice para RGB (`indice.py`), y
   `aplicar_termicas` tiene que traducirlo a PIL con el MISMO mapeo que usa
   `apply._transpose_para_angulo` para RGB. Si divergiera, el TIFF y su JPG
   quedarían con orientaciones distintas.
2. Que los metadatos se copien con exiftool EN LOTES, no imagen a imagen
   (~5x más lento si se regresa al `subprocess.run` por imagen).
3. Que una térmica sin su JPG de origen no tumbe el resto del vuelo.
4. Que el TIFF se escriba de forma atómica: un fallo a mitad no puede dejar
   un `.parcial` en la carpeta de entrega.
5. Que un TIFF sin sus metadatos copiados NUNCA quede `hecho`: es el
   invariante "TIFF con TODOS sus metadatos", y como `exiftool -stay_open`
   no da granularidad por imagen dentro de un lote, un fallo del lote tiene
   que marcar fallidas TODAS las filas de ese lote (nunca darlas por buenas
   a ciegas).
"""
import datetime as dt
import filecmp
import os
import types

import pytest
from PIL import Image as PILImage

import pipeline as pipeline_real
from atom_core import apply
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import SplitImagesConfig


class _SignalFalsa:
    """Doble de `progress_callback`/`progress_bar`/`progress_summarize`: solo
    apunta lo que se le emite, sin depender de Qt."""

    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *args, **kwargs):
        self.mensajes.append(valor)


class _PipelineDePrueba:
    """Doble de una instancia `SplitImages` (`pipeline.py`): registra las
    llamadas de conversión y de EXIF por lotes en vez de invocar
    `dji_irp`/`libdirp.so` y `exiftool` de verdad.

    `convert_dji_image_to_tif` SÍ escribe un TIFF real y mínimo (2x4 px, no
    cuadrado, para poder distinguir orientación por tamaño si hiciera
    falta): `aplicar_termicas` necesita algo real que abrir para el giro
    posterior. Si el JPG de origen no existe, se comporta como el conversor
    real ante ese caso (devuelve `None`, fallo silencioso ya logueado antes
    de llegar aquí).
    """

    def __init__(self, lotes_que_fallan=frozenset()):
        self.llamadas_convert = []
        self.llamadas_exif = []
        self._lotes_que_fallan = set(lotes_que_fallan)

    def convert_dji_image_to_tif(self, input_folder, output_folder, image_name,
                                  exiftool_exe, dji_utility, progress_callback,
                                  progress_bar, **kwargs):
        ruta_origen = os.path.join(input_folder, image_name)
        self.llamadas_convert.append({"ruta_origen": ruta_origen, **kwargs})
        if not os.path.exists(ruta_origen):
            return None
        os.makedirs(output_folder, exist_ok=True)
        tiff_path = os.path.join(output_folder, os.path.splitext(image_name)[0] + ".tiff")
        PILImage.new("F", (4, 2)).save(tiff_path, format="TIFF")
        return (ruta_origen, tiff_path)

    def _run_exif_batch_local(self, pairs, exiftool_exe, progress_callback=None):
        self.llamadas_exif.append(list(pairs))
        for src, _dst in pairs:
            if src in self._lotes_que_fallan:
                raise RuntimeError(f"exiftool batch devolvió error (simulado) para {src}")


def _cfg(**overrides):
    """`SplitImagesConfig` mínima, calcada de `test_apply_rgb._cfg`: solo los
    campos `convert_to_tif_*` importan a estos tests."""
    base = dict(
        input_folder="/origen", output_folder="/destino",
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="/estadillo.csv", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=85, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=True, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=True, gen_thumbnails=True, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5.0,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5.0,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=True, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    return SplitImagesConfig(**base)


def _fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=0, pb="PB1", vuelo="V01"):
    return FilaManifiesto(
        ruta_origen=str(origen),
        tipo="TERMICA",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb=pb,
        vuelo=vuelo,
        nombre_nuevo="20260501_100000_" + str(origen).rsplit("/", 1)[-1],
        angulo_giro=angulo_giro,
        pct_recorte=None,
        comprime=False,
        ruta_salida_original=str(salida_jpg),
        ruta_salida_crop=None,
        ruta_salida_tiff=str(salida_tiff),
        unassigned=False,
    )


def _manifiesto_con(tmp_path, filas):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas(filas)
    return manifiesto


def test_el_tiff_usa_el_angulo_del_manifiesto_igual_que_su_jpg(tmp_path, make_dji_jpeg, monkeypatch):
    """Invariante nº1: la térmica y la RGB del mismo vuelo se giran con el
    mismo ángulo. `fila["angulo_giro"]` sale del mismo diccionario
    (pb, vuelo) -> grados que usa el índice para las RGB; aquí se comprueba
    que `aplicar_termicas` traduce ese ángulo a PIL con el MISMO mapeo que
    ya usa `apply._transpose_para_angulo` para RGB (90 -> ROTATE_270).

    Desde que el `*_T.JPG` publicado también se gira, el TIFF radiométrico y
    su JPG salen con el MISMO transpose: se esperan DOS aplicaciones
    idénticas (con `cfg.gen_thumbnails=True`, el caso por defecto). El caso
    `--sin-rotacion` lo cubre `test_jpg_termico_no_se_gira_con_sin_rotacion`."""
    origen = tmp_path / "origen" / "DJI_0001_T.JPG"
    origen.parent.mkdir(parents=True)
    make_dji_jpeg(str(origen))

    salida_jpg = tmp_path / "salida" / "DJI_0001_T.JPG"
    salida_tiff = tmp_path / "salida" / "DJI_0001_T.tif"
    manifiesto = _manifiesto_con(
        tmp_path, [_fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=90)]
    )

    transposes_aplicados = []
    transpose_real = PILImage.Image.transpose

    def _transpose_que_apunta(self, method):
        transposes_aplicados.append(method)
        return transpose_real(self, method)

    monkeypatch.setattr(PILImage.Image, "transpose", _transpose_que_apunta)

    doble = _PipelineDePrueba()
    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 0}
    esperado = apply._transpose_para_angulo(90, pipeline_real)
    assert transposes_aplicados == [esperado, esperado]
    assert salida_tiff.exists()
    assert salida_jpg.exists()
    manifiesto.cerrar()


def test_los_metadatos_se_copian_en_lotes(tmp_path, make_dji_jpeg):
    """Con 5 térmicas y `tamano_lote_exif=2`, `_run_exif_batch_local` se
    llama 3 veces (2+2+1), nunca 5: previene volver al `exiftool` por
    imagen, que es ~5x más lento (reinicia el intérprete Perl cada vez)."""
    filas = []
    for indice in range(5):
        origen = tmp_path / "origen" / f"DJI_{indice:04d}_T.JPG"
        origen.parent.mkdir(parents=True, exist_ok=True)
        make_dji_jpeg(str(origen))
        salida_jpg = tmp_path / "salida" / f"DJI_{indice:04d}_T.JPG"
        salida_tiff = tmp_path / "salida" / f"DJI_{indice:04d}_T.tif"
        filas.append(_fila_termica(origen, salida_jpg, salida_tiff))

    manifiesto = _manifiesto_con(tmp_path, filas)
    doble = _PipelineDePrueba()

    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
        tamano_lote_exif=2,
    )

    assert resultado == {"hecho": 5, "fallido": 0}
    assert len(doble.llamadas_exif) == 3
    assert [len(lote) for lote in doble.llamadas_exif] == [2, 2, 1]
    manifiesto.cerrar()


def test_una_termica_sin_su_jpg_de_origen_queda_fallida(tmp_path, make_dji_jpeg):
    """Si el JPG de origen desapareció entre el índice y el apply, esa fila
    no puede tumbar el resto del vuelo: se marca fallida con motivo y el
    apply sigue con las demás."""
    origen_ok = tmp_path / "origen" / "DJI_0001_T.JPG"
    origen_ok.parent.mkdir(parents=True)
    make_dji_jpeg(str(origen_ok))
    origen_roto = tmp_path / "origen" / "NO_EXISTE_T.JPG"

    manifiesto = _manifiesto_con(tmp_path, [
        _fila_termica(origen_ok, tmp_path / "salida" / "DJI_0001_T.JPG",
                      tmp_path / "salida" / "DJI_0001_T.tif"),
        _fila_termica(origen_roto, tmp_path / "salida" / "NO_EXISTE_T.JPG",
                      tmp_path / "salida" / "NO_EXISTE_T.tif"),
    ])
    doble = _PipelineDePrueba()

    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 1}
    filas = manifiesto.todas()
    fallida = next(f for f in filas if f["ruta_origen"] == str(origen_roto))
    hecha = next(f for f in filas if f["ruta_origen"] == str(origen_ok))
    assert fallida["estado"] == "fallido"
    assert fallida["motivo_fallo"]
    assert hecha["estado"] == "hecho"
    assert hecha["motivo_fallo"] is None
    manifiesto.cerrar()


def test_el_tiff_se_escribe_de_forma_atomica(tmp_path, make_dji_jpeg, monkeypatch):
    """Un fallo a mitad del guardado girado (disco lleno, TIFF corrupto al
    escribir) no puede dejar un `.parcial` ni un TIFF a medias en la
    carpeta de entrega: el siguiente run los confundiría con una salida
    válida."""
    origen = tmp_path / "origen" / "DJI_0001_T.JPG"
    origen.parent.mkdir(parents=True)
    make_dji_jpeg(str(origen))

    carpeta_salida = tmp_path / "salida"
    salida_jpg = carpeta_salida / "DJI_0001_T.JPG"
    salida_tiff = carpeta_salida / "DJI_0001_T.tif"
    manifiesto = _manifiesto_con(
        tmp_path, [_fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=90)]
    )

    def _save_que_revienta(self, fp, *args, **kwargs):
        raise OSError("disco lleno (simulado)")

    monkeypatch.setattr(PILImage.Image, "save", _save_que_revienta)

    doble = _PipelineDePrueba()
    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 0, "fallido": 1}
    assert not salida_tiff.exists()
    assert list(carpeta_salida.glob("*.parcial*")) == []
    manifiesto.cerrar()


def test_falta_de_metadatos_marca_la_fila_como_fallida(tmp_path, make_dji_jpeg):
    """Invariante "TIFF con TODOS sus metadatos": si `exiftool` devuelve
    error para el lote de una térmica, esa fila NO puede quedar `hecho`
    aunque el TIFF ya se haya escrito bien. Con `tamano_lote_exif=1` cada
    fila es su propio lote, así que el fallo de una no arrastra a la otra."""
    origen_falla = tmp_path / "origen" / "DJI_0001_T.JPG"
    origen_falla.parent.mkdir(parents=True)
    make_dji_jpeg(str(origen_falla))
    origen_ok = tmp_path / "origen" / "DJI_0002_T.JPG"
    make_dji_jpeg(str(origen_ok))

    manifiesto = _manifiesto_con(tmp_path, [
        _fila_termica(origen_falla, tmp_path / "salida" / "DJI_0001_T.JPG",
                      tmp_path / "salida" / "DJI_0001_T.tif"),
        _fila_termica(origen_ok, tmp_path / "salida" / "DJI_0002_T.JPG",
                      tmp_path / "salida" / "DJI_0002_T.tif"),
    ])
    doble = _PipelineDePrueba(lotes_que_fallan={str(origen_falla)})

    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
        tamano_lote_exif=1,
    )

    assert resultado == {"hecho": 1, "fallido": 1}
    filas = manifiesto.todas()
    fallida = next(f for f in filas if f["ruta_origen"] == str(origen_falla))
    hecha = next(f for f in filas if f["ruta_origen"] == str(origen_ok))
    assert fallida["estado"] == "fallido"
    assert fallida["motivo_fallo"]
    assert hecha["estado"] == "hecho"
    # El TIFF sí se escribió (la conversión fue OK, lo que falló fue exiftool),
    # pero eso no basta para que la fila quede 'hecho'.
    assert (tmp_path / "salida" / "DJI_0001_T.tif").exists()
    manifiesto.cerrar()


def _jpg_termico_apaisado(path):
    """JPEG apaisado 640x512 (medida real de una térmica DJI), no el 64x48 de
    `make_dji_jpeg`: aquí importa poder distinguir orientación por tamaño."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    PILImage.new("RGB", (640, 512), color=(80, 90, 100)).save(path, format="JPEG", quality=95)


def test_jpg_termico_se_gira_con_gen_thumbnails_activo(tmp_path):
    """Con `gen_thumbnails=True` (switch maestro de rotación activo), el
    `*_T.JPG` publicado sale girado con el ángulo del manifiesto: ancho y
    alto quedan intercambiados respecto al origen apaisado (640x512 -> 512x640)."""
    origen = tmp_path / "origen" / "DJI_0001_T.JPG"
    _jpg_termico_apaisado(str(origen))

    salida_jpg = tmp_path / "salida" / "DJI_0001_T.JPG"
    salida_tiff = tmp_path / "salida" / "DJI_0001_T.tif"
    manifiesto = _manifiesto_con(
        tmp_path, [_fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=90)]
    )

    doble = _PipelineDePrueba()
    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(gen_thumbnails=True), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 0}
    with PILImage.open(origen) as img_origen:
        ancho_origen, alto_origen = img_origen.size
    with PILImage.open(salida_jpg) as img_salida:
        ancho_salida, alto_salida = img_salida.size
    assert (ancho_salida, alto_salida) == (alto_origen, ancho_origen)
    manifiesto.cerrar()


def test_jpg_termico_con_exif_real_conserva_gps_fecha_y_yaw_tras_girar(
    tmp_path, make_dji_jpeg, logger
):
    """Con un `*_T.JPG` que trae EXIF real (GPS + `DateTimeOriginal` + XMP
    `GimbalYawDegree`, vía `make_dji_jpeg`), tras girar la copia de destino
    (`gen_thumbnails=True`) el EXIF debe seguir presente y sus campos clave
    deben valer LO MISMO que antes del giro: es justo el dato que luego se
    consulta sobre estas fotos (`apply.py:568-570`). El original de entrada
    no debe tocarse."""
    import exif as exif_management

    origen = tmp_path / "origen" / "DJI_0001_T.JPG"
    os.makedirs(origen.parent, exist_ok=True)
    latitud, longitud = 40.4168, -3.7038
    fecha = dt.datetime(2024, 6, 1, 10, 30, 0)
    make_dji_jpeg(str(origen), lat=latitud, lon=longitud, dt_val=fecha, gimbal_yaw=37.5)
    contenido_origen_antes = origen.read_bytes()

    exif_obj = exif_management.GeneralInformationFromImage(logger)
    meta_location_obj = exif_management.MetaLocation(logger)
    _, lat_antes, lon_antes, _alt_antes = meta_location_obj.leerLatitudLongitudAltitud_exif_DJI(
        str(origen), _SignalFalsa()
    )
    fecha_antes = exif_obj.get_timestamp_from_image(str(origen))
    yaw_antes, _pitch_antes = exif_obj.get_gimbal_yaw_pitch(str(origen))

    salida_jpg = tmp_path / "salida" / "DJI_0001_T.JPG"
    salida_tiff = tmp_path / "salida" / "DJI_0001_T.tif"
    manifiesto = _manifiesto_con(
        tmp_path, [_fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=90)]
    )

    doble = _PipelineDePrueba()
    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(gen_thumbnails=True), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )
    assert resultado == {"hecho": 1, "fallido": 0}

    with PILImage.open(salida_jpg) as img_salida:
        ancho_salida, alto_salida = img_salida.size
    assert alto_salida > ancho_salida, "el JPG térmico girado debe quedar vertical"

    _, lat_despues, lon_despues, _alt_despues = meta_location_obj.leerLatitudLongitudAltitud_exif_DJI(
        str(salida_jpg), _SignalFalsa()
    )
    fecha_despues = exif_obj.get_timestamp_from_image(str(salida_jpg))

    assert lat_despues is not None and lon_despues is not None, (
        "el EXIF con el GPS debe seguir presente tras el giro"
    )
    assert (lat_despues, lon_despues) == (lat_antes, lon_antes), (
        "el GPS no debe cambiar al girar la copia de destino"
    )
    assert fecha_despues == fecha_antes, (
        "la fecha (DateTimeOriginal) no debe cambiar al girar la copia de destino"
    )

    yaw_despues, _pitch_despues = exif_obj.get_gimbal_yaw_pitch(str(salida_jpg))
    assert yaw_despues == yaw_antes, (
        f"el GimbalYawDegree del XMP debía conservarse tras el giro "
        f"(antes={yaw_antes!r}, despues={yaw_despues!r})"
    )

    assert origen.read_bytes() == contenido_origen_antes, (
        "el JPG térmico de ORIGEN nunca debe modificarse: el giro solo toca la copia de destino"
    )
    manifiesto.cerrar()


def test_jpg_termico_no_se_gira_con_sin_rotacion(tmp_path):
    """Con `gen_thumbnails=False` (`--sin-rotacion`), el `*_T.JPG` NO se gira
    aunque el manifiesto traiga ángulo: girarlo destruiría el payload
    radiométrico del R-JPEG (incidente CLARE `wpv52`). El JPG publicado debe
    ser IDÉNTICO byte a byte al de origen, mientras que el TIFF sí sale
    girado (el switch maestro solo protege al JPG, no al TIFF)."""
    origen = tmp_path / "origen" / "DJI_0001_T.JPG"
    _jpg_termico_apaisado(str(origen))

    salida_jpg = tmp_path / "salida" / "DJI_0001_T.JPG"
    salida_tiff = tmp_path / "salida" / "DJI_0001_T.tif"
    manifiesto = _manifiesto_con(
        tmp_path, [_fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=90)]
    )

    doble = _PipelineDePrueba()
    resultado = apply.aplicar_termicas(
        manifiesto, _cfg(gen_thumbnails=False), doble, _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 0}
    assert filecmp.cmp(str(origen), str(salida_jpg), shallow=False)

    # El TIFF, en cambio, sí sale girado: el staging del doble es 4x2 (F, 2px),
    # así que tras rotar 90 queda 2x4.
    with PILImage.open(salida_tiff) as img_tiff:
        assert img_tiff.size == (2, 4)
    manifiesto.cerrar()


def test_el_origen_nunca_se_modifica(tmp_path):
    """Ni con `gen_thumbnails=True` ni con `False` el fichero de ORIGEN se
    toca: el giro se aplica siempre sobre la copia de destino, nunca en
    sitio (a diferencia del motor viejo, que sí giraba el origen)."""
    for gen_thumbnails in (True, False):
        origen = tmp_path / f"origen_{gen_thumbnails}" / "DJI_0001_T.JPG"
        _jpg_termico_apaisado(str(origen))
        contenido_original = origen.read_bytes()

        salida_jpg = tmp_path / f"salida_{gen_thumbnails}" / "DJI_0001_T.JPG"
        salida_tiff = tmp_path / f"salida_{gen_thumbnails}" / "DJI_0001_T.tif"
        carpeta_manifiesto = tmp_path / f"m_{gen_thumbnails}"
        carpeta_manifiesto.mkdir()
        manifiesto = _manifiesto_con(
            carpeta_manifiesto,
            [_fila_termica(origen, salida_jpg, salida_tiff, angulo_giro=90)],
        )

        doble = _PipelineDePrueba()
        resultado = apply.aplicar_termicas(
            manifiesto, _cfg(gen_thumbnails=gen_thumbnails), doble,
            _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
        )

        assert resultado == {"hecho": 1, "fallido": 0}
        assert origen.read_bytes() == contenido_original
        manifiesto.cerrar()
