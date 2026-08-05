"""Copia girada `_ROT` del JPG térmico, junto a su TIFF (bloque 1, 2026-08-05).

El `*_T.JPG` de DJI es un R-JPEG con payload radiométrico propietario: si PIL lo
re-encoda, ese fichero ya no se puede convertir a TIFF nunca más. Por eso la
rotación va a una COPIA aparte y el original tiene que quedar byte a byte igual.
Ese es el invariante duro que se protege aquí.

El resto de casos son los que rompen el pipeline si se descuidan: que la copia
gire lo MISMO que giró el TIFF (o el par deja de casar), que no se cuele en la
conversión ni en el recuento `jpg_count == tiff_count`, y que una segunda pasada
no encadene `_ROT_ROT`.
"""
import os

import pytest
from PIL import Image

import pipeline
import utils
from utils import ROTATED_JPG_SUFFIX


def _noop():
    class _S:
        def emit(self, *a, **k):
            pass
    return _S()


@pytest.fixture
def vuelo(tmp_path, make_dji_jpeg):
    """<tmp>/TERMICA/PB1/PB1_V1/ con dos térmicas. Devuelve (raiz_termica, carpeta)."""
    carpeta = tmp_path / "TERMICA" / "PB1" / "PB1_V1"
    carpeta.mkdir(parents=True)
    make_dji_jpeg(str(carpeta / "DJI_0001_T.JPG"))
    make_dji_jpeg(str(carpeta / "DJI_0002_T.JPG"))
    return tmp_path / "TERMICA", carpeta


def _split(logger):
    obj = pipeline.SplitImages(logger)
    obj.reset_variables()
    return obj


# --- el invariante duro -------------------------------------------------------

def test_el_jpg_termico_original_no_se_toca(organizer_logger_stub, vuelo):
    """Si el original cambia UN byte, el payload radiométrico está en riesgo y la
    imagen deja de poder convertirse a TIFF. Es el motivo de que exista la copia."""
    raiz, carpeta = vuelo
    original = carpeta / "DJI_0001_T.JPG"
    antes = original.read_bytes()

    _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), rotate_90=True)

    assert original.read_bytes() == antes


# --- se escribe la copia, y girada en el sentido correcto ---------------------

@pytest.mark.parametrize("flags,transpose", [
    ({"rotate_90": True}, Image.ROTATE_270),        # 90º horario
    ({"rotate_minus_90": True}, Image.ROTATE_90),   # 90º antihorario
])
def test_escribe_la_copia_girada_en_la_misma_carpeta(organizer_logger_stub, vuelo,
                                                     flags, transpose):
    raiz, carpeta = vuelo
    esperada = Image.open(carpeta / "DJI_0001_T.JPG").transpose(transpose)

    escritas = _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), **flags)

    assert escritas == 2
    copia = carpeta / ("DJI_0001_T" + ROTATED_JPG_SUFFIX + ".JPG")
    assert copia.exists(), "la copia va junto a su TIFF, no en una subcarpeta"
    with Image.open(copia) as img:
        assert img.size == esperada.size
        assert img.size[0] != img.size[1], "el giro debe cambiar la orientación"


def test_la_copia_conserva_el_exif(organizer_logger_stub, vuelo):
    """Sobre estas fotos se consulta luego geolocalización y fecha."""
    raiz, carpeta = vuelo
    _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), rotate_90=True)

    with Image.open(carpeta / ("DJI_0001_T" + ROTATED_JPG_SUFFIX + ".JPG")) as img:
        assert img.info.get("exif"), "la copia girada perdió el EXIF del original"


def test_sin_rotacion_no_se_duplican_los_jpg(organizer_logger_stub, vuelo):
    raiz, carpeta = vuelo
    escritas = _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop())  # los tres flags a False

    assert escritas == 0
    assert not list(carpeta.glob("*" + ROTATED_JPG_SUFFIX + "*"))


# --- criterio AUTO: la copia gira lo mismo que el TIFF ------------------------

