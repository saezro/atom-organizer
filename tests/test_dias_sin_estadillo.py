"""Aviso (nunca bloqueante) de días con fotos y sin estadillo."""
import datetime as dt

from atom_core.indice import _MetadatosImagen, _dias_sin_estadillo, construir_indice
from tests.test_indice_organizado import (
    _PipelineDePrueba, _ExifDePrueba, _cfg, _crear_imagen, _escribir_estadillo, _manifiesto,
)


class _Rec:
    def __init__(self):
        self.textos = []

    def emit(self, *a, **k):
        self.textos.append(str(a[0]) if a else "")


def _dato(ts, i=0):
    return _MetadatosImagen(ruta=f"/x/{i}.JPG", nombre=f"{i}.JPG", timestamp=ts,
                            modelo=None, yaw=None, gps=None, pitch=None, flight_yaw=None)


def test_helper_dia_sin_estadillo_y_sin_timestamp_ignorado():
    datos = [_dato(dt.datetime(2026, 10, 2, 9, 0), i) for i in range(3)]
    datos += [_dato(dt.datetime(2026, 10, 1, 9, 0), 10), _dato(None, 11)]
    assert _dias_sin_estadillo(datos, [{"fecha": "2026:10:01"}]) == [("2026-10-02", 3)]


def test_helper_todo_cubierto_y_nunca_lanza():
    datos = [_dato(dt.datetime(2026, 10, 1, 9, 0))]
    assert _dias_sin_estadillo(datos, [{"fecha": "2026:10:01"}]) == []
    assert _dias_sin_estadillo(datos, [{"fecha": "basura"}]) == []
    assert _dias_sin_estadillo(datos, [None]) == []
    assert _dias_sin_estadillo(None, None) == []


def test_construir_indice_emite_aviso_y_lo_devuelve(tmp_path):
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"):
                (dt.datetime(2024, 6, 1, 10, 0), dt.datetime(2024, 6, 1, 10, 10))}
    cfg = _cfg(tmp_path)
    r1 = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    r2 = _crear_imagen(cfg.input_folder, "DJI_0002_D.JPG")
    exif = _ExifDePrueba(timestamps={r1: dt.datetime(2024, 6, 1, 10, 5),
                                     r2: dt.datetime(2024, 6, 3, 10, 5)},
                         modelos={r1: "M3T", r2: "M3T"})
    log = _Rec()
    manifiesto = _manifiesto(tmp_path)
    res = construir_indice(cfg, _PipelineDePrueba(ventanas), exif, manifiesto,
                           log, _Rec(), _Rec())
    manifiesto.cerrar()
    assert res["dias_sin_estadillo"] == [("2024-06-03", 1)]
    assert any("día 2024-06-03 sin estadillo (1 fotos)" in t for t in log.textos)


def test_construir_indice_todo_cubierto_sin_aviso(tmp_path):
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"):
                (dt.datetime(2024, 6, 1, 10, 0), dt.datetime(2024, 6, 1, 10, 10))}
    cfg = _cfg(tmp_path)
    r1 = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    exif = _ExifDePrueba(timestamps={r1: dt.datetime(2024, 6, 1, 10, 5)}, modelos={r1: "M3T"})
    log = _Rec()
    manifiesto = _manifiesto(tmp_path)
    res = construir_indice(cfg, _PipelineDePrueba(ventanas), exif, manifiesto,
                           log, _Rec(), _Rec())
    manifiesto.cerrar()
    assert res["dias_sin_estadillo"] == []
    assert not any("sin estadillo" in t for t in log.textos)
