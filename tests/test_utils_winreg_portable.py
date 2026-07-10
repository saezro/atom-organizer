"""
Verifica la portabilidad del import de `winreg`.

`utils.py` ya NO importa `winreg` a nivel de módulo: el import es diferido
dentro de `Utils.get_program_dir_installation` y está protegido con try/except
ImportError. Por eso `import utils` debe funcionar en Linux/macOS SIN el stub
que conftest.py registra para el resto de la suite.

Como conftest.py mete un stub de `winreg` en sys.modules ANTES de recolectar
tests, aquí lanzamos un SUBPROCESO limpio (sin conftest) para comprobar el
import real y la degradación de la función en plataformas sin registro Windows.
"""
import os
import subprocess
import sys
import textwrap

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_subprocess(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )


def test_utils_importa_sin_stub_de_winreg():
    """`import utils` funciona en un intérprete limpio sin winreg disponible."""
    res = _run_subprocess(
        """
        import sys
        # Garantizar que winreg NO esté precargado (en Linux/macOS no existe).
        sys.modules.pop("winreg", None)
        import utils
        assert "winreg" not in sys.modules, "utils no debe importar winreg al cargarse"
        print("IMPORT_OK")
        """
    )
    assert res.returncode == 0, f"stdout={res.stdout!r} stderr={res.stderr!r}"
    assert "IMPORT_OK" in res.stdout


def test_get_program_dir_installation_degrada_en_no_windows():
    """En plataformas sin winreg la función devuelve None sin lanzar."""
    res = _run_subprocess(
        """
        import logging, sys
        from types import SimpleNamespace
        sys.modules.pop("winreg", None)
        import utils

        fake_self = SimpleNamespace(
            organizer_logger=SimpleNamespace(logger=logging.getLogger("winreg_test"))
        )
        result = utils.Utils.get_program_dir_installation(fake_self, "Programa Inexistente XYZ")
        if sys.platform.startswith("win"):
            # En Windows sí hay registro; solo exigimos que no lance.
            print("WINDOWS_OK")
        else:
            assert result is None, f"esperaba None en no-Windows, obtuve {result!r}"
            print("DEGRADA_A_NONE")
        """
    )
    assert res.returncode == 0, f"stdout={res.stdout!r} stderr={res.stderr!r}"
    assert ("DEGRADA_A_NONE" in res.stdout) or ("WINDOWS_OK" in res.stdout)
