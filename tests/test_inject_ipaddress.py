import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "inject_ipaddress.py"


@pytest.mark.parametrize("relativa", ["_internal/base_library.zip", "base_library.zip"])
def test_inyecta_ipaddress_en_layout_pyinstaller_actual_y_legacy(tmp_path, relativa):
    destino = tmp_path / "dist" / "atom_organizer" / relativa
    destino.parent.mkdir(parents=True)
    with zipfile.ZipFile(destino, "w"):
        pass

    resultado = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, text=True, capture_output=True
    )

    assert resultado.returncode == 0, resultado.stderr
    with zipfile.ZipFile(destino) as archivo:
        assert "ipaddress.pyc" in archivo.namelist()
    assert not (tmp_path / "dist" / "atom_organizer" / "ipaddress.pyc").exists()


def test_falla_si_no_existe_base_library(tmp_path):
    resultado = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, text=True, capture_output=True
    )

    assert resultado.returncode == 1
    assert "_internal/base_library.zip" in resultado.stderr
