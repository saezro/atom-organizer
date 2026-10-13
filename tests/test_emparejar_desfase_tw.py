"""Emparejado T/W con desfase de índice DJI (W=n / T=n+1, mismo idx, W=n+1 / T=n) y 0 pares = error visible."""
import exif
from utils import OrganizerLogger


class _Cb:
    def __init__(self):
        self.textos = []

    def emit(self, t):
        self.textos.append(t)


def _obj(tmp_path, nombre):
    o = exif.MetaLocation(OrganizerLogger(nombre, log_dir=str(tmp_path), create_file_handler=False))
    o.stop = False
    return o


def _t(i, seg=0):
    return f"DJI_202601151000{seg:02d}_{i:04d}_T.JPG"


def _w(i, seg=0):
    return f"DJI_202601151000{seg:02d}_{i:04d}_W.JPG"


def test_desfase_w_n_t_n_mas_1(tmp_path):
    o = _obj(tmp_path, "d1")
    # W=270,272 ; T=271,273 (cada disparo en su segundo)
    t = [_t(271, 1), _t(273, 3)]
    w = [_w(270, 1), _w(272, 3)]
    pares, sp, np_ = o.emparejar_por_idx(t, w)
    assert pares == [(t[0], w[0]), (t[1], w[1])] and sp == [] and np_ == []
    assert o.desfase_tw == -1


def test_desfase_t_n_w_n_mas_1(tmp_path):
    o = _obj(tmp_path, "d2")
    t = [_t(270, 1), _t(272, 3)]
    w = [_w(271, 1), _w(273, 3)]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert pares == [(t[0], w[0]), (t[1], w[1])] and sp == []
    assert o.desfase_tw == 1


def test_mismo_indice(tmp_path):
    o = _obj(tmp_path, "d0")
    t = ["20260115_100000_DJI_0001_T.JPG", "20260115_100005_DJI_0002_T.JPG"]
    w = ["20260115_100000_DJI_0001_W.JPG", "20260115_100005_DJI_0002_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert pares == list(zip(t, w)) and sp == [] and o.desfase_tw == 0


def test_sin_timestamp_prueba_candidatos_y_w_una_vez(tmp_path):
    o = _obj(tmp_path, "dn")
    t = ["DJI_0011_T.JPG", "DJI_0013_T.JPG", "DJI_0015_T.JPG"]
    w = ["DJI_0010_W.JPG", "DJI_0012_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert pares == [(t[0], w[0]), (t[1], w[1])]
    assert sp == [t[2]] and o.desfase_tw == -1


def test_sin_pares_es_error_visible(tmp_path):
    o = _obj(tmp_path, "de")
    cb = _Cb()
    pares, _, _ = o.emparejar_por_idx(["DJI_0001_T.JPG"], ["DJI_0050_W.JPG"])
    assert pares == []
    o._aviso_sin_pares(cb, "vuelo", 1)
    assert o.error_meta_location == 1 and any("ERROR" in x for x in cb.textos)


def test_emparejado_sin_pares_cuenta_error(tmp_path, monkeypatch):
    o = _obj(tmp_path, "dg")
    ct, cw = tmp_path / "T", tmp_path / "W"
    ct.mkdir(); cw.mkdir()
    (ct / "DJI_0001_T.JPG").write_bytes(b"x")
    (cw / "DJI_0050_W.JPG").write_bytes(b"x")
    cb = _Cb()
    o.gen_meta_location_emparejado(str(ct), str(cw), cb, _Cb(), str(tmp_path), 0.0, False)
    assert o.error_meta_location >= 1 and any("ningún par" in x for x in cb.textos)


def test_salto_contador_999_a_1_con_timestamp(tmp_path):
    o = _obj(tmp_path, "s1")
    t, w = [], []
    for wi, ti, seg in [(997, 998, 10), (999, 1, 17), (2, 3, 24), (4, 5, 31)]:
        t.append(f"20261006_1350{seg}_DJI_{ti:04d}_T.JPG")
        w.append(f"20261006_1350{seg}_DJI_{wi:04d}_W.JPG")
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert sorted(pares) == sorted(zip(t, w)) and sp == []


def test_salto_contador_999_a_1_sin_timestamp(tmp_path):
    o = _obj(tmp_path, "s2")
    # desfase -1 (W=T-1); W0999 / T0001 cruzan el reinicio del contador
    t = ["DJI_0997_T.JPG", "DJI_0999_T.JPG", "DJI_0001_T.JPG", "DJI_0003_T.JPG"]
    w = ["DJI_0996_W.JPG", "DJI_0998_W.JPG", "DJI_0999_W.JPG", "DJI_0002_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert sorted(pares) == sorted(zip(t, w)) and sp == []
