"""Tests de `atom_core.avatar_cache`: cero red real, todo vía el hook `descargador`.

Lo que importa proteger aquí: que la segunda llamada NO vuelva a golpear la
red (el propósito entero del módulo es evitar el rate-limit de Google), que un
fallo de descarga jamás se propague como excepción hacia la UI, y que las
validaciones de tamaño/content-type/caducidad se apliquen de verdad.
"""
from __future__ import annotations

import os
import time

import pytest

from atom_core import avatar_cache

URL = "https://lh3.googleusercontent.com/a/foo=s96-c"
EMAIL = "operador@aerotools.es"

PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _descargador_contador(imagen=PIXEL_PNG, content_type="image/png"):
    """Devuelve (descargador, contador) — el contador es una lista mutable con el nº de llamadas."""
    llamadas = []

    def descargador(url):
        llamadas.append(url)
        return imagen, content_type

    return descargador, llamadas


def test_primera_llamada_descarga_y_escribe_fichero(tmp_path):
    descargador, llamadas = _descargador_contador()
    resultado = avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)

    assert resultado.startswith("data:image/png;base64,")
    assert len(llamadas) == 1

    ruta = avatar_cache.ruta_cache(tmp_path) / avatar_cache._nombre_fichero(EMAIL)
    assert ruta.is_file()


def test_segunda_llamada_no_vuelve_a_descargar(tmp_path):
    descargador, llamadas = _descargador_contador()
    primero = avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)
    segundo = avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)

    assert primero == segundo
    assert len(llamadas) == 1


def test_fallo_del_descargador_devuelve_cadena_vacia_sin_lanzar(tmp_path):
    def descargador(url):
        raise OSError("timeout simulado")

    resultado = avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)
    assert resultado == ""


def test_content_type_no_imagen_devuelve_vacio(tmp_path):
    descargador, llamadas = _descargador_contador(content_type="text/html")
    resultado = avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)

    assert resultado == ""
    assert len(llamadas) == 1


def test_respuesta_demasiado_grande_devuelve_vacio(tmp_path):
    grande = b"x" * (avatar_cache.LIMITE_BYTES + 1)
    descargador, llamadas = _descargador_contador(imagen=grande)
    resultado = avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)

    assert resultado == ""


def test_cache_caducada_vuelve_a_descargar(tmp_path):
    descargador, llamadas = _descargador_contador()
    avatar_cache.obtener(URL, EMAIL, tmp_path, descargador=descargador)
    assert len(llamadas) == 1

    ruta = avatar_cache.ruta_cache(tmp_path) / avatar_cache._nombre_fichero(EMAIL)
    edad_vieja = time.time() - (31 * 86400)
    os.utime(ruta, (edad_vieja, edad_vieja))

    avatar_cache.obtener(URL, EMAIL, tmp_path, max_edad_dias=30, descargador=descargador)
    assert len(llamadas) == 2


def test_limpiar_borra_solo_los_que_no_estan_vivos(tmp_path):
    descargador, _ = _descargador_contador()
    avatar_cache.obtener(URL, "vivo@aerotools.es", tmp_path, descargador=descargador)
    avatar_cache.obtener(URL, "muerto@aerotools.es", tmp_path, descargador=descargador)

    carpeta = avatar_cache.ruta_cache(tmp_path)
    assert len(list(carpeta.iterdir())) == 2

    avatar_cache.limpiar(tmp_path, {"vivo@aerotools.es"})

    restantes = list(carpeta.iterdir())
    assert len(restantes) == 1
    assert restantes[0].name == avatar_cache._nombre_fichero("vivo@aerotools.es")


def test_url_vacia_devuelve_vacio_sin_llamar_al_descargador(tmp_path):
    def descargador(url):
        raise AssertionError("no debería llamarse con url vacía")

    resultado = avatar_cache.obtener("", EMAIL, tmp_path, descargador=descargador)
    assert resultado == ""
