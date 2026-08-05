"""Invariante de la struct de parámetros de medición del SDK DJI (`dji_irp_linux.py`).

`dirp_get_measurement_params` escribe **20 bytes** (cinco floats), no 16: además de
distance / humidity / emissivity / reflection hay un quinto campo que la cabecera
pública no documenta y que resulta ser la temperatura ambiente (medido: 18,9-23,8 C
en las térmicas de un vuelo real).

Declarar la struct con solo cuatro campos era un **buffer overflow**: el SDK pisaba
4 bytes del heap de Python en cada térmica. No se veía porque cada imagen corre en un
proceso efímero que muere justo después — el daño quedaba enmascarado y se atribuía a
un "segfault benigno de libdirp/libgomp en el teardown". Encadenando imágenes en un
mismo proceso reventaba a la sexta, de forma reproducible.

Este test es barato y no necesita el SDK: fija el tamaño mínimo de la struct para que
nadie la vuelva a recortar creyendo que sobran campos.
"""
import ctypes

import dji_irp_linux


# Cinco floats: lo que el SDK escribe de verdad.
_BYTES_QUE_ESCRIBE_EL_SDK = 5 * ctypes.sizeof(ctypes.c_float)


def test_la_struct_cubre_lo_que_el_sdk_escribe():
    """Nunca menor que los 20 B que el SDK escribe: menos es corromper el heap."""
    assert ctypes.sizeof(dji_irp_linux._measurement_params_t) >= _BYTES_QUE_ESCRIBE_EL_SDK


def test_estan_los_cinco_campos_y_en_orden():
    """El orden importa: son posiciones de memoria, no argumentos con nombre."""
    campos = [nombre for nombre, _ in dji_irp_linux._measurement_params_t._fields_]
    assert campos[:5] == ["distance", "humidity", "emissivity", "reflection", "ambient"]


def test_los_cinco_campos_son_float32():
    tipos = dict(dji_irp_linux._measurement_params_t._fields_)
    for nombre in ("distance", "humidity", "emissivity", "reflection", "ambient"):
        assert tipos[nombre] is ctypes.c_float, nombre


def test_los_cuatro_parametros_del_cli_caen_en_los_primeros_16_bytes():
    """Lo que ATOM fija (y el CLI dji_irp expone) sigue al principio de la struct:
    si alguien reordena la struct, el SDK recibiría humidity donde espera distance."""
    offsets = [getattr(dji_irp_linux._measurement_params_t, n).offset
               for n in ("distance", "humidity", "emissivity", "reflection")]
    assert offsets == [0, 4, 8, 12]
