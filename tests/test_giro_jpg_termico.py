"""El `*_T.JPG` térmico se publica SIEMPRE byte a byte (decisión de Rodrigo,
2026-10-08, 3.4.123): girarlo con PIL destruye el bloque radiométrico del R-JPEG.
El TIFF se gira por su propio camino. Estos tests fijan que, pida el ángulo que pida
el manifiesto, el JPG sale idéntico y el ORIGINAL nunca se toca."""

import filecmp
import os

import pytest
from PIL import Image

from atom_core import apply as apply_mod


def _escribir_jpg(ruta, ancho, alto, con_exif=True):
    img = Image.new("RGB", (ancho, alto), (40, 60, 90))
    if con_exif:
        exif = Image.Exif()
        exif[271] = "DJI"  # Make
        img.save(ruta, format="JPEG", quality=95, exif=exif)
    else:
        img.save(ruta, format="JPEG", quality=95)
    img.close()


def test_sin_angulo_se_copia_byte_a_byte(tmp_path):
    # El caso normal no paga recompresión: copy2, no reabrir con PIL.
    origen = tmp_path / "DJI_0001_T.JPG"
    destino = tmp_path / "salida" / "DJI_0001_T.JPG"
    _escribir_jpg(origen, 640, 512)

    apply_mod._copiar_jpg_destino(str(origen), str(destino), 0)

    assert filecmp.cmp(str(origen), str(destino), shallow=False)


@pytest.mark.parametrize("angulo", [90, 270])
def test_con_angulo_el_jpg_sale_igual_que_el_original(tmp_path, angulo):
    origen = tmp_path / "DJI_0002_T.JPG"
    destino = tmp_path / "salida" / "DJI_0002_T.JPG"
    _escribir_jpg(origen, 640, 512)

    apply_mod._copiar_jpg_destino(str(origen), str(destino), angulo)

    assert filecmp.cmp(str(origen), str(destino), shallow=False)
    with Image.open(destino) as copia:
        assert (copia.width, copia.height) == (640, 512)


def test_la_copia_conserva_el_exif(tmp_path):
    # El EXIF lleva GPS y fecha: al ser copia byte a byte viaja intacto.
    origen = tmp_path / "DJI_0003_T.JPG"
    destino = tmp_path / "salida" / "DJI_0003_T.JPG"
    _escribir_jpg(origen, 640, 512, con_exif=True)

    apply_mod._copiar_jpg_destino(str(origen), str(destino), 90)

    with Image.open(destino) as copia:
        assert copia.getexif().get(271) == "DJI"


def test_una_termica_ya_vertical_no_se_gira_otra_vez(tmp_path):
    # Las térmicas DJI son apaisadas de fábrica: una vertical ya viene girada y
    # volver a girarla la dejaría a 180º. Misma guarda que `_girar_termica_local`.
    origen = tmp_path / "DJI_0004_T.JPG"
    destino = tmp_path / "salida" / "DJI_0004_T.JPG"
    _escribir_jpg(origen, 512, 640)

    apply_mod._copiar_jpg_destino(str(origen), str(destino), 90)

    assert filecmp.cmp(str(origen), str(destino), shallow=False)


def test_un_fallo_no_deja_parcial_en_la_carpeta_de_entrega(tmp_path, monkeypatch):
    origen = tmp_path / "DJI_0005_T.JPG"
    destino = tmp_path / "salida" / "DJI_0005_T.JPG"
    _escribir_jpg(origen, 640, 512)

    def revienta(*_args, **_kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(apply_mod.shutil, "copy2", revienta)
    with pytest.raises(OSError):
        apply_mod._copiar_jpg_destino(str(origen), str(destino), 90)

    assert not destino.exists()
    assert os.listdir(destino.parent) == []
