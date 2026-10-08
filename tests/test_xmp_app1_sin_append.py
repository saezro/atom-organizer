"""El XMP va como segmento APP1 en cabecera, nunca pegado tras el EOI con
reapertura `ab` (corrompía el JPG en el montaje de Drive)."""
import os
import re

import pytest
from PIL import Image

from atom_core import apply as ap
import exif as exif_mod

XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
       b'xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" '
       b'drone-dji:GimbalYawDegree="+12.50" drone-dji:GimbalPitchDegree="-90.00"/>'
       b'</rdf:RDF></x:xmpmeta>')


def _jpg(ruta):
    Image.new("RGB", (64, 48), (120, 130, 140)).save(ruta, "JPEG")


def test_insertar_app1_sin_bytes_tras_eoi_y_legible(tmp_path):
    src, out = str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")
    _jpg(src)
    ap.insertar_xmp_app1(src, out, XMP)
    datos = open(out, "rb").read()
    assert datos.endswith(b"\xff\xd9")
    assert datos.count(b"\xff\xd9") == 1 or datos.rstrip().endswith(b"\xff\xd9")
    ap.verificar_parcial(out)
    assert exif_mod.extraer_bloque_xmp_crudo(out) == XMP
    texto = exif_mod.leer_bloque_xmp(out)
    assert re.search(r'GimbalYawDegree="\+12\.50"', texto)
    assert re.search(r'GimbalPitchDegree="-90\.00"', texto)
    assert datos.find(XMP) < 70000  # en cabecera


def test_xmp_demasiado_grande_error_visible(tmp_path):
    src, out = str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")
    _jpg(src)
    with pytest.raises(ap.XmpDemasiadoGrandeError):
        ap.insertar_xmp_app1(src, out, b"x" * 70000)
    assert not os.path.exists(out)


def test_verificar_parcial_rechaza_vacio_y_truncado(tmp_path):
    vacio = tmp_path / "v.jpg"
    vacio.write_bytes(b"")
    with pytest.raises(Exception):
        ap.verificar_parcial(str(vacio))
    ok = tmp_path / "ok.jpg"
    _jpg(str(ok))
    trunc = tmp_path / "t.jpg"
    trunc.write_bytes(ok.read_bytes()[:-200])
    with pytest.raises(Exception):
        ap.verificar_parcial(str(trunc))


def test_ruta_parcial_unica_y_reconocida(tmp_path):
    d = str(tmp_path / "DJI_0001.JPG")
    a, b = ap.ruta_parcial(d), ap.ruta_parcial(d)
    assert a != b and a.endswith(".JPG")
    assert ap.es_nombre_parcial(os.path.basename(a))
    assert not ap.es_nombre_parcial("DJI_0001.JPG")
    open(a, "wb").close()
    assert ap.limpiar_parciales_huerfanos(str(tmp_path)) == 1


def test_copiar_jpg_con_giro_conserva_xmp_en_cabecera(tmp_path, make_dji_jpeg):
    import pipeline
    origen = make_dji_jpeg(str(tmp_path / "o.jpg"), gimbal_yaw=33.0)
    destino = str(tmp_path / "d.jpg")
    ap._copiar_jpg_destino(origen, destino, 90)
    datos = open(destino, "rb").read()
    assert datos.endswith(b"\xff\xd9")
    ap.verificar_parcial(destino)
    assert exif_mod.extraer_bloque_xmp_crudo(destino) is not None
    assert not [f for f in os.listdir(tmp_path) if ap.es_nombre_parcial(f)]


def test_guardar_atomico_xmp_cabecera_y_sin_parcial(tmp_path, make_dji_jpeg):
    import pipeline
    origen = make_dji_jpeg(str(tmp_path / "o.jpg"))
    destino = str(tmp_path / "sub" / "d.jpg")
    img = Image.open(origen)
    ap._guardar_atomico(img, destino, None, None, 90, pipeline, bloque_xmp=XMP)
    assert open(destino, "rb").read().endswith(b"\xff\xd9")
    ap.verificar_parcial(destino)
    assert exif_mod.extraer_bloque_xmp_crudo(destino) == XMP
    assert os.listdir(tmp_path / "sub") == ["d.jpg"]
