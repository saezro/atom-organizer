import os
import pandas as pd
from utils import OrganizerLogger as ol
from exif import MetaLocation


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_meta_location(tmp_path):
    logger = ol("test_meta_location", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return MetaLocation(logger)


def test_check_input_folder_and_iterate_con_rgb_extra_no_revienta(tmp_path):
    input_folder = tmp_path / "PLANTA"
    (input_folder / "RGB").mkdir(parents=True)
    (input_folder / "TERMICA").mkdir(parents=True)
    (input_folder / "RGB_Extra").mkdir(parents=True)
    csv_folder = tmp_path / "csvs"
    csv_folder.mkdir()

    ml = _make_meta_location(tmp_path)
    ml.total_images_number = 1
    cb = FakeSignal()

    resultado = ml.check_input_folder_and_iterate(
        str(input_folder), cb, cb, str(csv_folder), flight_height=50.0, calculate_proyected_distance=True
    )

    assert resultado is True


def test_check_input_folder_and_iterate_detecta_termica_ausente(tmp_path):
    # Solo existe RGB, falta TERMICA: el condicional roto ("TERMICA" and "RGB" not in list_dir)
    # nunca evalúa la presencia real de TERMICA, así que hay que comprobar que SÍ lo detecta tras el fix.
    input_folder = tmp_path / "PLANTA_SIN_TERMICA"
    (input_folder / "RGB").mkdir(parents=True)
    csv_folder = tmp_path / "csvs2"
    csv_folder.mkdir()

    ml = _make_meta_location(tmp_path)
    ml.total_images_number = 1
    cb = FakeSignal()

    resultado = ml.check_input_folder_and_iterate(
        str(input_folder), cb, cb, str(csv_folder), flight_height=50.0, calculate_proyected_distance=True
    )

    assert resultado is False


def test_check_gimbal_yaw_pitch_values_corrige_string_cero(tmp_path):
    df = pd.DataFrame({
        "GimbalYawDegree": ["10", "0", "30"],
        "GimbalPitchDegree": ["-90", "-90", "-90"],
        "Lat": [1.0, 1.0, 1.0],
        "Lon": [2.0, 2.0, 2.0],
        "CalculatedDistance": [0.0, 0.0, 0.0],
        "LatitudFoto": [0.0, 0.0, 0.0],
        "LongitudFoto": [0.0, 0.0, 0.0],
    })
    ml = _make_meta_location(tmp_path)
    cb = FakeSignal()

    resultado = ml.check_gimbal_yaw_pitch_values(df, flight_height=50.0, progress_callback=cb)

    assert float(resultado.loc[1, "GimbalYawDegree"]) == 20.0, "El '0' en string no se ha corregido a la media (10+30)/2"


def test_check_gimbal_yaw_pitch_values_sin_no_ceros_no_lanza_indexerror(tmp_path):
    df = pd.DataFrame({
        "GimbalYawDegree": ["0", "0", "0"],
        "GimbalPitchDegree": ["-90", "-90", "-90"],
        "Lat": [1.0, 1.0, 1.0],
        "Lon": [2.0, 2.0, 2.0],
        "CalculatedDistance": [0.0, 0.0, 0.0],
        "LatitudFoto": [0.0, 0.0, 0.0],
        "LongitudFoto": [0.0, 0.0, 0.0],
    })
    ml = _make_meta_location(tmp_path)
    cb = FakeSignal()

    # No debe lanzar IndexError aunque no exista ningún valor no-cero en toda la serie.
    resultado = ml.check_gimbal_yaw_pitch_values(df, flight_height=50.0, progress_callback=cb)
    assert resultado is not None
