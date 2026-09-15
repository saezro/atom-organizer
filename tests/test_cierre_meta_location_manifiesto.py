"""meta/location desde el manifiesto == meta/location releyendo imágenes.

Se construye el mismo árbol de salida dos veces: en uno corre el camino de
siempre (`MetaLocation.check_input_folder_and_iterate`), en el otro el cierre
nuevo alimentado por filas de manifiesto. Los CSV tienen que ser idénticos
byte a byte."""
import datetime as dt
import os
import shutil
from types import SimpleNamespace

import pytest

import exif
from atom_core import cierre, indice
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import OrganizerLogger


class _Cb:
    def emit(self, *a, **k):
        pass


# (tipo, nombre, lat, lon, yaw, pitch, rel_alt)
_IMAGENES = [
    ("RGB", "20260115_100005_DJI_0002_D.JPG", 37.10001, -5.60001, 12.5, -90.0, 50.0),
    ("RGB", "20260115_100000_DJI_0001_D.JPG", 37.10000, -5.60000, 0.0, -45.0, 50.0),
    ("RGB", "20260115_100010_DJI_0003_D.JPG", 37.10002, -5.60002, 30.0, -60.0, 51.5),
    ("TERMICA", "20260115_100000_DJI_0001_T.JPG", 37.10000, -5.60000, -88.1, -90.0, 50.0),
    ("TERMICA", "20260115_100005_DJI_0002_T.JPG", 37.10001, -5.60001, 91.3, -30.0, 49.0),
    ("RGB_Extra", "20260115_100000_DJI_0001_W.JPG", 37.10000, -5.60000, 5.0, -90.0, 50.0),
]


def _arbol(raiz, make_dji_jpeg):
    for tipo, nombre, lat, lon, yaw, pitch, rel in _IMAGENES:
        carpeta = raiz / tipo / "PB1" / "PB1_V1"
        carpeta.mkdir(parents=True, exist_ok=True)
        make_dji_jpeg(str(carpeta / nombre), lat=lat, lon=lon, gimbal_yaw=yaw,
                      gimbal_pitch=pitch, relative_altitude=rel,
                      dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))
    (raiz / "CSVs").mkdir()


def _cfg(raiz, calcular):
    return SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                           calculate_proyected_distance=calcular, gen_meta_location=True)


def _manifiesto(raiz, logger, con_metadatos):
    manifiesto = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    manifiesto.crear_esquema()
    gi = exif.GeneralInformationFromImage(logger)
    filas = []
    for tipo, nombre, *_ in _IMAGENES:
        ruta = str(raiz / tipo / "PB1" / "PB1_V1" / nombre)
        extra = indice.campos_posicion(indice._leer_metadatos(ruta, gi, _Cb())) if con_metadatos else {}
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None,
            pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=ruta, ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta), **extra))
    manifiesto.insertar_o_reabrir(filas)
    for fila in manifiesto.todas():
        manifiesto.marcar_hecha(fila["id"], "")
    return manifiesto


def _csvs(raiz):
    return {os.path.relpath(os.path.join(d, f), raiz): open(os.path.join(d, f), "rb").read()
            for d, _s, fs in os.walk(raiz) if ".organizado" not in d
            for f in fs if f.endswith(".csv")}


@pytest.mark.parametrize("calcular", [True, False])
@pytest.mark.parametrize("con_metadatos", [True, False])
def test_csv_desde_manifiesto_identico_al_actual(tmp_path, make_dji_jpeg, logger, calcular, con_metadatos):
    ref, nuevo = tmp_path / "ref", tmp_path / "nuevo"
    _arbol(ref, make_dji_jpeg)
    shutil.copytree(ref, nuevo)

    ml = exif.MetaLocation(OrganizerLogger("t", create_file_handler=False))
    ml.total_images_number = len(_IMAGENES)
    assert ml.check_input_folder_and_iterate(str(ref), _Cb(), _Cb(), str(ref / "CSVs"), 50.0, calcular)

    manifiesto = _manifiesto(nuevo, logger, con_metadatos)
    proyecciones = {}
    cierre._emitir_meta_location(manifiesto, _cfg(nuevo, calcular), _Cb(), proyecciones)

    esperado = _csvs(ref)
    assert esperado, "el camino de referencia no generó CSV: test mal montado"
    assert _csvs(nuevo) == esperado
    if calcular:
        assert len(proyecciones) == len(_IMAGENES)


def test_cierre_no_relee_imagenes_con_metadatos(tmp_path, make_dji_jpeg, logger, monkeypatch):
    raiz = tmp_path / "d"
    _arbol(raiz, make_dji_jpeg)
    manifiesto = _manifiesto(raiz, logger, con_metadatos=True)
    monkeypatch.setattr(exif.MetaLocation, "leer_exif_imagen",
                        lambda *a, **k: pytest.fail("releyó una imagen con meta_leida=1"))
    cierre._emitir_meta_location(manifiesto, _cfg(raiz, True), _Cb(), {})
