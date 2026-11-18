"""Desfase idx W-T por carpeta (DJI incrementa el contador por fichero)."""
import datetime as dt

from atom_core.indice import _MetadatosImagen, _heredar_vuelo_por_pareja
from atom_core.pares import desfase_por_carpeta


def _d(nombre, ts, carpeta="/sd/100MEDIA"):
    return _MetadatosImagen(ruta=f"{carpeta}/{nombre}", nombre=nombre, timestamp=ts,
                            modelo=None, yaw=None, gps=None)


class _Cb:
    def __init__(self):
        self.msgs = []

    def emit(self, m):
        self.msgs.append(m)


T0 = dt.datetime(2026, 3, 1, 10, 0, 0)


def test_desfase_funcion():
    t = [("/c", 696, T0)]
    w = [("/c", 695, T0)]
    assert desfase_por_carpeta(t, w) == {"/c": -1}
    assert desfase_por_carpeta([("/c", 5, None)], [("/c", 6, None)]) == {"/c": 1}
    assert desfase_por_carpeta([("/c", 5, T0)], [("/c", 5, T0)]) == {"/c": 0}


def test_dji_w695_t696_sin_aviso_y_hereda():
    v1 = {"pb": "1", "vuelo": "1"}
    w = _d("DJI_20260301100000_0695_W.JPG", T0)
    t = _d("DJI_20260301100000_0696_T.JPG", T0)
    cb = _Cb()
    res = _heredar_vuelo_por_pareja([(t, v1), (w, None)], cb)
    assert res[1][1] is v1
    assert not any("sin su RGB" in m for m in cb.msgs)


def test_w_sin_t_no_avisa_de_t():
    cb = _Cb()
    w = _d("DJI_20260301100000_0999_W.JPG", T0)
    _heredar_vuelo_por_pareja([(w, None)], cb)
    assert not any("térmicas" in m for m in cb.msgs)


def test_t_realmente_sin_w_avisa():
    cb = _Cb()
    t = _d("DJI_20260301100000_0001_T.JPG", T0)
    _heredar_vuelo_por_pareja([(t, None)], cb)
    assert any("1 térmicas (T) sin su RGB" in m for m in cb.msgs)


def test_d0_sigue_funcionando():
    v1 = {"pb": "1", "vuelo": "1"}
    t = _d("DJI_20260301100000_0007_T.JPG", T0)
    w = _d("DJI_20260301100001_0007_W.JPG", T0 + dt.timedelta(seconds=1))
    cb = _Cb()
    res = _heredar_vuelo_por_pareja([(t, v1), (w, None)], cb)
    assert res[1][1] is v1
    assert not cb.msgs or not any("sin su RGB" in m for m in cb.msgs)
