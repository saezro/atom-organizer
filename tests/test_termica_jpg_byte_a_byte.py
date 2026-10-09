"""Decisión de Rodrigo (2026-10-08): la T JPG térmica (R-JPEG DJI) se copia SIEMPRE
byte a byte; girarla con PIL destruye el bloque radiométrico. La RGB (W) sí se gira."""

import hashlib

import pytest
from PIL import Image

from atom_core import apply as apply_mod

EOI = b"\xff\xd9"
BLOQUE_RADIOMETRICO = b"RADIOMETRIC-PAYLOAD" * 500


def _rjpeg_sintetico(ruta, ancho=640, alto=512):
    Image.new("RGB", (ancho, alto), (40, 60, 90)).save(ruta, format="JPEG", quality=95)
    with open(ruta, "ab") as f:  # payload tras el 1.er EOI, como el R-JPEG real
        f.write(BLOQUE_RADIOMETRICO)
    return ruta


def _md5(ruta):
    with open(ruta, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


@pytest.mark.parametrize("angulo", [0, 90, 270])
def test_termica_se_copia_byte_a_byte_aunque_pida_giro(tmp_path, angulo):
    origen = _rjpeg_sintetico(str(tmp_path / "DJI_0001_T.JPG"))
    destino = str(tmp_path / "salida" / "DJI_0001_T.JPG")

    apply_mod._copiar_jpg_destino(origen, destino, angulo)

    assert _md5(destino) == _md5(origen)
    datos = open(destino, "rb").read()
    assert BLOQUE_RADIOMETRICO in datos[datos.find(EOI) + 2:]


def test_rgb_w_sigue_girandose(tmp_path, make_dji_jpeg):
    import pipeline
    origen = make_dji_jpeg(str(tmp_path / "o_W.jpg"))
    destino = str(tmp_path / "sub" / "d_W.jpg")
    with Image.open(origen) as img:
        w, h = img.size
        apply_mod._guardar_atomico(img, destino, Image.ROTATE_270, None, 90, pipeline)
    with Image.open(destino) as girada:
        assert (girada.width, girada.height) == (h, w)
