"""Regresión: `organizar_plan_apply` (motor nuevo, `atom_core.phases.py`) dejó
de crear `ESTADILLOS/` en la salida porque nunca copiaba el/los estadillo(s),
a diferencia del motor viejo (`pipeline.py:1203-1253`).

Estos tests cubren directamente `atom_core.phases._copiar_estadillos_a_salida`
(la función auxiliar que replica esa copia) sin levantar el pipeline
completo -índice/manifiesto/apply-, porque el comportamiento a comprobar es
puramente de sistema de ficheros: crear la carpeta, copiar con el nombre
original y resolver colisiones con un sufijo `_1`, `_2`… Estilo de la casa:
dobles a mano, nunca `unittest.mock` (ver `tests/test_etapas_pipeline.py`).
"""
import logging
from types import SimpleNamespace

from atom_core.phases import _copiar_estadillos_a_salida


def _logger() -> logging.Logger:
    return logging.getLogger("test_estadillos_plan_apply")


def test_crea_estadillos_vacio_sin_estadillo_configurado(tmp_path):
    output_folder = tmp_path / "salida"
    output_folder.mkdir()
    cfg = SimpleNamespace(output_folder=str(output_folder), estad="")

    _copiar_estadillos_a_salida(cfg, _logger())

    carpeta_estadillos = output_folder / "ESTADILLOS"
    assert carpeta_estadillos.is_dir()
    assert list(carpeta_estadillos.iterdir()) == []


def test_copia_estadillo_con_nombre_original(tmp_path):
    output_folder = tmp_path / "salida"
    output_folder.mkdir()
    estadillo = tmp_path / "Estadillo_PLANTA.csv"
    estadillo.write_text("Empresa;Trabajo\nAerotools;Test\n", encoding="utf-8")

    cfg = SimpleNamespace(output_folder=str(output_folder), estad=str(estadillo))

    _copiar_estadillos_a_salida(cfg, _logger())

    destino = output_folder / "ESTADILLOS" / "Estadillo_PLANTA.csv"
    assert destino.is_file()
    assert destino.read_text(encoding="utf-8") == estadillo.read_text(encoding="utf-8")


def test_colision_de_nombre_anade_sufijo_contador(tmp_path):
    output_folder = tmp_path / "salida"
    output_folder.mkdir()

    origen_1 = tmp_path / "origen_1" / "Estadillo.csv"
    origen_1.parent.mkdir()
    origen_1.write_text("contenido_1", encoding="utf-8")

    origen_2 = tmp_path / "origen_2" / "Estadillo.csv"
    origen_2.parent.mkdir()
    origen_2.write_text("contenido_2", encoding="utf-8")

    logger = _logger()

    cfg_1 = SimpleNamespace(output_folder=str(output_folder), estad=str(origen_1))
    _copiar_estadillos_a_salida(cfg_1, logger)

    cfg_2 = SimpleNamespace(output_folder=str(output_folder), estad=str(origen_2))
    _copiar_estadillos_a_salida(cfg_2, logger)

    carpeta_estadillos = output_folder / "ESTADILLOS"
    original = carpeta_estadillos / "Estadillo.csv"
    con_sufijo = carpeta_estadillos / "Estadillo_1.csv"

    assert original.is_file()
    assert original.read_text(encoding="utf-8") == "contenido_1"
    assert con_sufijo.is_file()
    assert con_sufijo.read_text(encoding="utf-8") == "contenido_2"
