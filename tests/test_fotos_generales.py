"""PB "GENERALES" en el estadillo: fotos de contexto de la planta, no de un
vuelo con línea real. Van a `FOTOS_GENERALES`, HERMANA de TERMICA/RGB/
RGB_Extra en la raíz de `output_folder`, PLANAS (sin subcarpeta PB/vuelo),
sin girar (no hay consenso de ángulo posible sin vuelo real).

Formato confirmado en bucket (`gs://<bucket>/<PLANTA>/INSPECCIONES/
TERMICA_MODULOS/2026/FOTOS_GENERALES/`): carpeta plana, ficheros con su
nombre original (renombrados o no según config), mezcla RGB/TERMICA/CROP.

Antes de esta corrección: `_construir_fila` montaba `PB{pb}` con
`pb="GENERALES"` -> carpeta `PBGENERALES` colgando de TERMICA/RGB, y el
consenso de ángulo por vuelo giraba esas imágenes como si fueran de un vuelo
real (bug reportado: 6 imágenes GENERALES giradas 90° en una planta).

Dobles a mano, mismo estilo que `tests/test_indice_organizado.py` (nunca
`unittest.mock`).
"""
import datetime as dt
import os

from atom_core import estadillo as estadillo_mod
from atom_core.indice import construir_indice
from atom_core.manifiesto import Manifiesto
from utils import SplitImagesConfig


class _Signal:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor):
        self.mensajes.append(valor)


class _PipelineDePrueba:
    def __init__(self, ventanas: dict, pct_por_modelo: dict | None = None):
        self._ventanas = ventanas
        self.percentage_by_models = pct_por_modelo or {}

    def ventana_horaria_vuelo(self, fecha, horaInicio, horaFinal,
                              margen_segundos, desfase_horas, desfase_minutos):
        return self._ventanas[(fecha, horaInicio, horaFinal)]

    def nombre_destino(self, image, input_folder, rename, mismatch_hours,
                       mismatch_minutes, ruta_local=None, timestamp=None):
        if not rename:
            return ""
        return f"RENOMBRADA_{image}"

    def get_percentage_by_model(self, model, percentage_cropping_dict):
        return percentage_cropping_dict[model.strip().upper()]

    def read_auto_rotate_degree(self, input_folder, progress_callback):
        return 0


class _ExifDePrueba:
    def __init__(self, timestamps=None, modelos=None, yaws=None, gps=None):
        self._timestamps = timestamps or {}
        self._modelos = modelos or {}
        self._yaws = yaws or {}
        self._gps = gps or {}

    def get_timestamp_from_image(self, pathImagen):
        return self._timestamps.get(pathImagen)

    def get_model(self, filename, progress_callback):
        return self._modelos.get(filename, "M3T")

    def get_gimbal_yaw_pitch(self, filename, bloque_xmp=None):
        return [str(self._yaws.get(filename, 0.0)), "0"]

    def leerLatitudLongitudAltitud_exif_DJI(self, pathImagen, progress_callback):
        return self._gps.get(pathImagen)


def _escribir_estadillo(ruta, filas):
    """`filas`: tuplas (pb, vuelo, fecha, hora_inicio, hora_final)."""
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n")
        for pb, vuelo, fecha, hora_inicio, hora_final in filas:
            fh.write(f"{pb};{vuelo};{fecha};{hora_inicio};{hora_final}\n")


def _cfg(tmp_path, **overrides):
    base = dict(
        input_folder=str(tmp_path / "origen"),
        output_folder=str(tmp_path / "destino"),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="_D",
        estad=str(tmp_path / "estadillo.csv"),
        choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=False,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=False, cropping_mode_auto=True, crop_percentage="50",
        gen_meta_location=False, gen_thumbnails=False, seconds_range=5.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5,
        gen_thumbnails_max_error=50, gen_thumbnails_subs_to_angle=5,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=False, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    os.makedirs(base["input_folder"], exist_ok=True)
    return SplitImagesConfig(**base)


def _crear_imagen(carpeta, nombre):
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "wb") as fh:
        fh.write(b"")
    return ruta


