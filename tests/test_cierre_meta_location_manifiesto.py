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


def test_carpeta_real_no_recompuesta_por_include_v(tmp_path, make_dji_jpeg, logger):
    """F5: la carpeta del CSV sale de `ruta_salida_original` (la real), no de
    recomponer `PB{pb}_{vuelo}`/`PB{pb}_V{vuelo}` con el `include_v` del run
    ACTUAL. El árbol en disco usa `PB1_V1` (include_v=True); si el cierre
    corre con `include_v=False` la carpeta recompuesta sería `PB1_1`
    (inexistente) y el CSV no podría escribirse."""
    raiz = tmp_path / "d"
    _arbol(raiz, make_dji_jpeg)
    manifiesto = _manifiesto(raiz, logger, con_metadatos=False)
    cfg = _cfg(raiz, calcular=False)
    cfg.include_v = False  # distinto del árbol real, a propósito

    rutas_emitidas = cierre._emitir_meta_location(manifiesto, cfg, _Cb(), {})

    assert "meta_location" in rutas_emitidas
    assert (raiz / "RGB" / "PB1" / "PB1_V1" / "PB1_V1_location.csv").exists()
    assert (raiz / "TERMICA" / "PB1" / "PB1_V1" / "PB1_V1_meta.csv").exists()


def test_error_en_un_vuelo_no_tumba_los_demas(tmp_path, make_dji_jpeg, logger, monkeypatch):
    """F5: un vuelo roto se avisa y se salta; el resto de vuelos sigue
    generando su meta/location con normalidad."""
    raiz = tmp_path / "d"
    _arbol(raiz, make_dji_jpeg)  # PB1/V1

    # Segundo vuelo (PB2/V1) con las mismas imágenes RGB+TERMICA.
    filas_extra = []
    for tipo, nombre, lat, lon, yaw, pitch, rel in _IMAGENES:
        if tipo == "RGB_Extra":
            continue
        carpeta = raiz / tipo / "PB2" / "PB2_V1"
        carpeta.mkdir(parents=True, exist_ok=True)
        nombre2 = nombre.replace("_100", "_110")
        ruta = str(carpeta / nombre2)
        make_dji_jpeg(ruta, lat=lat, lon=lon, gimbal_yaw=yaw, gimbal_pitch=pitch,
                      relative_altitude=rel, dt_val=dt.datetime(2026, 1, 15, 11, 0, 0))
        filas_extra.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre2}", tipo=tipo, timestamp_exif=None, modelo=None,
            pb="2", vuelo="1", nombre_nuevo=nombre2, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=ruta, ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta)))

    manifiesto = _manifiesto(raiz, logger, con_metadatos=False)
    manifiesto.insertar_o_reabrir(filas_extra)
    for fila in manifiesto.todas():
        if fila["estado"] != "hecho":
            manifiesto.marcar_hecha(fila["id"], "")

    avisos = []

    class _CbAviso:
        def emit(self, msg, *a, **k):
            avisos.append(msg)

    original_publicar = exif.MetaLocation.publicar_csv

    def publicar_roto(self, df, input_folder, *a, **k):
        if "PB2" in str(input_folder):
            raise RuntimeError("disco roto")
        return original_publicar(self, df, input_folder, *a, **k)

    monkeypatch.setattr(exif.MetaLocation, "publicar_csv", publicar_roto)

    cierre._emitir_meta_location(manifiesto, _cfg(raiz, False), _CbAviso(), {})

    assert any("disco roto" in a for a in avisos if isinstance(a, str)), avisos
    # El vuelo bueno (PB1/V1) tiene que haber salido pese al error en PB2/V1.
    assert (raiz / "RGB" / "PB1" / "PB1_V1" / "PB1_V1_location.csv").exists()
    assert (raiz / "TERMICA" / "PB1" / "PB1_V1" / "PB1_V1_meta.csv").exists()