@pytest.mark.parametrize("degree,transpose", [(90, Image.ROTATE_270), (270, Image.ROTATE_90)])
def test_auto_usa_el_mismo_criterio_que_el_tiff(organizer_logger_stub, tmp_path, vuelo,
                                                degree, transpose):
    raiz, carpeta = vuelo
    criterio = tmp_path / "CSVs" / "PB1_V1"
    criterio.mkdir(parents=True)
    (criterio / "PB1_V1_Videofiles.csv").write_text(
        f"New Name,Original Name,Degree\na,b,{degree}\n", encoding="utf-8")

    obj = _split(organizer_logger_stub)
    # Mismo lector que consume la conversión a TIFF: si divergieran, el JPG
    # saldría del revés respecto a su TIFF.
    assert obj.read_auto_rotate_degree(str(carpeta), _noop()) == degree

    obj.write_rotated_jpg_copies(str(raiz), _noop(), _noop(), auto_rotate=True)
    esperada = Image.open(carpeta / "DJI_0001_T.JPG").transpose(transpose)
    with Image.open(carpeta / ("DJI_0001_T" + ROTATED_JPG_SUFFIX + ".JPG")) as img:
        assert img.size == esperada.size


def test_auto_sin_criterio_no_escribe_nada(organizer_logger_stub, vuelo):
    """Sin CSV no hay ángulo: el TIFF sale sin rotar, así que una copia 'girada'
    sería un duplicado idéntico y engañoso."""
    raiz, carpeta = vuelo
    escritas = _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), auto_rotate=True)

    assert escritas == 0
    assert not list(carpeta.glob("*" + ROTATED_JPG_SUFFIX + "*"))


# --- que no se cuele donde rompe ---------------------------------------------

def test_la_copia_no_entra_en_el_listado_de_conversion(organizer_logger_stub, vuelo):
    """`convert_dji_images_to_tif` lista con exclusión: si viera las `_ROT`,
    intentaría convertir un JPG ya sin payload radiométrico."""
    raiz, carpeta = vuelo
    _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), rotate_90=True)

    listadas = utils.Utils(organizer_logger_stub).get_images_from_dir(
        str(carpeta), [ROTATED_JPG_SUFFIX])
    assert sorted(listadas) == ["DJI_0001_T.JPG", "DJI_0002_T.JPG"]


def test_la_copia_no_descuadra_el_recuento_jpg_tiff(organizer_logger_stub, vuelo):
    """`checking_convert_to_tif` compara jpg_count con tiff_count; contar las
    copias daría el doble de JPG y un falso 'no coinciden'."""
    raiz, carpeta = vuelo
    for nombre in ("DJI_0001_T.tiff", "DJI_0002_T.tiff"):
        (carpeta / nombre).write_bytes(b"")
    _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), rotate_90=True)

    resultados = _split(organizer_logger_stub).checking_convert_to_tif(
        str(raiz), _noop(), _noop())

    assert resultados, "no se analizó ninguna carpeta PB"
    for datos in resultados.values():
        assert datos["jpg_count"] == 2 and datos["match"] is True


def test_segunda_pasada_no_encadena_rot_rot(organizer_logger_stub, vuelo):
    raiz, carpeta = vuelo
    obj = _split(organizer_logger_stub)
    obj.write_rotated_jpg_copies(str(raiz), _noop(), _noop(), rotate_90=True)
    escritas = obj.write_rotated_jpg_copies(str(raiz), _noop(), _noop(), rotate_90=True)

    assert escritas == 0
    assert not list(carpeta.glob("*_ROT_ROT*"))
    assert len(list(carpeta.glob("*.JPG"))) == 4  # 2 originales + 2 copias


def test_una_imagen_corrupta_no_tumba_el_resto(organizer_logger_stub, vuelo):
    """El paso corre DESPUÉS de que el TIFF ya esté en disco: un fallo aquí no
    puede llevarse por delante el vuelo entero."""
    raiz, carpeta = vuelo
    (carpeta / "DJI_0003_T.JPG").write_bytes(b"esto no es un JPEG")

    escritas = _split(organizer_logger_stub).write_rotated_jpg_copies(
        str(raiz), _noop(), _noop(), rotate_90=True)

    assert escritas == 2  # las dos buenas sí se escribieron


def test_stop_cooperativo_corta_la_escritura(organizer_logger_stub, vuelo):
    raiz, _ = vuelo
    obj = _split(organizer_logger_stub)
    obj.set_stop(True)

    assert obj.write_rotated_jpg_copies(str(raiz), _noop(), _noop(), rotate_90=True) == 0


def test_carpeta_inexistente_no_revienta(organizer_logger_stub, tmp_path):
    obj = _split(organizer_logger_stub)
    assert obj.write_rotated_jpg_copies(
        str(tmp_path / "no-existe"), _noop(), _noop(), rotate_90=True) == 0
