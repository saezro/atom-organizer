"""Sufijo `_V` (RGB Mavic 3T) equivale a `_W` en la clave de pareja y en el emparejado."""
import exif
from atom_core.pares import clave_pareja
from utils import OrganizerLogger


def _obj(tmp_path):
    o = exif.MetaLocation(OrganizerLogger("v", log_dir=str(tmp_path), create_file_handler=False))
    o.stop = False
    return o


def test_clave_v_normaliza_a_w():
    assert clave_pareja("DJI_20261021140519_0827_V.JPG") == (827, "W")
    assert clave_pareja("DJI_20261021140519_0812_V_point134.JPG") == (812, "W")
    assert clave_pareja("DJI_20261021140519_0827_X.JPG") is None


def test_emparejar_v_con_t(tmp_path):
    o = _obj(tmp_path)
    t = ["DJI_20261021140519_0827_T.JPG", "DJI_20261021140520_0812_T.JPG"]
    w = ["DJI_20261021140519_0827_V.JPG", "DJI_20261021140520_0812_V_point134.JPG"]
    pares, sin_pareja, sin_patron = o.emparejar_por_idx(t, w)
    assert len(pares) == 2 and not sin_pareja and not sin_patron


def test_sufijo_desconocido_sin_patron(tmp_path):
    o = _obj(tmp_path)
    pares, _, sin_patron = o.emparejar_por_idx(["DJI_20261021140519_0827_T.JPG"],
                                               ["DJI_20261021140519_0827_X.JPG"])
    assert not pares and sin_patron == ["DJI_20261021140519_0827_X.JPG"]
