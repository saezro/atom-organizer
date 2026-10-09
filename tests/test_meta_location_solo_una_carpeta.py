"""Runs con una sola de las carpetas RGB/TERMICA (3.4.x): sin ERROR y con el CSV que toca."""
import datetime as dt
import os
from types import SimpleNamespace

from atom_core import cierre
from atom_core.manifiesto import FilaManifiesto, Manifiesto
import exif
from utils import OrganizerLogger


class _Cb:
    def __init__(self):
        self.msgs = []

    def emit(self, msg=None, *a, **k):
        self.msgs.append(msg)


def _errores(cb):
    return [m for m in cb.msgs if isinstance(m, str) and "ERROR" in m]


def _vuelo(raiz, tipo, sufijo, make_dji_jpeg):
    carpeta = raiz / tipo / "PB1" / "PB1_V1"
    carpeta.mkdir(parents=True)
    for i in range(1, 3):
        make_dji_jpeg(str(carpeta / f"20260115_10000{i}_DJI_000{i}_{sufijo}.JPG"), lat=37.1, lon=-5.6,
                      gimbal_yaw=0.0, gimbal_pitch=-90.0, relative_altitude=50.0,
                      dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))
    (raiz / "CSVs").mkdir()


def _ml(tmp_path):
    ml = exif.MetaLocation(OrganizerLogger("t_una_carpeta", log_dir=str(tmp_path / "Logs"), create_file_handler=False))
    ml.total_images_number = 2
    return ml


def test_iterate_solo_rgb_escribe_location(tmp_path, make_dji_jpeg):
    raiz = tmp_path / "d"
    _vuelo(raiz, "RGB", "W", make_dji_jpeg)
    cb = _Cb()
    assert _ml(tmp_path).check_input_folder_and_iterate(str(raiz), cb, cb, str(raiz / "CSVs"), 50.0, False) is True
    assert (raiz / "RGB" / "PB1" / "PB1_V1" / "PB1_V1_location.csv").exists()
    assert not _errores(cb)


def test_iterate_solo_termica_escribe_meta(tmp_path, make_dji_jpeg):
    raiz = tmp_path / "d"
    _vuelo(raiz, "TERMICA", "T", make_dji_jpeg)
    cb = _Cb()
    assert _ml(tmp_path).check_input_folder_and_iterate(str(raiz), cb, cb, str(raiz / "CSVs"), 50.0, False) is True
    assert (raiz / "TERMICA" / "PB1" / "PB1_V1" / "PB1_V1_meta.csv").exists()
    assert not _errores(cb)


def test_iterate_ninguna_da_error_y_no_escribe(tmp_path):
    raiz = tmp_path / "d"
    (raiz / "CSVs").mkdir(parents=True)
    cb = _Cb()
    assert _ml(tmp_path).check_input_folder_and_iterate(str(raiz), cb, cb, str(raiz / "CSVs"), 50.0, False) is False
    assert _errores(cb) and not list((raiz / "CSVs").iterdir())


def _manifiesto(raiz, tipo, sufijo):
    m = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    m.crear_esquema()
    filas = []
    for i in range(1, 3):
        nombre = f"20260115_10000{i}_DJI_000{i}_{sufijo}.JPG"
        ruta = str(raiz / tipo / "PB1" / "PB1_V1" / nombre)
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None, pb="1", vuelo="1",
            nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None, comprime=False, ruta_salida_original=ruta,
            ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta)))
    m.insertar_o_reabrir(filas)
    for fila in m.todas():
        m.marcar_hecha(fila["id"], "")
    return m


def _cfg(raiz):
    return SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                           calculate_proyected_distance=False, gen_meta_location=True)


def test_cierre_solo_rgb_y_solo_termica(tmp_path, make_dji_jpeg):
    for tipo, suf, csv in (("RGB", "W", "PB1_V1_location.csv"), ("TERMICA", "T", "PB1_V1_meta.csv")):
        raiz = tmp_path / tipo
        _vuelo(raiz, tipo, suf, make_dji_jpeg)
        cb = _Cb()
        cierre._emitir_meta_location(_manifiesto(raiz, tipo, suf), _cfg(raiz), cb, {})
        assert (raiz / tipo / "PB1" / "PB1_V1" / csv).exists()
        assert not _errores(cb)


def test_cierre_ninguna_da_error_y_no_escribe(tmp_path):
    raiz = tmp_path / "d"
    (raiz / "CSVs").mkdir(parents=True)
    m = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    m.crear_esquema()
    cb = _Cb()
    assert cierre._emitir_meta_location(m, _cfg(raiz), cb, {}) == {}
    assert _errores(cb) and not list((raiz / "CSVs").iterdir())
