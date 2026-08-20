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
    """Casa esta visible y ademas tiene perfil guardado -> `guardada` True,
    que es lo que deja al kiosco conectar sin abrir el teclado."""
    def _run_fake(cmd, **kw):
        salidas = {
            ("device", "wifi", "list"): "yes:Casa:80:WPA2\n",
            ("connection", "show"): "netplan-wlan0-Casa:802-11-wireless\n",
        }
        for clave, salida in salidas.items():
            if cmd[-len(clave):] == list(clave):
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=salida, stderr="")
        if cmd[:2] == ["nmcli", "-g"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="Casa\n", stderr="")
        raise AssertionError(f"comando inesperado: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    res = app_webview.Api().red_listar()
    assert res == {
        "ok": True,
        "actual": "Casa",
        "redes": [{
            "ssid": "Casa", "senal": 80, "segura": True,
            "activa": True, "guardada": True,
        }],
    }


def test_red_listar_marca_no_guardada_y_excluye_el_hotspot(monkeypatch):
    """Una red sin perfil pide clave, y el hotspot propio nunca sale como
    'guardada' aunque su conexion `atom-ap` exista."""
    def _run_fake(cmd, **kw):
        if cmd[-3:] == ["device", "wifi", "list"]:
            salida = f"no:Vecino:60:WPA2\nno:{app_webview._AP_SSID}:99:WPA2\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=salida, stderr="")
        if cmd[-2:] == ["connection", "show"]:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="atom-ap:802-11-wireless\n", stderr="")
        if cmd[:2] == ["nmcli", "-g"]:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=f"{app_webview._AP_SSID}\n", stderr="")
        raise AssertionError(f"comando inesperado: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    redes = {r["ssid"]: r for r in app_webview.Api().red_listar()["redes"]}
    assert redes["Vecino"]["guardada"] is False
    assert redes[app_webview._AP_SSID]["guardada"] is False


def test_red_conectar_repara_perfil_sin_key_mgmt(monkeypatch):
    """El perfil que deja netplan no trae `key-mgmt`: nmcli aborta, se
    completa el perfil y se reintenta, sin borrarlo (el rescate depende de el).
    """
    llamadas = []

    def _run_fake(cmd, **kw):
        llamadas.append(cmd)
        if cmd[:4] == ["nmcli", "device", "wifi", "connect"]:
            ya_reparado = any(c[:3] == ["nmcli", "connection", "modify"] for c in llamadas)
            if ya_reparado:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                cmd, returncode=4, stdout="",
                stderr="Error: 802-11-wireless-security.key-mgmt: property is missing.")
        if cmd[-2:] == ["connection", "show"]:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="netplan-wlan0-Casa:802-11-wireless\n", stderr="")
        if cmd[:2] == ["nmcli", "-g"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="Casa\n", stderr="")
        if cmd[:3] == ["nmcli", "connection", "modify"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        raise AssertionError(f"comando inesperado: {cmd}")

    monkeypatch.setattr(subprocess, "run", _run_fake)
    assert app_webview.Api().red_conectar("Casa", "secreta") == {"ok": True}

    modify = [c for c in llamadas if c[:3] == ["nmcli", "connection", "modify"]]
    assert modify == [[
        "nmcli", "connection", "modify", "netplan-wlan0-Casa",
        "802-11-wireless-security.key-mgmt", "wpa-psk",
        "802-11-wireless-security.psk", "secreta",
    ]]
    assert not any(c[:3] == ["nmcli", "connection", "delete"] for c in llamadas)


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
