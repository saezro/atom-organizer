"""Regla del responsable (2026-09-28, caso de una planta): una planta con
orientación 'Horizontal' NUNCA se gira -ángulo 0 para todas
sus imágenes, sea cual sea el GimbalYaw-. El resto de orientaciones
('Vertical', 'Varias') y la orientación desconocida siguen el consenso por
yaw de siempre (`atom_core.indice._consenso_de_angulo_por_vuelo`).

Yaws de este test: de una planta real (orientacion=
'Horizontal'), leídos del índice real de la organización del 2026-09-28
(`INDICE_<PLANTA>_ORGANIZADO_PRUEBA.xlsx`, hoja `Imagenes`): PB1_V1 (yaw~16.2,
860 imágenes → 90º con la regla vieja), PB2_V1 (yaw~-52.5, 1120 imágenes →
270º con la regla vieja) y GENERALES/<PLANTA> (yaw~18.4, 6 imágenes → 90º con la
regla vieja). Con `orientacion='Horizontal'` las tres deben quedar en 0.

Mismo estilo que `tests/test_indice_organizado.py`: dobles a mano, sin
`unittest.mock`.
"""
import datetime as dt
import os

from atom_core.indice import construir_indice
from atom_core.manifiesto import Manifiesto
from utils import SplitImagesConfig


class _Signal:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor):
        self.mensajes.append(valor)


class _PipelineDePrueba:
    def __init__(self, ventanas):
        self._ventanas = ventanas
        self.percentage_by_models = {}

    def ventana_horaria_vuelo(self, fecha, horaInicio, horaFinal,
                              margen_segundos, desfase_horas, desfase_minutos):
        return self._ventanas[(fecha, horaInicio, horaFinal)]

    def nombre_destino(self, image, input_folder, rename, mismatch_hours,
                       mismatch_minutes, ruta_local=None, timestamp=None):
        return ""

    def get_percentage_by_model(self, model, percentage_cropping_dict):
        return percentage_cropping_dict[model.strip().upper()]

    def read_auto_rotate_degree(self, input_folder, progress_callback):
        return 0


class _ExifDePrueba:
    def __init__(self, timestamps=None, yaws=None):
        self._timestamps = timestamps or {}
        self._yaws = yaws or {}

    def get_timestamp_from_image(self, pathImagen):
        return self._timestamps.get(pathImagen)

    def get_model(self, filename, progress_callback):
        return "M3T"

    def get_gimbal_yaw_pitch(self, filename, bloque_xmp=None):
        return [str(self._yaws.get(filename, 0.0)), "0"]

    def leerLatitudLongitudAltitud_exif_DJI(self, pathImagen, progress_callback):
        return None


def _escribir_estadillo(ruta, filas):
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n")
        for pb, vuelo, fecha, hora_inicio, hora_final in filas:
            fh.write(f"{pb};{vuelo};{fecha};{hora_inicio};{hora_final}\n")


