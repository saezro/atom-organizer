"""`resumen.json` del índice: contenido y paridad N=1 / reduce multi-task."""
import hashlib
import json
import os

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
    try:
        mr.escribir_resumen_indice(str(tmp_path), " ", {})
    except mr.ErrorIndiceParcial:
        pass
    else:
        raise AssertionError("debía fallar sin ejecución")


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