def _manifiesto(tmp_path, nombre="manifiesto.db"):
    m = Manifiesto(tmp_path / nombre)
    m.crear_esquema()
    return m


def test_generales_van_a_fotos_generales_plana_sin_valueerror(tmp_path):
    """PB "GENERALES" no revienta (antes: `int(pb)` en el motor viejo) y su
    imagen acaba en `<output>/FOTOS_GENERALES/<fichero>`, sin `PBGENERALES`
    ni subcarpeta de vuelo."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("GENERALES", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 0
    esperado = os.path.join(cfg.output_folder, "FOTOS_GENERALES", "DJI_0001_D.JPG")
    assert fila["ruta_salida_original"] == esperado
    assert "PBGENERALES" not in fila["ruta_salida_original"]
    manifiesto.cerrar()


def test_generales_no_se_gira_aunque_el_yaw_pida_rotacion(tmp_path):
    """Un yaw que en un vuelo normal daría consenso 90° NO debe girar una
    imagen GENERALES: no hay línea de vuelo real de la que sacar consenso."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("GENERALES", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    # yaw=90 caería en la banda de rotación de 90º si esto fuera un vuelo real.
    exif = _ExifDePrueba(
        timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)},
        yaws={ruta: 90.0},
    )
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["angulo_giro"] == 0
    manifiesto.cerrar()


