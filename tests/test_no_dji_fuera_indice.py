"""Ficheros no DJI (PNG de análisis, Make ajeno) quedan fuera del índice."""
import os

import datetime as dt
import logging

import exif as exif_mod
from atom_core.indice import construir_indice
from tests.test_dias_sin_estadillo import _Rec
from tests.test_indice_organizado import (
    _PipelineDePrueba, _cfg, _crear_imagen, _escribir_estadillo, _manifiesto,
)

VENTANAS = {("2024:06:01", "10:00:00", "10:10:00"):
            (dt.datetime(2024, 6, 1, 10, 0), dt.datetime(2024, 6, 1, 10, 10))}


def _correr(tmp_path, make_dji_jpeg, make_canon=False):
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    cfg = _cfg(tmp_path)
    os.makedirs(cfg.input_folder, exist_ok=True)
    make_dji_jpeg(os.path.join(cfg.input_folder, "DJI_0001_D.JPG"), make="DJI", model="M3T")
    if make_canon:
        make_dji_jpeg(os.path.join(cfg.input_folder, "IMG_0001.JPG"), make="Canon", model="EOS")
    _crear_imagen(cfg.input_folder, "Conteo_Data_Propiedades_1.png")
    log = _Rec()
    m = _manifiesto(tmp_path)
    res = construir_indice(cfg, _PipelineDePrueba(VENTANAS), exif_mod.GeneralInformationFromImage(logging.getLogger('t')), m, log, _Rec(), _Rec())
    m.cerrar()
    return cfg, res, log


def test_png_sin_make_ignorado_y_jpg_dji_indexado(tmp_path, make_dji_jpeg):
    cfg, res, log = _correr(tmp_path, make_dji_jpeg)
    assert res["total"] == 1
    assert [(os.path.basename(r), mot) for r, mot in res["no_dji"]] == [
        ("Conteo_Data_Propiedades_1.png", "sin Make")]
    assert any("AVISO: 1 ficheros no DJI ignorados" in t and "Conteo_Data_Propiedades_1.png" in t
               for t in log.textos)
    assert os.path.exists(os.path.join(cfg.input_folder, "Conteo_Data_Propiedades_1.png"))


def test_make_canon_ignorado_con_motivo(tmp_path, make_dji_jpeg):
    _cfg_, res, _log = _correr(tmp_path, make_dji_jpeg, make_canon=True)
    motivos = {os.path.basename(r): m for r, m in res["no_dji"]}
    assert motivos["IMG_0001.JPG"] == "Make=Canon"
    assert res["total"] == 1


def test_jpg_dji_con_exif_fuera_del_buffer_se_indexa(tmp_path, make_dji_jpeg, monkeypatch):
    from atom_core import indice
    monkeypatch.setattr(indice, "_exif_entero_en_buffer", lambda buf: False)
    _cfg_, res, _log = _correr(tmp_path, make_dji_jpeg)
    assert res["total"] == 1
    assert all(not r.endswith(".JPG") for r, _m in res["no_dji"])


def test_make_ilegible_se_indexa_con_aviso(tmp_path, make_dji_jpeg, monkeypatch):
    from atom_core import indice

    def _falla(*a, **k):
        raise OSError("lectura rota")
    monkeypatch.setattr(indice, "_make_desde_buffer", _falla)
    monkeypatch.setattr(exif_mod.GeneralInformationFromImage, "get_make", _falla)
    _cfg_, res, log = _correr(tmp_path, make_dji_jpeg)
    assert res["no_dji"] == []  # ilegible != descartado
    assert res["total"] >= 1
    assert any("Make ilegible" in t for t in log.textos)


def test_png_gcs_es_no_dji_y_jpg_gcs_no():
    from atom_core.indice import _MetadatosImagen, _motivo_no_dji
    def dato(r):
        return _MetadatosImagen(ruta=r, nombre=os.path.basename(r), timestamp=None,
                                modelo=None, yaw=None, gps=None)
    assert _motivo_no_dji(dato("gs://b/p/a.PNG")) == "PNG no DJI"
    assert _motivo_no_dji(dato("gs://b/p/a.JPG")) is None