def _cfg(tmp_path, **overrides):
    base = dict(
        input_folder=str(tmp_path / "origen"),
        output_folder=str(tmp_path / "destino"),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="_W",
        estad=str(tmp_path / "estadillo.csv"),
        choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=False,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=False, cropping_mode_auto=True, crop_percentage="50",
        gen_meta_location=False, gen_thumbnails=False, seconds_range=5.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        # Bandas de una planta Horizontal (`utils.ROTATION_YAW_MARGIN=80`, ver la
        # task): yaw~16.2 y yaw~-52.5 caen dentro del margen de giro.
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=80,
        gen_thumbnails_max_error=50, gen_thumbnails_subs_to_angle=80,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=False, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
        orientacion="",
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


def _preparar_dos_vuelos_planta_e(tmp_path, **overrides):
    """PB1_V1 (yaw~16.2) y PB2_V1 (yaw~-52.5), los dos vuelos de una planta Horizontal
    que en el organizado del 2026-09-28 giraron por consenso de yaw (90º y
    270º respectivamente). Una imagen de cada uno basta: el consenso por
    mayoría con una sola imagen por vuelo ya decide la banda."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2026:09:11", "12:50:00", "13:10:00"),
        ("2", "1", "2026:09:11", "13:20:00", "13:40:00"),
    ])
    cfg = _cfg(tmp_path, **overrides)
    ruta_pb1 = _crear_imagen(cfg.input_folder, "DJI_0905_W.JPG")
    ruta_pb2 = _crear_imagen(cfg.input_folder, "DJI_1000_W.JPG")

    ventanas = {
        ("2026:09:11", "12:50:00", "13:10:00"): (
            dt.datetime(2026, 9, 11, 12, 50, 0), dt.datetime(2026, 9, 11, 13, 10, 0)),
        ("2026:09:11", "13:20:00", "13:40:00"): (
            dt.datetime(2026, 9, 11, 13, 20, 0), dt.datetime(2026, 9, 11, 13, 40, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(
        timestamps={
            ruta_pb1: dt.datetime(2026, 9, 11, 12, 51, 38),
            ruta_pb2: dt.datetime(2026, 9, 11, 13, 30, 0),
        },
        yaws={ruta_pb1: 16.2, ruta_pb2: -52.5},
    )
    manifiesto = _manifiesto(tmp_path)
    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())
    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    manifiesto.cerrar()
    return filas, ruta_pb1, ruta_pb2


def test_orientacion_horizontal_nunca_gira_con_yaws_reales_de_planta_e(tmp_path):
    """Planta Horizontal (orientacion='Horizontal'): con la regla vieja el
    PB1_V1 giraba a 90º y el PB2_V1 a 270º (860+1120 imágenes reales, ver
    docstring del módulo). Con `orientacion='Horizontal'` ambos deben
    quedar en 0, pase lo que pase con el yaw."""
    filas, ruta_pb1, ruta_pb2 = _preparar_dos_vuelos_planta_e(tmp_path, orientacion="Horizontal")
    assert filas[ruta_pb1]["angulo_giro"] == 0
    assert filas[ruta_pb2]["angulo_giro"] == 0


def test_orientacion_vertical_mantiene_consenso_por_yaw(tmp_path):
    """`orientacion='Vertical'` (o cualquier otra que no sea 'Horizontal')
    no cambia nada: sigue el consenso por yaw de siempre, igual que
    `test_todas_las_filas_de_un_vuelo_comparten_angulo`."""
    filas, ruta_pb1, ruta_pb2 = _preparar_dos_vuelos_planta_e(tmp_path, orientacion="Vertical")
    assert filas[ruta_pb1]["angulo_giro"] == 90
    assert filas[ruta_pb2]["angulo_giro"] == 270


def test_orientacion_ausente_mantiene_comportamiento_actual_y_avisa(tmp_path):
    """Sin orientación (inspección sin elegir, o la Suite todavía sin el
    campo -ver `lib/organizer-catalogo.js` de Atom-suite-): se sigue girando
    por yaw como hasta hoy, pero se avisa por `progress_callback` de que es
    un giro a ciegas, sin saber si la planta es Horizontal."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2026:09:11", "12:50:00", "13:10:00"),
        ("2", "1", "2026:09:11", "13:20:00", "13:40:00"),
    ])
    cfg = _cfg(tmp_path, orientacion="")
    ruta_pb1 = _crear_imagen(cfg.input_folder, "DJI_0905_W.JPG")
    ruta_pb2 = _crear_imagen(cfg.input_folder, "DJI_1000_W.JPG")

    ventanas = {
        ("2026:09:11", "12:50:00", "13:10:00"): (
            dt.datetime(2026, 9, 11, 12, 50, 0), dt.datetime(2026, 9, 11, 13, 10, 0)),
        ("2026:09:11", "13:20:00", "13:40:00"): (
            dt.datetime(2026, 9, 11, 13, 20, 0), dt.datetime(2026, 9, 11, 13, 40, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(
        timestamps={
            ruta_pb1: dt.datetime(2026, 9, 11, 12, 51, 38),
            ruta_pb2: dt.datetime(2026, 9, 11, 13, 30, 0),
        },
        yaws={ruta_pb1: 16.2, ruta_pb2: -52.5},
    )
    manifiesto = _manifiesto(tmp_path)
    progress_callback = _Signal()
    construir_indice(cfg, pipeline, exif, manifiesto, progress_callback, _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta_pb1]["angulo_giro"] == 90
    assert filas[ruta_pb2]["angulo_giro"] == 270
    assert any("orientación de la planta desconocida" in m for m in progress_callback.mensajes)
    manifiesto.cerrar()
