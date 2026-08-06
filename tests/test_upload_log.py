"""Tests de `atom_core.upload_log` — el rastro que deja una subida.

Antes de esto, una subida no escribía NADA en disco: el progreso iba por un
evento a React y desaparecía al cerrar la app. Cuando el operador decía «se
quedó colgado» o «tardó dos horas» no había ni una línea que mirar. Lo que se
comprueba aquí es que ese fichero existe, se puede escribir y que importar el
módulo de subida no lo crea por su cuenta.
"""
from __future__ import annotations

import logging

from atom_core import upload_log


def test_escribe_en_un_fichero_que_se_puede_leer_despues(tmp_path):
    upload_log._reset_para_tests()
    try:
        log = upload_log.get_logger(tmp_path)
        log.warning("corte de red en DJI_0001.JPG")
        for h in log.handlers:
            h.flush()

        destino = tmp_path / upload_log.FILENAME
        assert destino.exists()
        assert "corte de red en DJI_0001.JPG" in destino.read_text(encoding="utf-8")
    finally:
        upload_log._reset_para_tests()


def test_pedirlo_dos_veces_no_duplica_las_lineas(tmp_path):
    """`upload_plan` lo pide en cada subida; sin esto, la tercera se vería por
    triplicado."""
    upload_log._reset_para_tests()
    try:
        upload_log.get_logger(tmp_path)
        log = upload_log.get_logger(tmp_path)
        log.info("una sola vez")
        for h in log.handlers:
            h.flush()

        texto = (tmp_path / upload_log.FILENAME).read_text(encoding="utf-8")
        assert texto.count("una sola vez") == 1
    finally:
        upload_log._reset_para_tests()


def test_un_directorio_imposible_no_tumba_la_subida(tmp_path):
    """Escribir el log es lo accesorio; subir es el trabajo de verdad."""
    upload_log._reset_para_tests()
    try:
        imposible = tmp_path / "fichero-no-carpeta"
        imposible.write_text("soy un fichero")
        log = upload_log.get_logger(imposible / "dentro")
        log.error("esto no va a ninguna parte, pero no revienta")
    finally:
        upload_log._reset_para_tests()


def test_importar_cloud_upload_no_crea_ficheros_por_su_cuenta():
    """El logger se configura al subir, no al importar: los tests y el arranque
    de la app no tienen por qué tocar el disco."""
    upload_log._reset_para_tests()
    from atom_core import cloud_upload  # noqa: F401

    assert logging.getLogger(upload_log.LOGGER_NAME).handlers == []
