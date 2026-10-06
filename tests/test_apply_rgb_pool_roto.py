"""Un worker RGB que muere (BrokenProcessPool) no debe tumbar las demás filas:
el pool se recrea con la mitad de workers y solo se marca fallida la imagen
que mata al proceso incluso estando sola."""
import multiprocessing
import os
import sys

import pytest

from atom_core import apply
from atom_core.manifiesto import Manifiesto
from tests.test_apply_rgb import _ControladorFalso, _SignalFalsa, _cfg, _fila_manifiesto  # noqa: F401

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="usa fork para que el worker de test sea importable")

VENENO = "veneno"


def _trabajo_que_muere(fila, cfg):
    if VENENO in fila["ruta_origen"]:
        os._exit(1)
    return "ok:" + fila["ruta_origen"]


def _montar(tmp_path, nombres, make_dji_jpeg):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    filas = []
    for nombre in nombres:
        origen = tmp_path / "origen" / nombre
        origen.parent.mkdir(exist_ok=True)
        make_dji_jpeg(str(origen))
        filas.append(_fila_manifiesto(origen, tmp_path / "salida" / nombre))
    manifiesto.insertar_muchas(filas)
    return manifiesto


def _estados(manifiesto):
    conexion = manifiesto._conexion()
    return {os.path.basename(r[0]): r[1] for r in conexion.execute(
        "SELECT ruta_origen, estado FROM imagenes")}


@pytest.mark.parametrize("workers", [1, 3])
def test_worker_muerto_solo_falla_la_culpable(tmp_path, monkeypatch, workers, make_dji_jpeg):
    monkeypatch.setenv("ATOM_MP_START", "fork")
    monkeypatch.setattr(apply, "_trabajo_fila", _trabajo_que_muere)
    nombres = [f"DJI_{i:04d}.JPG" for i in range(8)] + [f"DJI_{VENENO}.JPG"]
    manifiesto = _montar(tmp_path, nombres, make_dji_jpeg)
    rondas = []
    original = apply._kwargs_pool_rgb
    monkeypatch.setattr(apply, "_kwargs_pool_rgb",
                        lambda w: rondas.append(w) or original(w))
    msgs = _SignalFalsa()

    res = apply.aplicar_rgb(manifiesto, _cfg(), None, msgs, _SignalFalsa(), _SignalFalsa(),
                            controlador=_ControladorFalso(trabajadores=workers))

    assert res == {"hecho": 8, "fallido": 1}
    est = _estados(manifiesto)
    assert est[f"DJI_{VENENO}.JPG"] == "fallido"
    assert sum(1 for v in est.values() if v == "hecho") == 8
    # Acotado: <=3 recreaciones multi-worker + rondas de 1 (una por culpable) + 1.
    assert len(rondas) <= 6
    if workers > 1:
        assert rondas[0] == workers and rondas[-1] == 1
        assert any("recreación" in str(m) for m in msgs.mensajes)
