"""`resumen.json` del índice: contenido y paridad N=1 / reduce multi-task."""
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from atom_core import indice_mapreduce as mr
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)
from tests.test_indice_mapreduce import (  # noqa: F401 (fixtures y ayudas)
    EJEC, _cfg_meta, _correr, _indice_n, _origen,
)
from tests.test_organizado_plan_apply_e2e import (  # noqa: F401 (fixtures)
    _HostDePrueba, _SignalFalsa, _cfg, _fake_convert_dji_image_to_tif,
    _fake_run_exif_batch_local,
)


def _leer(destino, ejecucion=EJEC):
    with open(mr.ruta_resumen(destino, ejecucion), encoding="utf-8") as fh:
        return json.load(fh)


def test_construir_resumen_sintetico():
    base = "/o/"
    imagenes = sorted(f"{base}{n}" for n in (
        "A/DJI_20260115100000_0001_T.JPG", "A/DJI_20260115100000_0001_W.JPG",
        "A/DJI_20260115110000_0002_T.JPG", "B/DJI_20260115120000_0001_T.JPG",
        "B/DJI_20260115120000_0001_W.JPG", "B/fuera1.JPG", "B/fuera2.JPG"))
    asignadas = [
        (f"{base}A/DJI_20260115100000_0001_T.JPG", "1", "1"),
        (f"{base}A/DJI_20260115100000_0001_W.JPG", "1", "1"),
        (f"{base}A/DJI_20260115110000_0002_T.JPG", "1", "2"),
        (f"{base}B/DJI_20260115120000_0001_T.JPG", "2", "1"),
        (f"{base}B/DJI_20260115120000_0001_W.JPG", "2", "1"),
        (f"{base}B/fuera1.JPG", None, None),
        (f"{base}B/fuera2.JPG", None, None),
    ]
    fuera = [(f"{base}B/fuera1.JPG", "sin-fecha-exif"), (f"{base}B/fuera2.JPG", "fuera-de-ventana")]
    r = mr.construir_resumen_indice(imagenes, asignadas, fuera, lambda x: x[len(base):])
    assert r["hash_listado"] == mr.hash_listado(imagenes)
    assert (r["n_imagenes"], r["n_pares"], r["n_pb"], r["n_vuelos"], r["n_sin_ordenar"]) == (7, 2, 2, 3, 2)
    assert r["pb"] == [
        {"pb": "1", "vuelo": "1", "imagenes": 2, "pares": 1},
        {"pb": "1", "vuelo": "2", "imagenes": 1, "pares": 0},
        {"pb": "2", "vuelo": "1", "imagenes": 2, "pares": 1}]
    assert r["fuera_de_orden"] == [
        {"motivo": "fuera-de-ventana", "n": 1, "ejemplos": ["B/fuera2.JPG"]},
        {"motivo": "sin-fecha-exif", "n": 1, "ejemplos": ["B/fuera1.JPG"]}]


def test_motivo_vacio_es_sin_clasificar_y_ejemplos_max_5():
    imagenes = [f"/o/f{i}.JPG" for i in range(8)]
    r = mr.construir_resumen_indice(
        imagenes, [(x, None, None) for x in imagenes], [(x, None) for x in imagenes])
    assert r["fuera_de_orden"] == [
        {"motivo": "sin-clasificar", "n": 8, "ejemplos": imagenes[:5]}]


def test_escribir_resumen_local_exige_ejecucion(tmp_path):
    ruta = mr.escribir_resumen_indice(str(tmp_path), "e9", {"n_imagenes": 0})
    assert ruta == mr.ruta_resumen(str(tmp_path), "e9")
    datos = json.loads(open(ruta, encoding="utf-8").read())
    assert datos["version"] == 1 and datos["ejecucion_indice"] == "e9"
    with pytest.raises(mr.ErrorIndiceParcial):
        mr.escribir_resumen_indice(str(tmp_path), " ", {})


