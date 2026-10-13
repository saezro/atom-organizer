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


def test_orden_cronologico_con_reinicio_contador(tmp_path):
    o = _obj(tmp_path, "ord1")
    def n(hms, i, s):
        return f"20261005_{hms}_DJI_{i:04d}_{s}.JPG"
    datos = [("130009", 905), ("130011", 907), ("130217", 2), ("130439", 70)]
    t = [n(h, i, "T") for h, i in datos]
    w = [n(h, i - 1, "W") for h, i in datos]
    pares, sp, _ = o.emparejar_por_idx(list(reversed(t)), list(reversed(w)))
    assert [p[0] for p in pares] == t and sp == []
    assert [p[1] for p in pares] == w


def test_orden_sin_reinicio_no_cambia(tmp_path):
    o = _obj(tmp_path, "ord2")
    t = [f"20261005_1300{s:02d}_DJI_{i:04d}_T.JPG" for i, s in ((1, 0), (2, 5), (3, 9))]
    w = [x.replace("_T.", "_W.") for x in t]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert [p[0] for p in pares] == t and sp == []


def test_rollover_a_caballo_no_cruza(tmp_path):
    o = _obj(tmp_path, "ord3")
    t = ["20261005_130000_DJI_1000_T.JPG", "20261005_130500_DJI_0002_T.JPG"]
    w = ["20261005_130000_DJI_0999_W.JPG", "20261005_130500_DJI_0001_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert pares == list(zip(t, w)) and sp == []
    # mismo hueco de índice pero timestamps >2 s distintos: no se cruzan
    pares, sp, _ = o.emparejar_por_idx([t[1]], [w[0]])
    assert pares == [] and sorted(sp) == sorted([t[1], w[0]])


def test_huerfanas_t_y_w(tmp_path):
    o = _obj(tmp_path, "ord4")
    t = ["20261005_130000_DJI_0002_T.JPG", "20261005_130100_DJI_0004_T.JPG"]
    w = ["20261005_130000_DJI_0001_W.JPG", "20261005_130200_DJI_0007_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert pares == [(t[0], w[0])] and sorted(sp) == sorted([t[1], w[1]])


def test_dos_t_misma_w_solo_una(tmp_path):
    o = _obj(tmp_path, "ord5")
    t = ["20261005_130000_DJI_0002_T.JPG", "20261005_130001_DJI_0004_T.JPG"]
    w = ["20261005_130000_DJI_0001_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert pares == [(t[0], w[0])] and sp == [t[1]]


def test_jitter_cadencia_corta_tras_rollover(tmp_path):
    import datetime as dt
    o = _obj(tmp_path, "ord6")
    base = dt.datetime(2026, 10, 5, 13, 0, 0)
    t, w = [], []
    # disparos cada 2 s; W=n, T=n+1; el contador reinicia 0999 -> 0001; jitter T/W de +-1 s alterno
    idxs = [(i, i + 1) for i in range(979, 999, 2)] + [(999, 1)] + [(i, i + 1) for i in range(2, 22, 2)]
    for s, (wi, ti) in enumerate(idxs):
        tw = base + dt.timedelta(seconds=2 * s)
        tt = tw + dt.timedelta(seconds=1 if s % 2 else -1 if s else 0)
        t.append(f"{tt:%Y%m%d_%H%M%S}_DJI_{ti:04d}_T.JPG")
        w.append(f"{tw:%Y%m%d_%H%M%S}_DJI_{wi:04d}_W.JPG")
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert sp == [] and sorted(pares) == sorted(zip(t, w))


def test_sin_ts_en_una_foto_no_desactiva_rama_ts(tmp_path):
    o = _obj(tmp_path, "ord7")
    t = ["20261005_130000_DJI_0002_T.JPG", "20261005_130005_DJI_0004_T.JPG", "DJI_0006_T.JPG"]
    w = ["20261005_130000_DJI_0001_W.JPG", "20261005_130005_DJI_0003_W.JPG", "DJI_0005_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert sorted(pares) == sorted(zip(t, w)) and sp == []


def test_reloj_tw_desfasado_recupera_por_idx(tmp_path):
    o = _obj(tmp_path, "ord8")
    t = ["20261005_130000_DJI_0002_T.JPG", "20261005_130005_DJI_0004_T.JPG", "20261005_130010_DJI_0006_T.JPG"]
    w = ["20261005_130000_DJI_0001_W.JPG", "20261005_130005_DJI_0003_W.JPG", "20261005_130020_DJI_0005_W.JPG"]
    pares, sp, _ = o.emparejar_por_idx(t, w)
    assert sorted(pares) == sorted(zip(t, w)) and sp == []


def test_rendimiento_3000(tmp_path):
    import datetime as dt, time
    o = _obj(tmp_path, "perf")
    base = dt.datetime(2026, 10, 5, 13, 0, 0)
    t, w = [], []
    for s in range(3000):
        ts = base + dt.timedelta(seconds=s * 3)
        t.append(f"{ts:%Y%m%d_%H%M%S}_DJI_{(s * 2 + 2) % 1000:04d}_T.JPG".replace("_0000_", "_1000_"))
        w.append(f"{ts:%Y%m%d_%H%M%S}_DJI_{(s * 2 + 1) % 1000:04d}_W.JPG")
    t0 = time.time()
    pares, sp, _ = o.emparejar_por_idx(t, w)
    dt_s = time.time() - t0
    print(f"PERF 3000x3000: {dt_s:.2f}s")
    assert len(pares) + len(sp) // 2 >= 2990 and dt_s < 10
