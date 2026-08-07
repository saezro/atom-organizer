"""
`saving_all_xmp_data` sustituye a la pareja `saving_gimbal_data_in_xmp` +
`saving_xmp_data_in_xmp`. Cada una de las viejas abría el JPEG con pyexiv2 y lo
REESCRIBÍA entero: dos pasadas completas sobre un fichero de 5-20 MB para grabar
once claves que caben en un solo `modify_xmp`.

Lo que se prueba aquí es que el atajo NO cambia el resultado: para la misma imagen y
los mismos datos, el XMP que queda escrito tiene que ser exactamente el mismo que
dejaban las dos llamadas encadenadas. Si alguien añade una clave a una de las dos
funciones viejas y no a la nueva, este test lo caza.

Y se prueba que escribe UNA sola vez, que es el motivo del cambio: en Windows cada
reescritura dispara el escaneo del antivirus, así que el número de aperturas es el
comportamiento a fijar, no un detalle de implementación.
"""
import pytest

pyexiv2 = pytest.importorskip("pyexiv2")
from PIL import Image  # noqa: E402

import exif  # noqa: E402


GIMBAL = ("-89.90", "-90.00")
RESTO = ("+123.45", "+80.20", "+0.00", "+1.10", "-88.30", "+2.20", "0", "0", "50")


@pytest.fixture
def gestor(logger):
    return exif.GeneralInformationFromImage(logger)


def _jpg(tmp_path, nombre):
    """Un JPEG de verdad (pyexiv2 rechaza los ficheros de mentira)."""
    ruta = tmp_path / nombre
    Image.new("RGB", (64, 48), (10, 20, 30)).save(ruta, quality=80)
    return str(ruta)


def test_una_llamada_deja_el_mismo_xmp_que_las_dos_viejas(tmp_path, gestor):
    viejo = _jpg(tmp_path, "viejo.jpg")
    nuevo = _jpg(tmp_path, "nuevo.jpg")

    gestor.saving_gimbal_data_in_xmp(viejo, GIMBAL)
    gestor.saving_xmp_data_in_xmp(viejo, RESTO)

    gestor.saving_all_xmp_data(nuevo, GIMBAL, RESTO)

    with pyexiv2.Image(viejo) as im:
        xmp_viejo = im.read_xmp()
    with pyexiv2.Image(nuevo) as im:
        xmp_nuevo = im.read_xmp()

    assert xmp_nuevo == xmp_viejo
    # Y que no esté vacío: un `{} == {}` pasaría igual sin haber escrito nada.
    assert xmp_viejo.get("Xmp.drone-dji.GimbalYawDegree") == GIMBAL[0]
    assert xmp_viejo.get("Xmp.drone-dji.RtkFlag") == RESTO[8]


def test_escribe_el_fichero_una_sola_vez(tmp_path, gestor, monkeypatch):
    """El motivo del cambio: dos reescrituras completas del JPEG pasan a una."""
    jpg = _jpg(tmp_path, "contando.jpg")
    aperturas = []
    original = exif.pyexiv2.Image

    def espia(ruta, *args, **kwargs):
        aperturas.append(ruta)
        return original(ruta, *args, **kwargs)

    monkeypatch.setattr(exif.pyexiv2, "Image", espia)

    gestor.saving_all_xmp_data(jpg, GIMBAL, RESTO)

    assert aperturas == [jpg]


def test_no_lanza_si_la_imagen_no_existe(tmp_path, gestor):
    """
    Mismo contrato defensivo que las dos funciones viejas: registran el fallo y siguen.
    `compress_image` las llama dentro de su propio try, pero el pipeline no puede
    permitirse que una imagen ilegible tumbe el lote entero.
    """
    gestor.saving_all_xmp_data(str(tmp_path / "no_existe.jpg"), GIMBAL, RESTO)


def test_las_dos_funciones_viejas_siguen_existiendo(gestor):
    """
    No se borran: hay tests y otros puntos del pipeline que las usan. El cambio es
    aditivo, y esto lo fija para que nadie las quite «de paso».
    """
    assert callable(gestor.saving_gimbal_data_in_xmp)
    assert callable(gestor.saving_xmp_data_in_xmp)
