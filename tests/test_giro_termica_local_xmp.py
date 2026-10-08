"""`pipeline._girar_termica_local` conserva el XMP (yaw/gimbal) al girar en sitio."""
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       b'<rdf:Description xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" drone-dji:GimbalYawDegree="+12.3"/>'
       b'</rdf:RDF></x:xmpmeta>')


def _jpg(ruta, w, h, xmp=None):
    Image.new("RGB", (w, h), (40, 60, 90)).save(ruta, format="JPEG", quality=95)
    if xmp:
        from atom_core.apply import insertar_xmp_app1
        out = str(ruta) + ".x"
        insertar_xmp_app1(str(ruta), out, xmp)
        Path(out).replace(ruta)


def _sp():
    import pipeline
    return pipeline.SplitImages.__new__(pipeline.SplitImages)


def test_girada_conserva_xmp(tmp_path):
    from exif import extraer_bloque_xmp_crudo
    ruta = tmp_path / "a_T.JPG"
    _jpg(ruta, 640, 512, XMP)
    assert _sp()._girar_termica_local(ruta, Image.Transpose.ROTATE_270) == "girada"
    with Image.open(ruta) as im:
        assert im.size == (512, 640)
    assert extraer_bloque_xmp_crudo(str(ruta)) == XMP
    assert [p.name for p in tmp_path.glob("*.JPG*")] == ["a_T.JPG"]


def test_sin_xmp_se_marca(tmp_path):
    ruta = tmp_path / "b_T.JPG"
    _jpg(ruta, 640, 512)
    assert _sp()._girar_termica_local(ruta, Image.Transpose.ROTATE_270) == "girada_sin_xmp"


def test_ya_vertical_no_se_recodifica(tmp_path):
    ruta = tmp_path / "c_T.JPG"
    _jpg(ruta, 512, 640, XMP)
    antes = ruta.read_bytes()
    assert _sp()._girar_termica_local(ruta, Image.Transpose.ROTATE_270) == "ya_girada"
    assert ruta.read_bytes() == antes
