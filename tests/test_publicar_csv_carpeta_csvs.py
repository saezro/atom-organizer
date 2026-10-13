"""`publicar_csv` (rama local) nunca debe dejar `CSVs` como FICHERO."""
import os

import pandas as pd

import exif
from atom_core import cierre
from utils import OrganizerLogger
from tests.test_cierre_meta_location_manifiesto import (
    _IMAGENES, _Cb, _arbol, _cfg, _manifiesto,
)


def _ml():
    return exif.MetaLocation(OrganizerLogger("t", create_file_handler=False))


def _df():
    return pd.DataFrame({"Foto": ["a.JPG"], "Fecha": ["2026-01-15 10:00:00"]})


def _publicar(tmp_path, csv_folder):
    vuelo = tmp_path / "PB1_V1"
    vuelo.mkdir()
    _ml().publicar_csv(_df(), str(vuelo), "meta.csv", str(csv_folder), _Cb())
    return "PB1_V1_meta.csv"


def test_publicar_csv_crea_carpeta_csvs_inexistente(tmp_path):
    csv_folder = tmp_path / "CSVs"
    nombre = _publicar(tmp_path, csv_folder)
    assert csv_folder.is_dir()
    assert (csv_folder / nombre).is_file()


def test_publicar_csv_con_fichero_csvs_previo_lo_aparta(tmp_path):
    csv_folder = tmp_path / "CSVs"
    csv_folder.write_bytes(b"dato viejo")
    nombre = _publicar(tmp_path, csv_folder)
    assert csv_folder.is_dir()
    assert (csv_folder / nombre).is_file()
    rotos = [p for p in tmp_path.iterdir() if p.name.startswith("CSVs.roto-")]
    assert len(rotos) == 1 and rotos[0].read_bytes() == b"dato viejo"


def test_cierre_sin_carpeta_csvs_la_crea(tmp_path, make_dji_jpeg, logger):
    _arbol(tmp_path, make_dji_jpeg)
    os.rmdir(tmp_path / "CSVs")
    manifiesto = _manifiesto(tmp_path, logger, True)
    cierre._emitir_meta_location(manifiesto, _cfg(tmp_path, False), _Cb())
    assert (tmp_path / "CSVs").is_dir()
    nombres = os.listdir(tmp_path / "CSVs")
    assert "PB1_V1_meta.csv" in nombres and "PB1_V1_location.csv" in nombres