def test_colision_de_nombre_en_generales_no_sobrescribe(tmp_path):
    """Dos imágenes GENERALES de vuelos distintos con el MISMO nombre
    (tarjetas SD que reinician numeración) no pueden compartir ruta de
    salida: la segunda debe llevar un sufijo determinista, nunca pisar a la
    primera."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("GENERALES", "1", "2024:06:01", "10:00:00", "10:10:00"),
        ("GENERALES", "2", "2024:06:01", "11:00:00", "11:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta_a = _crear_imagen(os.path.join(cfg.input_folder, "vuelo1"), "DJI_0001_D.JPG")
    ruta_b = _crear_imagen(os.path.join(cfg.input_folder, "vuelo2"), "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:01", "11:00:00", "11:10:00"): (
            dt.datetime(2024, 6, 1, 11, 0, 0), dt.datetime(2024, 6, 1, 11, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta_a: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta_b: dt.datetime(2024, 6, 1, 11, 5, 0),
    })
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    salida_a = filas[ruta_a]["ruta_salida_original"]
    salida_b = filas[ruta_b]["ruta_salida_original"]
    assert salida_a != salida_b
    assert {os.path.basename(salida_a), os.path.basename(salida_b)} == {
        "DJI_0001_D.JPG", "DJI_0001_D_2.JPG"}
    assert os.path.dirname(salida_a) == os.path.dirname(salida_b)
    manifiesto.cerrar()


def test_variantes_de_escritura_del_pb_van_todas_a_fotos_generales(tmp_path):
    """"GENERAL", "FOTOS GENERALES" y "FOTOS_GENERALES" en el estadillo (con
    espacios/mayúsculas de sobra) son la MISMA cosa que "GENERALES"."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        (" general ", "1", "2024:06:01", "10:00:00", "10:10:00"),
        ("Fotos Generales", "2", "2024:06:01", "11:00:00", "11:10:00"),
        ("fotos_generales", "3", "2024:06:01", "12:00:00", "12:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta_a = _crear_imagen(cfg.input_folder, "A.JPG")
    ruta_b = _crear_imagen(cfg.input_folder, "B.JPG")
    ruta_c = _crear_imagen(cfg.input_folder, "C.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:01", "11:00:00", "11:10:00"): (
            dt.datetime(2024, 6, 1, 11, 0, 0), dt.datetime(2024, 6, 1, 11, 10, 0)),
        ("2024:06:01", "12:00:00", "12:10:00"): (
            dt.datetime(2024, 6, 1, 12, 0, 0), dt.datetime(2024, 6, 1, 12, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta_a: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta_b: dt.datetime(2024, 6, 1, 11, 5, 0),
        ruta_c: dt.datetime(2024, 6, 1, 12, 5, 0),
    })
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    carpeta_esperada = os.path.join(cfg.output_folder, "FOTOS_GENERALES")
    for fila in manifiesto.todas():
        assert os.path.dirname(fila["ruta_salida_original"]) == carpeta_esperada
        assert fila["angulo_giro"] == 0
    manifiesto.cerrar()


def test_generales_multicarpeta_scoping_no_contamina_a_otro_piloto(tmp_path):
    """Dos pilotos el mismo día, cada uno en su carpeta con su propio
    estadillo (`sel/PILOTO_A/{DCIM, estadillo con filas PB1 y GENERALES}`,
    `sel/PILOTO_B/{DCIM, estadillo con PB2}`), con horas SOLAPADAS entre la
    ventana GENERALES de A y la ventana PB2 de B: las fotos GENERALES de A
    acaban en `<output>/FOTOS_GENERALES/` sin girar (yaw=90 no debe rotar),
    y las fotos de B -con el MISMO reloj que la ventana GENERALES de A- no
    se contaminan: siguen cayendo en su propio PB2, no en FOTOS_GENERALES
    (ver scoping por carpeta, `_ventanas_para_imagen`)."""
    sel = tmp_path / "sel"
    piloto_a = sel / "PILOTO_A"
    piloto_b = sel / "PILOTO_B"
    os.makedirs(piloto_a, exist_ok=True)
    os.makedirs(piloto_b, exist_ok=True)
    _escribir_estadillo(piloto_a / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
        ("GENERALES", "1", "2024:06:01", "10:10:00", "10:20:00"),
    ])
    _escribir_estadillo(piloto_b / "estadillo.csv", [
        ("2", "1", "2024:06:01", "10:10:00", "10:20:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(piloto_a / "estadillo.csv"), str(piloto_b / "estadillo.csv")])
    cfg = _cfg(tmp_path, input_folder=str(sel), estad=estad)
    ruta_a1 = _crear_imagen(piloto_a / "DCIM", "DJI_0001_D.JPG")
    ruta_a2 = _crear_imagen(piloto_a / "DCIM", "DJI_0002_D.JPG")
    ruta_b = _crear_imagen(piloto_b / "DCIM", "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:01", "10:10:00", "10:20:00"): (
            dt.datetime(2024, 6, 1, 10, 10, 0), dt.datetime(2024, 6, 1, 10, 20, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    ts_pb1 = dt.datetime(2024, 6, 1, 10, 5, 0)
    # Mismo reloj para la GENERALES de A y la foto de B: sin scoping, la
    # ventana GENERALES de A (10:10-10:20) también reclamaría la foto de B.
    ts_solapado = dt.datetime(2024, 6, 1, 10, 15, 0)
    exif = _ExifDePrueba(
        timestamps={ruta_a1: ts_pb1, ruta_a2: ts_solapado, ruta_b: ts_solapado},
        yaws={ruta_a2: 90.0},
    )
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    carpeta_generales = os.path.join(cfg.output_folder, "FOTOS_GENERALES")

    assert filas[ruta_a1]["pb"] == "1"
    assert os.path.dirname(filas[ruta_a1]["ruta_salida_original"]) != carpeta_generales

    assert os.path.dirname(filas[ruta_a2]["ruta_salida_original"]) == carpeta_generales
    assert filas[ruta_a2]["angulo_giro"] == 0

    assert filas[ruta_b]["pb"] == "2"
    assert filas[ruta_b]["vuelo"] == "1"
    assert os.path.dirname(filas[ruta_b]["ruta_salida_original"]) != carpeta_generales
    manifiesto.cerrar()
