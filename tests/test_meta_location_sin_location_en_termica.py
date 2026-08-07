"""El location.csv NO se duplica dentro de la carpeta del vuelo térmico.

Hasta v3.4.6, `gen_meta_location` copiaba el `<vuelo>_location.csv` de la carpeta RGB
hermana dentro de `TERMICA/<PBX>/<PBX_VXX>/`. Daniel lo reportó como salida sobrante: ese
CSV lista las imágenes `_W` (RGB), no las `_T`, y era un duplicado byte a byte del que ya
está en `RGB/<PBX>/<PBX_VXX>/`. En TERMICA se queda solo el `_meta.csv`, que sí describe
sus imágenes.

Estos tests son el guard contra reintroducir la copia.
"""

import os

import pandas as pd

from exif import MetaLocation
from utils import OrganizerLogger as ol


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _monta_vuelo(root):
    """TERMICA/<vuelo> vacía + RGB/<vuelo> con su location.csv ya generado (paso previo real)."""
    termica_vuelo = root / "TERMICA" / "PB1_V01"
    rgb_vuelo = root / "RGB" / "PB1_V01"
    termica_vuelo.mkdir(parents=True)
    rgb_vuelo.mkdir(parents=True)
    location_df = pd.DataFrame([["20260728_100801_DJI_0001_W.JPG", 1.0, 2.0, 3.0, 4.0]])
    location_df.to_csv(rgb_vuelo / "PB1_V01_location.csv", header=False, index=False)
    return termica_vuelo, rgb_vuelo


def _meta_location(tmp_path):
    logger = ol("test_sin_location_en_termica", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    obj = MetaLocation(logger)
    obj.current_image_number = 0
    obj.total_images_number = 1
    obj.stop = False
    return obj


def _gen_meta(obj, termica_vuelo, csv_folder):
    obj.gen_meta_location(str(termica_vuelo), "meta.csv", FakeSignal(), FakeSignal(), str(csv_folder), 0.0, False)


def test_gen_meta_location_no_copia_el_location_a_la_carpeta_termica(tmp_path):
    termica_vuelo, _ = _monta_vuelo(tmp_path)
    csv_folder = tmp_path / "CSVs"
    csv_folder.mkdir()

    _gen_meta(_meta_location(tmp_path), termica_vuelo, csv_folder)

    assert not os.path.exists(termica_vuelo / "PB1_V01_location.csv")
    assert [f for f in os.listdir(termica_vuelo) if f.endswith(".csv")] == []


def test_el_location_sigue_en_la_carpeta_rgb(tmp_path):
    """No se ha movido: quitar la copia no puede dejar al vuelo RGB sin su location."""
    termica_vuelo, rgb_vuelo = _monta_vuelo(tmp_path)
    csv_folder = tmp_path / "CSVs"
    csv_folder.mkdir()

    _gen_meta(_meta_location(tmp_path), termica_vuelo, csv_folder)

    assert os.path.exists(rgb_vuelo / "PB1_V01_location.csv")


def test_copy_flight_csvs_lleva_los_dos_a_csvs_sin_el_location_en_termica(tmp_path, organizer_logger_stub):
    """`CSVs/` es donde se consultan: el location se lee de la carpeta RGB, no de TERMICA."""
    termica_vuelo, _ = _monta_vuelo(tmp_path)
    (termica_vuelo / "PB1_V01_meta.csv").write_text("20260728_100801_DJI_0002_T.JPG,1.0,2.0\n")

    from pipeline import GenStructFolder

    obj = GenStructFolder(organizer_logger_stub)
    obj.root_folder = str(tmp_path)
    obj.csvs_root_folder = str(tmp_path / "CSVs")
    os.makedirs(obj.csvs_root_folder)

    obj.copy_flight_csvs_to_csvs_folder(str(termica_vuelo), "PB1_V01_meta.csv")

    assert os.path.exists(os.path.join(obj.csvs_root_folder, "PB1_V01_meta.csv"))
    assert os.path.exists(os.path.join(obj.csvs_root_folder, "PB1_V01_location.csv"))
