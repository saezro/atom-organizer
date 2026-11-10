"""Apply (RGB y térmicas) y cierre con origen/destino `gs://` (Cloud Run).

Almacén GCS falso en memoria (el de `test_giro_manifiesto_gcs`): nada de red.
Lo que se sujeta: el origen gs:// se baja a local, se procesa en local y el
resultado se PUBLICA vía `almacen` (nunca `os.replace`/`os.makedirs` sobre una
ruta `gs://`, que crearía un árbol local `gs:/…`); sin `.parcial` en el bucket;
el R-JPEG térmico llega byte a byte (nunca se gira).
"""
import datetime as dt
import os
from types import SimpleNamespace

import pytest

import exif
from atom_core import apply, cierre, indice
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from tests.test_apply_rgb import (
    _SignalFalsa, _cfg, _fila_manifiesto, _pipeline_doble,
)
from tests.test_apply_termicas import (
    MARCA_EXIF, _PipelineDePrueba, _PoolExiftoolFalso, _fila_termica,
)
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)

RAIZ = "gs://bucket-falso/PLANTA/INSP"


@pytest.fixture(autouse=True)
def _tmp_del_fixture_bucket(tmp_path):
    # `bucket` redirige gettempdir() a tmp_path/tmp: que exista.
    (tmp_path / "tmp").mkdir(exist_ok=True)


def _clave(uri):
    return uri[len("gs://bucket-falso/"):]


def _poner(bucket, uri_o_clave, datos):
    clave = _clave(uri_o_clave) if uri_o_clave.startswith("gs://") else uri_o_clave
    bucket.objetos[clave] = datos
    bucket.generaciones[clave] = bucket.generaciones.get(clave, 0) + 1


def _manifiesto(tmp_path, filas):
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_muchas(filas)
    return m


def _sin_dir_gs_local():
    assert not os.path.exists("gs:") and not os.path.exists("gs:/bucket-falso")


def _sin_parciales(bucket):
    assert [k for k in bucket.objetos if ".parcial" in k] == []


@pytest.mark.parametrize("origen_remoto,destino_remoto", [(True, True), (True, False), (False, True)])
def test_apply_rgb_gcs_publica_original_y_crop(bucket, tmp_path, make_dji_jpeg, monkeypatch,
                                                origen_remoto, destino_remoto):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "DJI_0001.JPG"
    make_dji_jpeg(str(local))
    datos = local.read_bytes()
    if origen_remoto:
        origen = RAIZ + "/SD/DJI_0001.JPG"
        _poner(bucket, origen, datos)
    else:
        origen = str(local)
    base = RAIZ + "/RGB/PB1/PB1_V1" if destino_remoto else str(tmp_path / "salida")
    salida = base + "/20260501_100000_DJI_0001.JPG"
    crop = base + "/20260501_100000_DJI_0001_CROP.JPG"
    manifiesto = _manifiesto(tmp_path, [_fila_manifiesto(origen, salida, crop, angulo_giro=90, pct_recorte=0.8)])

    resultado = apply.aplicar_rgb(manifiesto, _cfg(), _pipeline_doble(), _SignalFalsa(),
                                  _SignalFalsa(), _SignalFalsa())

    assert resultado == {"hecho": 1, "fallido": 0}, manifiesto.todas()[0]["motivo_fallo"]
    fila = manifiesto.todas()[0]
    assert fila["estado"] == "hecho"
    if destino_remoto:
        assert len(bucket.objetos[_clave(salida)]) > 0
        assert len(bucket.objetos[_clave(crop)]) > 0
        assert f"{salida}:{len(bucket.objetos[_clave(salida)])}" in fila["verificacion"]
    else:
        assert os.path.getsize(salida) > 0 and os.path.getsize(crop) > 0
    _sin_parciales(bucket)
    _sin_dir_gs_local()
    manifiesto.cerrar()


def test_apply_rgb_gcs_fallo_no_deja_parcial_ni_objeto(bucket, tmp_path, make_dji_jpeg, monkeypatch):
    monkeypatch.chdir(tmp_path)
    origen = RAIZ + "/SD/NO_JPG.JPG"
    _poner(bucket, origen, b"no soy un jpeg" * 50)
    salida = RAIZ + "/RGB/PB1/PB1_V1/x.JPG"
    manifiesto = _manifiesto(tmp_path, [_fila_manifiesto(origen, salida)])
    resultado = apply.aplicar_rgb(manifiesto, _cfg(), _pipeline_doble(), _SignalFalsa(),
                                  _SignalFalsa(), _SignalFalsa())
    assert resultado == {"hecho": 0, "fallido": 1}
    assert _clave(salida) not in bucket.objetos
    _sin_parciales(bucket)
    _sin_dir_gs_local()
    manifiesto.cerrar()