def test_resumen_n1_y_reduce_son_iguales(_origen, logger, monkeypatch):
    cfg1 = _cfg_meta(_origen, output_folder=str(_origen / "salida1"))
    cfg7 = _cfg_meta(_origen, output_folder=str(_origen / "salida7"))
    _correr(logger, monkeypatch, cfg1, "indice", 0, 1)
    _indice_n(logger, monkeypatch, cfg7, 7, orden=[6, 5, 4, 3, 2, 1, 0])
    uno, siete = _leer(cfg1.output_folder), _leer(cfg7.output_folder)
    assert uno == siete
    assert uno["n_sin_ordenar"] == 2
    assert uno["n_pb"] == 2 and uno["n_vuelos"] == 3
    assert sum(e["n"] for e in uno["fuera_de_orden"]) >= 2
    assert uno["ejecucion_indice"] == EJEC
    imagenes = mr_listado(cfg1)
    assert uno["hash_listado"] == mr.hash_listado(imagenes)
    assert uno["n_imagenes"] == len(imagenes)
    assert all(len(e["ejemplos"]) <= 5 and not os.path.isabs(x)
               for e in uno["fuera_de_orden"] for x in e["ejemplos"])


def mr_listado(cfg):
    from atom_core import indice as indice_mod
    return indice_mod._listar_imagenes(cfg.input_folder)


class _Cb:
    def __init__(self):
        self.msgs = []

    def emit(self, msg):
        self.msgs.append(msg)


def _dato(ruta, ts):
    return SimpleNamespace(ruta=ruta, timestamp=ts)


def _resumen_sintetico(tmp_path, asignaciones, dias_sin_estadillo=(), no_dji=(),
                       omitidas=(), no_disponibles=(), estadillo_sin_fecha=False):
    # una fila por imagen sin ventana, como en el índice real (pb/vuelo None = SIN_ORDENAR)
    filas = [SimpleNamespace(ruta_origen=d.ruta, pb=None, vuelo=None)
             for d, v in asignaciones if v is None]
    from atom_core import indice
    cfg = SimpleNamespace(input_folder="/o", output_folder=str(tmp_path))
    imagenes = sorted({d.ruta for d, _v in asignaciones} | {r for r, _m in no_dji}
                      | set(omitidas) | set(no_disponibles))
    indice._escribir_resumen(
        cfg, "e1", imagenes, asignaciones, filas, list(dias_sin_estadillo), list(no_dji),
        list(omitidas), list(no_disponibles), _Cb(), estadillo_sin_fecha=estadillo_sin_fecha)
    return _leer(str(tmp_path), "e1")


def test_escribir_resumen_clasifica_motivos(tmp_path):
    import datetime as dt
    asig = [
        (_dato("/o/a.JPG", None), None),
        (_dato("/o/b.JPG", dt.datetime(2026, 1, 16, 10)), None),
        (_dato("/o/c.JPG", dt.datetime(2026, 1, 15, 10)), None),
    ]
    r = _resumen_sintetico(tmp_path, asig, dias_sin_estadillo=[("2026-01-16", 1)])
    assert r["fuera_de_orden"] == [
        {"motivo": "dia-sin-estadillo", "n": 1, "ejemplos": ["b.JPG"]},
        {"motivo": "fuera-de-ventana", "n": 1, "ejemplos": ["c.JPG"]},
        {"motivo": "sin-fecha-exif", "n": 1, "ejemplos": ["a.JPG"]}]
    assert r["n_sin_ordenar"] == sum(e["n"] for e in r["fuera_de_orden"]) == 3


def test_escribir_resumen_estadillo_sin_fecha_legible_y_no_dji(tmp_path):
    import datetime as dt
    asig = [(_dato("/o/c.JPG", dt.datetime(2026, 1, 15, 10)), None)]
    r = _resumen_sintetico(tmp_path, asig, no_dji=[("/o/x.txt", "ext raro")],
                           estadillo_sin_fecha=True)
    assert r["fuera_de_orden"] == [
        {"motivo": "estadillo-sin-fecha-legible", "n": 1, "ejemplos": ["c.JPG"]},
        {"motivo": "no-dji", "n": 1, "ejemplos": ["x.txt"]}]


def test_escribir_resumen_sin_ejecucion_gcs_falla(tmp_path):
    from atom_core import indice
    cfg = SimpleNamespace(input_folder="/o", output_folder="gs://b/dest")
    with pytest.raises(mr.ErrorIndiceParcial):
        indice._escribir_resumen(cfg, "", [], [], [], [], [], [], [], _Cb())
