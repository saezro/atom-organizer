import os
import pytest
from external_tools import load_config_or_default, ReadLoadConfig


def test_load_config_or_default_missing_file(tmp_path):
    """Un ini inexistente debe devolver defaults sin lanzar excepción."""
    ini_inexistente = str(tmp_path / "no_existe.ini")
    resultado = load_config_or_default(ini_inexistente)
    assert resultado == {"ruta_thermoviewer": "", "percentage_by_models": {}}


def test_load_config_or_default_empty_path():
    """Un path vacío (diálogo cancelado) debe devolver defaults sin lanzar excepción."""
    resultado = load_config_or_default("")
    assert resultado == {"ruta_thermoviewer": "", "percentage_by_models": {}}


def test_load_config_or_default_missing_paths_section(tmp_path):
    """Un ini existente pero sin sección [paths] debe devolver default para ruta_thermoviewer."""
    ini_incompleto = tmp_path / "incompleto.ini"
    ini_incompleto.write_text("[percentage_by_models]\nM3T = 50\n")
    resultado = load_config_or_default(str(ini_incompleto))
    assert resultado["ruta_thermoviewer"] == ""
    assert resultado["percentage_by_models"] == {"M3T": 50}


def test_read_load_config_instance_missing_file_no_crash(tmp_path):
    """El objeto ReadLoadConfig no debe crashear al cargar un ini inexistente."""
    obj = ReadLoadConfig()
    obj.load_new_config(str(tmp_path / "no_existe.ini"))
    assert obj.ruta_thermoviewer == ""
    assert obj.percentage_by_models == {}
