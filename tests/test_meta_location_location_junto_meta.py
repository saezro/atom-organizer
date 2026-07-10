import os
import pandas as pd
from utils import OrganizerLogger as ol
from exif import MetaLocation


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_meta_location(tmp_path):
    logger = ol("test_meta_location_junto_meta", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return MetaLocation(logger)


def test_gen_meta_location_copia_location_a_carpeta_del_vuelo(tmp_path):
    root = tmp_path
    termica_vuelo = root / "TERMICA" / "PB1_V01"
    rgb_vuelo = root / "RGB" / "PB1_V01"
    termica_vuelo.mkdir(parents=True)
    rgb_vuelo.mkdir(parents=True)
    csv_folder = root / "MINIATURAS_dest"
    csv_folder.mkdir()

    # Simulamos que location.csv ya fue generado en la carpeta RGB hermana (paso previo real del flujo).
    location_df = pd.DataFrame([["foto1.jpg", 1.0, 2.0, 3.0, 4.0]])
    location_df.to_csv(rgb_vuelo / "PB1_V01_location.csv", header=False, index=False)

    obj = _make_meta_location(tmp_path)
    obj.current_image_number = 0
    obj.total_images_number = 1
    obj.stop = False

    # No hay imágenes JPG reales en termica_vuelo, así que gen_meta_location no generará df propio;
    # forzamos directamente la copia llamando al método auxiliar que se va a crear.
    obj.copy_location_csv_to_flight_folder(str(termica_vuelo))

    assert os.path.exists(termica_vuelo / "PB1_V01_location.csv")
