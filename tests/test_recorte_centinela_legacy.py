"""Centinela `-` (porcentaje None) en el flujo legacy `iterate_folders_for_rgb_cropping`
y en la tabla `config/recortes_modelos.csv`."""
import csv
import os

from PIL import Image, ImageChops

import pipeline
from atom_core import recortes


class _Sig:
    def emit(self, *a, **k):
        pass


def _jpeg(ruta):
    img = Image.new("RGB", (64, 48), (120, 60, 30))
    img.save(ruta, quality=95)


def test_legacy_pct_none_genera_crop_identico_al_original(tmp_path, organizer_logger_stub, monkeypatch):
    orig = tmp_path / "DJI_0001_Z.JPG"
    _jpeg(str(orig))
    rc = pipeline.RGBCropping(organizer_logger_stub)
    monkeypatch.setattr(rc.exif_management_obj, "get_model", lambda *a, **k: "H30T")
    monkeypatch.setattr(rc, "get_percentage_by_model", lambda *a, **k: None)

    rc.iterate_folders_for_rgb_cropping(str(tmp_path), _Sig(), _Sig(), {"H30T_Z": None}, True, 0)

    crop = tmp_path / "DJI_0001_Z_CROP.JPG"
    assert crop.exists()
    a, b = Image.open(orig).convert("RGB"), Image.open(crop).convert("RGB")
    assert a.size == b.size
    assert ImageChops.difference(a, b).getbbox() is None


def test_crop_centered_image_pct_none_copia_integra(tmp_path, organizer_logger_stub, monkeypatch):
    orig = tmp_path / "DJI_0002_Z.JPG"
    _jpeg(str(orig))
    rc = pipeline.RGBCropping(organizer_logger_stub)
    monkeypatch.setattr(rc.exif_management_obj, "get_model", lambda *a, **k: "H30T")
    monkeypatch.setattr(rc, "get_percentage_by_model", lambda *a, **k: None)
    rc.crop_centered_image(str(tmp_path), "DJI_0002_Z.JPG", {"H30T_Z": None}, True, 0, _Sig())
    b = Image.open(tmp_path / "DJI_0002_Z_CROP.JPG").convert("RGB")
    assert b.size == (64, 48)
    assert ImageChops.difference(Image.open(orig).convert("RGB"), b).getbbox() is None


def test_csv_h30t_z_parsea_con_las_columnas_esperadas():
    ruta = os.path.join(os.path.dirname(__file__), "..", "config", "recortes_modelos.csv")
    with open(ruta, "r", encoding="utf-8-sig", newline="") as f:
        filas = list(csv.reader(f))
    assert tuple(filas[0]) == ("modelo", "camara", "porcentaje", "nota")
    assert all(len(fila) == 4 for fila in filas)
    h30t_z = [f for f in filas if f[:2] == ["H30T", "Z"]]
    assert len(h30t_z) == 1 and h30t_z[0][2] == "-"
    assert recortes.cargar_tabla()[("H30T", "Z")]["porcentaje"] is None
