"""Auditoria ALTO 8: la W hereda el vuelo de su T por idx DJI (borde de ventana)."""
import datetime as dt

from atom_core.indice import _MetadatosImagen, _heredar_vuelo_por_pareja


def _d(nombre, ts):
    return _MetadatosImagen(ruta=f"/sd/100MEDIA/{nombre}", nombre=nombre, timestamp=ts,
                            modelo=None, yaw=None, gps=None)


def test_w_en_borde_hereda_vuelo_de_su_t():
    v1 = {"pb": "1", "vuelo": "1"}
    t = _d("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _d("DJI_20260301100500_0007_W.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    otra = _d("DJI_20260301100500_0008_W.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    res = _heredar_vuelo_por_pareja([(t, v1), (w, None), (otra, None)])
    assert res[1][1] is v1
    assert res[2][1] is None  # sin T con ese idx: no se inventa vuelo


def test_w_de_otra_carpeta_no_hereda_por_idx_repetido():
    v1 = {"pb": "1", "vuelo": "1"}
    t = _d("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _MetadatosImagen(ruta="/sd/101MEDIA/DJI_20260301130000_0007_W.JPG",
                         nombre="DJI_20260301130000_0007_W.JPG", timestamp=None,
                         modelo=None, yaw=None, gps=None)
    assert _heredar_vuelo_por_pareja([(t, v1), (w, None)])[1][1] is None


class _Cb:
    def __init__(self):
        self.lineas = []

    def emit(self, texto):
        self.lineas.append(str(texto))


def test_idx_repetido_en_misma_carpeta_no_hereda():
    v1, v2 = {"pb": "1", "vuelo": "1"}, {"pb": "1", "vuelo": "2"}
    t1 = _d("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    t2 = _d("DJI_20260301150000_0007_T.JPG", dt.datetime(2026, 3, 1, 15, 0, 0))
    w = _d("DJI_20260301150001_0007_W.JPG", dt.datetime(2026, 3, 1, 15, 0, 1))
    cb = _Cb()
    res = _heredar_vuelo_por_pareja([(t1, v1), (t2, v2), (w, None)], cb)
    assert res[2][1] is None
    assert any("AVISO" in l and "NO heredan" in l for l in cb.lineas)


def test_w_a_mas_de_5s_no_hereda():
    v1 = {"pb": "1", "vuelo": "1"}
    t = _d("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _d("DJI_20260301100520_0007_W.JPG", dt.datetime(2026, 3, 1, 10, 5, 20))
    cb = _Cb()
    assert _heredar_vuelo_por_pareja([(t, v1), (w, None)], cb)[1][1] is None
    assert any("AVISO" in l for l in cb.lineas)


def test_t_sin_w_avisa_con_recuento_por_vuelo_y_t_sin_vuelo_w_con():
    v1 = {"pb": "1", "vuelo": "1"}
    t = _d("DJI_20260301100459_0007_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    t_sin_vuelo = _d("DJI_20260301100459_0009_T.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w9 = _d("DJI_20260301100500_0009_W.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    cb = _Cb()
    res = _heredar_vuelo_por_pareja([(t, v1), (t_sin_vuelo, None), (w9, v1)], cb)
    assert res[0][1] is v1 and res[2][1] is v1
    texto = "".join(cb.lineas)
    assert "1 térmicas (T) sin su RGB (W)" in texto and "PB1/V1: 1" in texto
    assert "cuya térmica (T) no tiene vuelo" in texto
