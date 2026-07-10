import os
import utils as utils_module


def test_csvs_folder_recibe_copia_de_meta_y_location(tmp_path, organizer_logger_stub):
    # Estructura real: TERMICA/<vuelo> y RGB/<vuelo> (input_folder que llega a
    # copy_flight_csvs_to_csvs_folder es la carpeta del vuelo, basename "PB1_V01",
    # análogo al patrón ya existente para MINIATURAS con os.path.basename(input_folder)).
    root = tmp_path
    termica_vuelo = root / "TERMICA" / "PB1_V01"
    rgb_vuelo = root / "RGB" / "PB1_V01"
    termica_vuelo.mkdir(parents=True)
    rgb_vuelo.mkdir(parents=True)
    (termica_vuelo / "meta.csv").write_text("Foto,Lat,Lon\n")
    (rgb_vuelo / "location.csv").write_text("Foto,Lat,Lon\n")

    from pipeline import GenStructFolder
    obj = GenStructFolder(organizer_logger_stub)
    obj.root_folder = str(root)
    obj.miniaturas_root_folder = str(root / "MINIATURAS")
    obj.csvs_root_folder = str(root / "CSVs")
    os.makedirs(os.path.join(obj.miniaturas_root_folder, "PB1_V01_miniaturas"))
    os.makedirs(obj.csvs_root_folder)

    obj.copy_flight_csvs_to_csvs_folder(str(termica_vuelo), "meta.csv")

    assert os.path.exists(os.path.join(obj.csvs_root_folder, "PB1_V01_miniaturas", "meta.csv"))
    assert os.path.exists(os.path.join(obj.csvs_root_folder, "PB1_V01_miniaturas", "location.csv"))
