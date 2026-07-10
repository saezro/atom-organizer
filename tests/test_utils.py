import os
import sys
import subprocess
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "general_functions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import (
    ExternalToolError,
    safe_pct,
    unique_dest,
    safe_move,
    safe_copy2,
    run_external,
)


def test_safe_pct_total_cero():
    assert safe_pct(0, 0) == 0

def test_safe_pct_normal():
    assert safe_pct(1, 4) == 25

def test_safe_pct_recortado_a_100():
    assert safe_pct(5, 4) == 100

def test_unique_dest_sin_colision(tmp_path):
    dest = tmp_path / "foto.jpg"
    assert unique_dest(str(dest)) == str(dest)

def test_unique_dest_con_colision(tmp_path):
    dest = tmp_path / "foto.jpg"
    dest.write_text("existente")
    resultado = unique_dest(str(dest))
    assert resultado == str(tmp_path / "foto_1.jpg")

def test_safe_move_mueve_archivo(tmp_path):
    src = tmp_path / "origen.jpg"
    src.write_text("contenido")
    dest = tmp_path / "destino.jpg"
    resultado = safe_move(str(src), str(dest))
    assert resultado == str(dest)
    assert not src.exists()
    assert dest.read_text() == "contenido"

def test_safe_move_no_sobreescribe_destino_existente(tmp_path):
    src = tmp_path / "origen.jpg"
    src.write_text("nuevo")
    dest = tmp_path / "destino.jpg"
    dest.write_text("original")
    resultado = safe_move(str(src), str(dest))
    assert resultado == str(tmp_path / "destino_1.jpg")
    assert dest.read_text() == "original"
    assert (tmp_path / "destino_1.jpg").read_text() == "nuevo"
    assert not src.exists()

def test_run_external_returncode_no_cero_lanza_error():
    with pytest.raises(ExternalToolError):
        run_external([sys.executable, "-c", "import sys;sys.exit(3)"])

def test_run_external_ok_devuelve_completed_process():
    resultado = run_external([sys.executable, "-c", "print(1)"])
    assert isinstance(resultado, subprocess.CompletedProcess)
    assert resultado.returncode == 0

def test_run_external_timeout_lanza_error():
    with pytest.raises(ExternalToolError):
        run_external(
            [sys.executable, "-c", "import time;time.sleep(5)"],
            timeout=0.2,
        )
