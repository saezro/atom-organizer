"""Giro IN-PLACE del JPG térmico, junto a su TIFF (v3.4.6, 2026-08-05).

Hasta v3.4.5 esto escribía una copia aparte `<nombre>_ROT.JPG` y dejaba el
original sin girar: la salida venía duplicada. Cas: «no debes poner la girada con
otro nombre, simplemente giras la jpg y la tiff». Ahora queda un solo fichero por
imagen, con su nombre de siempre y girado igual que su TIFF.

El invariante duro pasa a ser el ORDEN: girar el `*_T.JPG` destruye su payload
radiométrico (es un R-JPEG de DJI), así que el paso tiene que correr DESPUÉS de
que el TIFF esté escrito y verificado. Lo demás son los casos que rompen el
pipeline si se descuidan: que gire lo MISMO que giró el TIFF (o el par deja de
casar), que no se duplique nada, y que una segunda pasada no lo gire a 180º.
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


# --- el invariante: se gira EN SU SITIO, sin dejar copias ---------------------

@pytest.mark.parametrize("flags,transpose", [
    ({"rotate_90": True}, Image.ROTATE_270),        # 90º horario
    ({"rotate_minus_90": True}, Image.ROTATE_90),   # 90º antihorario
])
def test_gira_el_jpg_en_su_sitio_sin_crear_copias(organizer_logger_stub, vuelo,
                                                  flags, transpose):
    raiz, carpeta = vuelo
    original = carpeta / "DJI_0001_T.JPG"
    esperada = Image.open(original).transpose(transpose)

    giradas = _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop(), **flags)

    assert giradas == 2
    assert not list(carpeta.glob("*" + ROTATED_JPG_SUFFIX + "*")), (
        "la salida no puede llevar copias con otro nombre: se gira el original")
    assert sorted(p.name for p in carpeta.glob("*.JPG")) == [
        "DJI_0001_T.JPG", "DJI_0002_T.JPG"], "un solo fichero por imagen"
    with Image.open(original) as img:
        assert img.size == esperada.size
        assert img.size[0] != img.size[1], "el giro debe cambiar la orientación"


def test_no_deja_temporales_en_la_carpeta_del_cliente(organizer_logger_stub, vuelo):
    """El guardado va a un `.rot.tmp` + os.replace; si sobreviviera, quedaría
    basura en la carpeta que se le entrega al cliente."""
    raiz, carpeta = vuelo
    _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop(), rotate_90=True)

    assert not list(carpeta.glob("*.tmp"))


def test_conserva_el_exif(organizer_logger_stub, vuelo):
    """Sobre estas fotos se consulta luego geolocalización y fecha."""
    raiz, carpeta = vuelo
    _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop(), rotate_90=True)

    with Image.open(carpeta / "DJI_0001_T.JPG") as img:
        assert img.info.get("exif"), "la imagen girada perdió el EXIF del original"


def test_sin_rotacion_no_se_toca_nada(organizer_logger_stub, vuelo):
    raiz, carpeta = vuelo
    antes = (carpeta / "DJI_0001_T.JPG").read_bytes()

    giradas = _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop())  # los tres flags a False

    assert giradas == 0
    assert (carpeta / "DJI_0001_T.JPG").read_bytes() == antes


# --- criterio AUTO: el JPG gira lo mismo que el TIFF --------------------------

@pytest.mark.parametrize("degree,transpose", [(90, Image.ROTATE_270), (270, Image.ROTATE_90)])
def test_auto_usa_el_mismo_criterio_que_el_tiff(organizer_logger_stub, tmp_path, vuelo,
                                                degree, transpose):
    raiz, carpeta = vuelo
    criterio = tmp_path / "CSVs"
    criterio.mkdir(parents=True)
    (criterio / "PB1_V1_Videofiles.csv").write_text(
        f"New Name,Original Name,Degree\na,b,{degree}\n", encoding="utf-8")

    esperada = Image.open(carpeta / "DJI_0001_T.JPG").transpose(transpose)
    obj = _split(organizer_logger_stub)
    # Mismo lector que consume la conversión a TIFF: si divergieran, el JPG
    # saldría del revés respecto a su TIFF.
    assert obj.read_auto_rotate_degree(str(carpeta), _noop()) == degree

    obj.rotate_thermal_jpgs_in_place(str(raiz), _noop(), _noop(), auto_rotate=True)
    with Image.open(carpeta / "DJI_0001_T.JPG") as img:
        assert img.size == esperada.size


def test_auto_sin_criterio_no_gira(organizer_logger_stub, vuelo):
    """Sin CSV no hay ángulo: el TIFF sale sin rotar, así que girar el JPG lo
    dejaría del revés respecto a su TIFF."""
    raiz, carpeta = vuelo
    antes = (carpeta / "DJI_0001_T.JPG").read_bytes()

    giradas = _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop(), auto_rotate=True)

    assert giradas == 0
    assert (carpeta / "DJI_0001_T.JPG").read_bytes() == antes


# --- que no se cuele donde rompe ---------------------------------------------

def test_las_rot_heredadas_de_345_no_entran_en_la_conversion(organizer_logger_stub, vuelo):
    """Una carpeta procesada con 3.4.5 tiene copias `_ROT`. Si se re-procesa,
    `convert_dji_images_to_tif` no puede intentar convertirlas (son JPG ya sin
    payload radiométrico) ni contarlas en `jpg_count == tiff_count`."""
    _, carpeta = vuelo
    legado = carpeta / ("DJI_0001_T" + ROTATED_JPG_SUFFIX + ".JPG")
    legado.write_bytes((carpeta / "DJI_0001_T.JPG").read_bytes())

    listadas = utils.Utils(organizer_logger_stub).get_images_from_dir(
        str(carpeta), [ROTATED_JPG_SUFFIX])
    assert sorted(listadas) == ["DJI_0001_T.JPG", "DJI_0002_T.JPG"]


def test_el_recuento_jpg_tiff_sigue_cuadrando(organizer_logger_stub, vuelo):
    """`checking_convert_to_tif` compara jpg_count con tiff_count. Con el giro
    in-place no se añade ningún JPG, así que tiene que seguir cuadrando."""
    raiz, carpeta = vuelo
    for nombre in ("DJI_0001_T.tiff", "DJI_0002_T.tiff"):
        (carpeta / nombre).write_bytes(b"")
    _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop(), rotate_90=True)

    resultados = _split(organizer_logger_stub).checking_convert_to_tif(
        str(raiz), _noop(), _noop())

    assert resultados, "no se analizó ninguna carpeta PB"
    for datos in resultados.values():
        assert datos["jpg_count"] == 2 and datos["match"] is True


def test_segunda_pasada_no_la_deja_a_180(organizer_logger_stub, vuelo):
    """Sin guarda, re-procesar la misma carpeta giraría otra vez lo ya girado.
    Las térmicas DJI son apaisadas de fábrica: si ya viene vertical, se deja."""
    raiz, carpeta = vuelo
    obj = _split(organizer_logger_stub)
    obj.rotate_thermal_jpgs_in_place(str(raiz), _noop(), _noop(), rotate_90=True)
    tras_la_primera = Image.open(carpeta / "DJI_0001_T.JPG").size

    giradas = obj.rotate_thermal_jpgs_in_place(str(raiz), _noop(), _noop(), rotate_90=True)

    assert giradas == 0
    with Image.open(carpeta / "DJI_0001_T.JPG") as img:
        assert img.size == tras_la_primera


def test_una_imagen_corrupta_no_tumba_el_resto(organizer_logger_stub, vuelo):
    """El paso corre DESPUÉS de que el TIFF ya esté en disco: un fallo aquí no
    puede llevarse por delante el vuelo entero."""
    raiz, carpeta = vuelo
    rota = carpeta / "DJI_0003_T.JPG"
    rota.write_bytes(b"esto no es un JPEG")

    giradas = _split(organizer_logger_stub).rotate_thermal_jpgs_in_place(
        str(raiz), _noop(), _noop(), rotate_90=True)

    assert giradas == 2  # las dos buenas sí se giraron
    assert rota.read_bytes() == b"esto no es un JPEG", (
        "un fallo a mitad no puede dejar el fichero original truncado")


def test_stop_cooperativo_corta_el_giro(organizer_logger_stub, vuelo):
    raiz, _ = vuelo
    obj = _split(organizer_logger_stub)
    obj.set_stop(True)

    assert obj.rotate_thermal_jpgs_in_place(str(raiz), _noop(), _noop(), rotate_90=True) == 0


def test_carpeta_inexistente_no_revienta(organizer_logger_stub, tmp_path):
    obj = _split(organizer_logger_stub)
    assert obj.rotate_thermal_jpgs_in_place(
        str(tmp_path / "no-existe"), _noop(), _noop(), rotate_90=True) == 0


# --- el orden importa: girar destruye el payload ------------------------------

def test_el_giro_va_despues_de_la_conversion_a_tiff():
    """Girar el `*_T.JPG` lo re-encoda y le quita el payload radiométrico, así que
    a partir de ahí ya no se puede convertir a TIFF. En `gui.py` el giro TIENE que
    invocarse después de `checking_convert_to_tif`, nunca antes."""
    import re

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, "gui.py"), encoding="utf-8") as fh:
        fuente = fh.read()

    giros = [m.start() for m in re.finditer(r"rotate_thermal_jpgs_in_place", fuente)]
    verificaciones = [m.start() for m in re.finditer(r"checking_convert_to_tif", fuente)]
    assert giros and verificaciones
    for pos in giros:
        assert any(v < pos for v in verificaciones), (
            "el giro in-place se invoca antes de convertir y verificar el TIFF: "
            "las térmicas llegarían al conversor ya sin payload radiométrico.")
