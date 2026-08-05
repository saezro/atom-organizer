"""
`exif.leer_bloque_xmp` sustituye al `fd.read()` que se tragaba la foto entera (9-20 MB en
una imagen de dron) para leer un bloque XMP que vive en los primeros KB. Lo que se prueba
aquí es que el atajo NO cambia ni un dato: para la MISMA imagen, el yaw/pitch y el resto
del XMP tienen que salir exactamente iguales que leyendo el fichero completo, incluidos
los dos casos incómodos:

- la imagen no tiene XMP (no debe inventarse nada: sigue devolviendo los "0" de siempre);
- el XMP está más allá del trozo de cabecera (debe caer al fallback y encontrarlo igual).

El segundo es el que justifica que el fallback exista: sin él, una imagen con mucho
metadato por delante del XMP se quedaría sin girar y el vuelo saldría mal.
"""
import os

import pytest

import exif


def _xmp(yaw: str, pitch: str) -> bytes:
    """Bloque XMP con el mismo aspecto que el que escriben las cámaras DJI."""
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
        f'drone-dji:GimbalYawDegree="{yaw}" drone-dji:GimbalPitchDegree="{pitch}" '
        'drone-dji:AbsoluteAltitude="+123.45" drone-dji:RelativeAltitude="+80.20" '
        'drone-dji:GimbalRollDegree="+0.00" drone-dji:FlightRollDegree="+1.10" '
        'drone-dji:FlightYawDegree="-88.30" drone-dji:FlightPitchDegree="+2.20" '
        'drone-dji:CamReverse="0" drone-dji:GimbalReverse="0" drone-dji:RtkFlag="50" '
        '/></rdf:RDF></x:xmpmeta>'
    ).encode("latin-1")


def _escribir_jpg(path, xmp_bytes, relleno=0, padding_antes=0):
    """
    Escribe un JPEG de mentira: SOI, `padding_antes` bytes de segmentos de relleno, el
    segmento XMP, y `relleno` bytes de cuerpo para darle el tamaño de una foto real.
    Un segmento APP no admite más de 65535 bytes, así que el padding va encadenado.
    """
    with open(path, "wb") as f:
        f.write(b"\xff\xd8")
        escritos = 0
        while escritos < padding_antes:
            trozo = min(65531, padding_antes - escritos)
            f.write(b"\xff\xe0" + (trozo + 2).to_bytes(2, "big") + b"\x00" * trozo)
            escritos += trozo
        if xmp_bytes:
            f.write(b"\xff\xe1" + (len(xmp_bytes) + 2).to_bytes(2, "big") + xmp_bytes)
        if relleno:
            f.write(b"\x00" * relleno)
    return path


def _lectura_completa(filename: str) -> str:
    """La implementación vieja, tal cual estaba: leer el fichero entero."""
    with open(filename, encoding="latin-1") as fd:
        return fd.read()


CASOS = [
    ("normal", _xmp("-89.90", "-90.00"), 0),
    ("yaw_positivo", _xmp("+89.90", "-90.00"), 0),
    ("yaw_a_cero", _xmp("0.00", "0.00"), 0),
    # XMP empujado más allá del trozo de cabecera -> obliga a usar el fallback.
    ("xmp_tras_el_limite", _xmp("-77.77", "-88.88"), exif._XMP_HEADER_BYTES + 4096),
]


@pytest.mark.parametrize("nombre, xmp_bytes, padding", CASOS)
def test_leer_bloque_xmp_da_el_mismo_xmp_que_leer_el_fichero_entero(tmp_path, nombre, xmp_bytes, padding):
    jpg = _escribir_jpg(tmp_path / f"{nombre}.jpg", xmp_bytes, relleno=512 * 1024, padding_antes=padding)

    completo = _lectura_completa(jpg)
    cabecera = exif.leer_bloque_xmp(jpg)

    # Los índices del trozo tienen que ser los mismos que los del fichero entero: ambos
    # empiezan en el byte 0, y de ahí sale el recorte del bloque XMP.
    assert cabecera.find("<x:xmpmeta") == completo.find("<x:xmpmeta")
    assert cabecera.find("</x:xmpmeta") == completo.find("</x:xmpmeta")

    inicio, fin = completo.find("<x:xmpmeta"), completo.find("</x:xmpmeta")
    assert cabecera[inicio:fin + 12] == completo[inicio:fin + 12]


def test_imagen_sin_xmp_no_rompe_y_no_inventa_datos(tmp_path):
    """Sin bloque XMP el atajo relee entero, no encuentra nada, y no se inventa un giro."""
    jpg = _escribir_jpg(tmp_path / "sin_xmp.jpg", None, relleno=256 * 1024)

    cabecera = exif.leer_bloque_xmp(jpg)

    assert cabecera == _lectura_completa(jpg)
    assert cabecera.find("<x:xmpmeta") == -1


def test_el_atajo_lee_muchisimo_menos_que_el_fichero_entero(tmp_path):
    """
    El sentido del cambio: en una imagen grande con el XMP delante, no se toca el cuerpo.
    Se compara el tamaño de lo leído, no el tiempo, para que no dependa de la máquina.
    """
    jpg = _escribir_jpg(tmp_path / "grande.jpg", _xmp("-89.90", "-90.00"), relleno=8 * 1024 * 1024)

    cabecera = exif.leer_bloque_xmp(jpg)

    assert len(cabecera) <= exif._XMP_HEADER_BYTES
    assert len(cabecera) < os.path.getsize(jpg) / 10  # menos de una décima parte del fichero
