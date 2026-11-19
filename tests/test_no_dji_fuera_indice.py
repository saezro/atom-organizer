"""Ficheros no DJI (PNG de análisis, Make ajeno) quedan fuera del índice."""
import os

import datetime as dt
import logging

import exif as exif_mod
from atom_core.indice import construir_indice
from tests.test_dias_sin_estadillo import _Rec
from tests.test_indice_organizado import (
    _PipelineDePrueba, _cfg, _escribir_estadillo, _manifiesto,
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
    # PNG real sin EXIF/Make (un fichero vacío ahora es error visible, no «sin Make»).
    from PIL import Image
    with Image.new("RGB", (8, 8), color=(10, 20, 30)) as im:
        im.save(os.path.join(cfg.input_folder, "Conteo_Data_Propiedades_1.png"), format="PNG")
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


def test_jpg_dji_con_xmp_fuera_de_la_cabecera_se_indexa(tmp_path, make_dji_jpeg):
    """XMP tras >256 KiB de relleno: Rust pide el fichero entero (`necesita_completo`) y el JPEG DJI se indexa."""
    from tests import corpus_helpers as ch
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    cfg = _cfg(tmp_path)
    os.makedirs(cfg.input_folder, exist_ok=True)
    exif = ch._exif_bytes(fecha="2024:06:01 10:05:00")
    with open(os.path.join(cfg.input_folder, "DJI_0001_D.JPG"), "wb") as fh:
        fh.write(ch._jpeg_dji(exif, ch._xmp_atributos(ch._XMP_DJI), relleno_app3=300_000,
                              xmp_tras_relleno=True))
    m = _manifiesto(tmp_path)
    res = construir_indice(cfg, _PipelineDePrueba(VENTANAS), exif_mod.GeneralInformationFromImage(logging.getLogger('t')), m, _Rec(), _Rec(), _Rec())
    m.cerrar()
    assert res["total"] == 1
    assert res["no_dji"] == []


def test_make_ilegible_se_indexa_con_aviso(tmp_path, make_dji_jpeg, monkeypatch):
    """Rust marca `make_ilegible` (ExifRead lanzaría al leer Make): se indexa, con aviso, no se descarta."""
    import types
    from atom_core import rust_bridge
    exigir_real = rust_bridge.exigir

    class _RsMakeIlegible:
        def __init__(self, rs):
            self._rs = rs

        def __getattr__(self, nombre):
            return getattr(self._rs, nombre)

        def parsear_cabecera(self, *args, **kwargs):
            cab = self._rs.parsear_cabecera(*args, **kwargs)
            campos = {k: getattr(cab, k) for k in dir(cab) if not k.startswith("_")}
            campos["make_ilegible"] = True
            return types.SimpleNamespace(**campos)

    monkeypatch.setattr(rust_bridge, "exigir",
                        lambda fase, *a, **k: _RsMakeIlegible(exigir_real(fase, *a, **k))
                        if fase == "indice" else exigir_real(fase, *a, **k))
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
