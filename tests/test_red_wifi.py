import subprocess

import app_webview


def test_parse_nmcli_wifi_ssid_escapado_dedup_orden():
    salida = (
        "yes:Casa\\:2.4G:70:WPA2\n"
        "no:Casa\\:2.4G:90:WPA2\n"
        "no:Vecino:50:\n"
        "no:Vecino:40:\n"
        "no::30:WPA2\n"
    )
    redes, actual = app_webview._parse_nmcli_wifi(salida)

    assert actual == "Casa:2.4G"
    assert redes == [
        {"ssid": "Casa:2.4G", "senal": 90, "segura": True, "activa": False},
        {"ssid": "Vecino", "senal": 50, "segura": False, "activa": False},
    ]


def test_parse_nmcli_wifi_red_segura_vs_abierta():
    salida = "no:Abierta:60:--\nno:Segura:60:WPA2\n"
    redes, actual = app_webview._parse_nmcli_wifi(salida)

    assert actual is None
    por_ssid = {r["ssid"]: r for r in redes}
    assert por_ssid["Abierta"]["segura"] is False
    assert por_ssid["Segura"]["segura"] is True


def test_red_listar_ok(monkeypatch):
    def _run_fake(cmd, **kw):
        assert cmd == ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout="yes:Casa:80:WPA2\n", stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().red_listar()
    assert res == {
        "ok": True,
        "actual": "Casa",
        "redes": [{"ssid": "Casa", "senal": 80, "segura": True, "activa": True}],
    }


def test_red_listar_error_nmcli(monkeypatch):
    def _run_fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="nmcli: dispositivo no encontrado")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().red_listar()
    assert res == {"ok": False, "error": "nmcli: dispositivo no encontrado"}


def test_red_conectar_con_password_ok(monkeypatch):
    llamadas = []

    def _run_fake(cmd, **kw):
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().red_conectar("Casa", "secreta123")
    assert res == {"ok": True}
    assert llamadas == [["nmcli", "device", "wifi", "connect", "Casa", "password", "secreta123"]]


def test_red_conectar_sin_password_ok(monkeypatch):
    llamadas = []

    def _run_fake(cmd, **kw):
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().red_conectar("Abierta")
    assert res == {"ok": True}
    assert llamadas == [["nmcli", "device", "wifi", "connect", "Abierta"]]


def test_red_conectar_no_filtra_password_en_error(monkeypatch):
    def _run_fake(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="",
            stderr="Error: password secreta123 no valida para Casa",
        )

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().red_conectar("Casa", "secreta123")
    assert res["ok"] is False
    assert "secreta123" not in res["error"]
    assert "***" in res["error"]


def test_red_conectar_no_filtra_password_en_excepcion(monkeypatch):
    def _run_boom(cmd, **kw):
        raise RuntimeError("fallo llamando a nmcli con secreta123")

    monkeypatch.setattr(subprocess, "run", _run_boom)
    res = app_webview.Api().red_conectar("Casa", "secreta123")
    assert res["ok"] is False
    assert "secreta123" not in res["error"]
