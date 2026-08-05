import os
import utils as utils_module


def test_csvs_folder_recibe_copia_de_meta_y_location(tmp_path, organizer_logger_stub):
    # Estructura real: TERMICA/<vuelo> y RGB/<vuelo> (input_folder que llega a
    # copy_flight_csvs_to_csvs_folder es la carpeta del vuelo, basename "PB1_V01").
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
    obj.csvs_root_folder = str(root / "CSVs")
    os.makedirs(obj.csvs_root_folder)

    obj.copy_flight_csvs_to_csvs_folder(str(termica_vuelo), "meta.csv")

    assert os.path.exists(os.path.join(obj.csvs_root_folder, "meta.csv"))
    assert os.path.exists(os.path.join(obj.csvs_root_folder, "location.csv"))


def _monta_vuelo(root):
    termica_vuelo = root / "TERMICA" / "PB1_V01"
    rgb_vuelo = root / "RGB" / "PB1_V01"
    termica_vuelo.mkdir(parents=True)
    rgb_vuelo.mkdir(parents=True)
    (termica_vuelo / "PB1_V01_meta.csv").write_text("Foto,Lat,Lon\n")
    (rgb_vuelo / "PB1_V01_location.csv").write_text("Foto,Lat,Lon\n")
    return termica_vuelo


def _obj(root, organizer_logger_stub):
    from pipeline import GenStructFolder
    obj = GenStructFolder(organizer_logger_stub)
    obj.root_folder = str(root)
    obj.csvs_root_folder = str(root / "CSVs")
    return obj


def _noop():
    import types
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def test_reprocesar_no_versiona_los_csv(tmp_path, organizer_logger_stub):
    """Re-procesar el mismo destino dejaba `<vuelo>_location_1.csv`, `_2`, ... idénticos:
    safe_copy2 no sobrescribe, versiona. El meta/location regenerado de un vuelo es el
    mismo dato, así que se sobrescribe."""
    termica_vuelo = _monta_vuelo(tmp_path)
    obj = _obj(tmp_path, organizer_logger_stub)

    for _ in range(3):
        obj.copy_flight_csvs(str(termica_vuelo), _noop())

    destino = tmp_path / "CSVs"
    assert sorted(p.name for p in destino.glob("*.csv")) == ["PB1_V01_location.csv", "PB1_V01_meta.csv"], (
        f"Han aparecido copias versionadas: {sorted(p.name for p in destino.glob('*.csv'))}"
    )


def test_varios_csv_en_el_vuelo_no_repiten_la_copia(tmp_path, organizer_logger_stub):
    """El bucle copiaba el par meta/location una vez por CADA .csv de la carpeta, así que
    un vuelo con CSVs extra acababa con el location duplicado."""
    termica_vuelo = _monta_vuelo(tmp_path)
    (termica_vuelo / "PB1_V01_Videofiles.csv").write_text("New Name,Original Name,Degree\n")
    (termica_vuelo / "otro.csv").write_text("x\n")
    obj = _obj(tmp_path, organizer_logger_stub)

    obj.copy_flight_csvs(str(termica_vuelo), _noop())

    destino = tmp_path / "CSVs"
    assert sorted(p.name for p in destino.glob("*.csv")) == ["PB1_V01_location.csv", "PB1_V01_meta.csv"]


def test_en_csvs_no_queda_ninguna_subcarpeta(tmp_path, organizer_logger_stub):
    """`CSVs/` es plana: solo ficheros .csv, ninguna carpeta por vuelo.

    Hasta v3.4.1 el criterio de giro y las copias de meta/location se escribían en
    `CSVs/<vuelo>/` (herencia de cuando vivían en `MINIATURAS/<vuelo>/`). Eso metía una
    carpeta dentro de CSVs y duplicaba byte a byte el meta/location que el paso de
    meta/location ya deja plano ahí mismo.
    """
    import pandas as pd

    from pipeline import GenStructFolder
    termica_vuelo = _monta_vuelo(tmp_path)
    obj = _obj(tmp_path, organizer_logger_stub)

    obj.copy_flight_csvs(str(termica_vuelo), _noop())
    obj.write_videofiles_csv(str(termica_vuelo),
                             pd.DataFrame(columns=["New Name", "Original Name", "Degree"]))

    csvs = tmp_path / "CSVs"
    # La ÚNICA subcarpeta admitida es la interna del criterio de giro. Cualquier otra
    # es la regresión que fijaba este test (v3.4.0-v3.4.1 creaban una carpeta por
    # vuelo). El criterio se aparta ahí a propósito desde v3.4.4: no es un CSV que el
    # usuario consuma y estorbaba entre los de meta/location.
    assert [p.name for p in csvs.iterdir() if p.is_dir()] == [utils_module.CRITERIO_DIRNAME], (
        "Ha aparecido una carpeta inesperada dentro de CSVs; ahí solo deben ir los .csv "
        f"y la interna {utils_module.CRITERIO_DIRNAME}/."
    )
    assert not (csvs / "PB1_V01_Videofiles.csv").exists(), (
        "El criterio de giro sigue suelto en CSVs/: debe ir en la subcarpeta interna."
    )
    assert (csvs / utils_module.CRITERIO_DIRNAME / "PB1_V01_Videofiles.csv").exists()