def test_apply_termicas_gcs_publica_tiff_y_jpg_sin_girar(bucket, tmp_path, make_dji_jpeg,
                                                          monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(apply, "_PoolExiftool", _PoolExiftoolFalso)
    local = tmp_path / "DJI_0001_T.JPG"
    make_dji_jpeg(str(local))
    datos = local.read_bytes()
    origen = RAIZ + "/SD/DJI_0001_T.JPG"
    _poner(bucket, origen, datos)
    jpg = RAIZ + "/TERMICA/PB1/PB1_V1/DJI_0001_T.JPG"
    tiff = RAIZ + "/TERMICA/PB1/PB1_V1/DJI_0001_T.tiff"
    manifiesto = _manifiesto(tmp_path, [_fila_termica(origen, jpg, tiff, angulo_giro=90)])

    resultado = apply.aplicar_termicas(manifiesto, _cfg(), _PipelineDePrueba(), _SignalFalsa(),
                                       _SignalFalsa(), _SignalFalsa())

    assert resultado == {"hecho": 1, "fallido": 0}, manifiesto.todas()[0]["motivo_fallo"]
    assert bucket.objetos[_clave(jpg)] == datos  # R-JPEG intacto, nunca girado
    assert bucket.objetos[_clave(tiff)].endswith(MARCA_EXIF)
    fila = manifiesto.todas()[0]
    assert f"{tiff}:{len(bucket.objetos[_clave(tiff)])}" in fila["verificacion"]
    _sin_parciales(bucket)
    _sin_dir_gs_local()
    manifiesto.cerrar()


def test_errores_csv_de_la_fase_en_destino_gcs(bucket, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    origen = RAIZ + "/SD/NO_EXISTE.JPG"
    salida = RAIZ + "/RGB/PB1/PB1_V1/x.JPG"
    manifiesto = _manifiesto(tmp_path, [_fila_manifiesto(origen, salida)])
    apply.aplicar_rgb(manifiesto, _cfg(output_folder=RAIZ), _pipeline_doble(), _SignalFalsa(),
                      _SignalFalsa(), _SignalFalsa())
    payload = apply.emitir_errores_fase(manifiesto, _cfg(output_folder=RAIZ), _SignalFalsa(),
                                        "RGB", "RGB", ("RGB",))
    assert payload["n_fallos"] == 1
    assert payload["csv"] and payload["csv"].startswith("gs://"), payload
    assert any(k.endswith("ERRORES_RGB.csv") for k in bucket.objetos)
    _sin_dir_gs_local()
    manifiesto.cerrar()


# ---- cierre ---------------------------------------------------------------

def _cfg_cierre():
    return SimpleNamespace(output_folder=RAIZ, include_v=True, flight_height=50.0,
                           calculate_proyected_distance=False, gen_meta_location=True,
                           convert_to_tif=False, cropping_rgb=False, input_folder="")


def _manifiesto_cierre(tmp_path, bucket, make_dji_jpeg, logger, faltan=()):
    gi = exif.GeneralInformationFromImage(logger)

    class _Cb:
        def emit(self, *a, **k):
            pass

    filas = []
    for i, (tipo, suf) in enumerate((("RGB", "W"), ("TERMICA", "T"))):
        nombre = f"20260115_10000{i}_DJI_0001_{suf}.JPG"
        loc = tmp_path / nombre
        make_dji_jpeg(str(loc), dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))
        extra = indice.campos_posicion(indice._leer_metadatos(str(loc), gi, _Cb()))
        ruta = f"{RAIZ}/{tipo}/PB1/PB1_V1/{nombre}"
        if nombre not in faltan:
            _poner(bucket, ruta, loc.read_bytes())
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None,
            pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=ruta, ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=loc.stat().st_size, **extra))
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_o_reabrir(filas)
    for fila in m.todas():
        m.marcar_hecha(fila["id"], "")
    return m


def test_cierre_csvs_gcs_se_publican_en_el_bucket(bucket, tmp_path, make_dji_jpeg, logger, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = _manifiesto_cierre(tmp_path, bucket, make_dji_jpeg, logger)
    cb = _SignalFalsa()
    cierre.emitir_csvs(m, _cfg_cierre(), cb)
    claves = sorted(bucket.objetos)
    assert any(k.startswith("PLANTA/INSP/CSVs/") and k.endswith("_meta.csv") for k in claves), claves
    assert any(k.startswith("PLANTA/INSP/CSVs/") and k.endswith("_location.csv") for k in claves), claves
    _sin_dir_gs_local()
    m.cerrar()


def test_cierre_verificar_gcs_lista_en_el_bucket(bucket, tmp_path, make_dji_jpeg, logger, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = _manifiesto_cierre(tmp_path, bucket, make_dji_jpeg, logger)
    assert [p for p in cierre.verificar(m, _cfg_cierre()) if "no existe" in p or "vacío" in p] == []
    m.cerrar()


def test_cierre_verificar_gcs_detecta_objeto_ausente(bucket, tmp_path, make_dji_jpeg, logger, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = _manifiesto_cierre(tmp_path, bucket, make_dji_jpeg, logger,
                           faltan=("20260115_100001_DJI_0001_T.JPG",))
    problemas = cierre.verificar(m, _cfg_cierre())
    assert any("no existe" in p and "DJI_0001_T.JPG" in p for p in problemas), problemas
    m.cerrar()
