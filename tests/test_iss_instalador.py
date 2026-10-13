"""Contrato del .iss: evita mezcla exe nuevo + _internal viejo en updates."""
import re
from pathlib import Path

ISS = (Path(__file__).resolve().parents[1] / "packaging" / "windows"
       / "ATOM-Organizer.iss").read_text(encoding="utf-8")
# Sin comentarios `;` para no dar falsos positivos con el texto explicativo.
CODIGO = "\n".join(l for l in ISS.splitlines() if not l.lstrip().startswith(";"))


def test_setupmutex_presente_y_appmutex_ausente():
    assert re.search(r"^SetupMutex=ATOM-Organizer-Setup\s*$", CODIGO, re.M)
    assert not re.search(r"^AppMutex\s*=", CODIGO, re.M)


def test_restartapplications_ausente():
    assert not re.search(r"^RestartApplications\s*=", CODIGO, re.M)
    assert re.search(r"^CloseApplications=yes", CODIGO, re.M)


def test_code_con_prepare_to_install():
    assert "[Code]" in CODIGO
    assert "function PrepareToInstall(var NeedsRestart: Boolean): String;" in CODIGO
    assert "QtCore.pyd" in CODIGO
    assert "taskkill.exe" in CODIGO


def test_run_relanza_con_wizardsilent():
    run = CODIGO.split("[Run]")[1].split("[UninstallDelete]")[0]
    assert "Check: WizardSilent" in run


def test_run_de_relanzado_lleva_tras_actualizar():
    run = CODIGO.split("[Run]")[1].split("[UninstallDelete]")[0]
    linea = [l for l in run.splitlines() if "Check: WizardSilent" in l][0]
    assert 'Parameters: "--tras-actualizar"' in linea


def test_prepare_to_install_sin_rename_ni_powershell():
    code = CODIGO.split("[Code]")[1]
    assert "RenameFile" not in code
    assert "powershell" not in code.lower()
    assert "Get-CimInstance" not in code
    assert "tasklist" in code
    assert "GetTickCount" in code
    assert "fmShareExclusive" in code
