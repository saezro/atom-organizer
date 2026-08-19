import subprocess

import app_webview


def test_modo_invalido_no_ejecuta_nada(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("subprocess.run no debe llamarse con modo invalido")

    monkeypatch.setattr(subprocess, "run", _boom)
    res = app_webview.Api().sistema_apagar("shutdown_ya")
    assert res == {"ok": False, "error": "modo no valido"}


def test_modo_valido_devuelve_ok(monkeypatch):
    llamadas = []

    def _run_fake(cmd, **kw):
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().sistema_apagar("reboot")
    assert res == {"ok": True}
    assert llamadas == [["systemctl", "reboot"]]
