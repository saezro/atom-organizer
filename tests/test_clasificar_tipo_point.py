"""#3890: térmicas `_T_pointN` (H30T) se clasificaban como RGB en el índice."""
from types import SimpleNamespace

from atom_core import indice
from atom_core.suffixes import _stem_suffix


def _cfg(thermo="_T", extra=""):
    return SimpleNamespace(end_thermo_files=thermo, end_rgb_extra_files=extra, end_rgb_files="")


def test_termica_simple():
    assert indice._clasificar_tipo("DJI_20260910_0001_T.JPG", _cfg()) == "TERMICA"


def test_termica_con_point():
    assert indice._clasificar_tipo("DJI_20260910_0001_T_point0.JPG", _cfg()) == "TERMICA"
    assert indice._clasificar_tipo("DJI_20260910_0001_T_point12.JPG", _cfg()) == "TERMICA"


def test_visible_con_point_es_rgb():
    assert indice._clasificar_tipo("DJI_20260910_0001_V_point3.JPG", _cfg()) == "RGB"


def test_rgb_extra_con_y_sin_point():
    cfg = _cfg(extra="_Z")
    assert indice._clasificar_tipo("DJI_0001_Z.JPG", cfg) == indice.NOMBRE_CARPETA_RGB_EXTRA
    assert indice._clasificar_tipo("DJI_0001_Z_point2.JPG", cfg) == indice.NOMBRE_CARPETA_RGB_EXTRA


def test_sin_sufijo_es_rgb():
    assert indice._clasificar_tipo("IMG1234.JPG", _cfg()) == "RGB"


def test_stem_suffix_ignora_point():
    assert _stem_suffix("DJI_0001_T_point0") == "_T"
    assert _stem_suffix("DJI_0001_V_point7") == "_V"
    assert _stem_suffix("DJI_0001_T") == "_T"


def test_detect_suffixes_rgb_tokens_informativo(tmp_path):
    from atom_core.suffixes import detect_suffixes
    for n in ("DJI_0001_T.JPG", "DJI_0002_T.JPG", "DJI_0001_W.JPG", "DJI_0002_W.JPG", "DJI_0003_W.JPG"):
        (tmp_path / n).write_bytes(b"x")
    r = detect_suffixes(str(tmp_path))
    assert r["thermal"] == "_T" and r["rgb"] == ""
    assert r["rgb_tokens"] == [{"token": "_W", "n": 3}]
    solo_t = tmp_path / "solo_t"
    solo_t.mkdir()
    (solo_t / "DJI_0001_T.JPG").write_bytes(b"x")
    assert detect_suffixes(str(solo_t))["rgb_tokens"] == []
