import numpy as np
import pandas as pd
from utils import OrganizerLogger as ol
from exif import MetaLocation


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_meta_location(tmp_path):
    logger = ol("test_meta_location_altitud_siempre", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return MetaLocation(logger)


def test_safe_float_altitude_con_xmp_valido(tmp_path):
    obj = _make_meta_location(tmp_path)
    xmp_data = ["100", "12.5", "0", "0", "0", "0", "0", "0", "0"]

    assert obj.safe_float_altitude(xmp_data, FakeSignal()) == 12.5


def test_safe_float_altitude_con_xmp_invalido_no_crashea(tmp_path):
    obj = _make_meta_location(tmp_path)

    resultado = obj.safe_float_altitude(["100", "no_es_un_numero"], FakeSignal())
    assert np.isnan(resultado)


def test_gen_meta_location_columna_alturarelativa_siempre_presente(tmp_path, synthetic_jpeg):
    carpeta = tmp_path / "PB1_V01"
    carpeta.mkdir()
    synthetic_jpeg(str(carpeta / "DJI_0001.jpg"), lat=40.0, lon=-3.0, relative_altitude=30.0)
    csv_folder = tmp_path / "csv_dest"
    csv_folder.mkdir()

    obj = _make_meta_location(tmp_path)
    obj.current_image_number = 0
    obj.total_images_number = 1
    obj.stop = False

    obj.gen_meta_location(str(carpeta), "meta.csv", FakeSignal(), FakeSignal(), str(csv_folder), 50.0, False)

    generated = list(carpeta.glob("*_meta.csv"))
    assert len(generated) == 1
    df = pd.read_csv(generated[0], header=None)
    # Columnas: Foto,Lat,Lon,GimbalYawDegree,GimbalPitchDegree,AlturaRelativa -> 6 columnas cuando calculate_proyected_distance=False
    assert df.shape[1] == 6
    assert df.iloc[0, 5] == 30.0
