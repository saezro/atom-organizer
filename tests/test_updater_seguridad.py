"""Endurecimiento del auto-update: lista blanca de hosts y verificación SHA-256."""
import hashlib
import io

import pytest

from atom_core import updater

NAME = "ATOM-Organizer-Setup-v9.9.9.exe"
URL = f"https://github.com/saezro/atom-organizer/releases/download/v9.9.9/{NAME}"
PAYLOAD = b"x" * (21 * 1024 * 1024)
GOOD = hashlib.sha256(PAYLOAD).hexdigest()


class FakeResp(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self._url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self._url


@pytest.fixture
def red(monkeypatch, tmp_path):
    """Sustituye _open: sirve el instalador y, opcionalmente, SHA256SUMS."""
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    estado = {"sums": f"{GOOD}  {NAME}\n".encode(), "abiertas": []}

    def fake_open(url):
        estado["abiertas"].append(url)
        if url.endswith(updater.SUMS_NAME):
            if estado["sums"] is None:
                raise OSError("HTTP 404")
            return FakeResp(estado["sums"], url)
        return FakeResp(PAYLOAD, url)

    monkeypatch.setattr(updater, "_open", fake_open)
    return estado


def test_host_no_permitido_no_abre_nada(red):
    r = updater.download(f"https://evil.example.com/{NAME}")
    assert r["ok"] is False and "no permitida" in r["error"]
    assert red["abiertas"] == []


def test_http_plano_rechazado(red):
    r = updater.download(URL.replace("https:", "http:"))
    assert r["ok"] is False and "no permitida" in r["error"]


def test_redireccion_a_host_no_permitido(monkeypatch):
    h = updater._HostCheckRedirect()
    req = updater.urllib.request.Request(URL)
    with pytest.raises(updater.UpdateSecurityError):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/a.exe")


def test_redireccion_a_host_permitido_ok():
    h = updater._HostCheckRedirect()
    req = updater.urllib.request.Request(URL)
    nueva = h.redirect_request(req, None, 302, "Found", {},
                               "https://release-assets.githubusercontent.com/x/y")
    assert nueva is not None


def test_checksum_ok(red):
    r = updater.download(URL)
    assert r["ok"] is True and r["sha256"] == GOOD
    assert (updater.Path(r["path"])).exists()


def test_checksum_mismatch_borra_y_no_instala(red):
    red["sums"] = f"{'0' * 64}  {NAME}\n".encode()
    r = updater.download(URL)
    assert r["ok"] is False and "integridad" in r["error"]
    assert not (updater.Path(updater.tempfile.gettempdir()) / "atom-organizer-update" / NAME).exists()


def test_falta_sha256sums_error_visible(red):
    red["sums"] = None
    r = updater.download(URL)
    assert r["ok"] is False and updater.SUMS_NAME in r["error"]
    assert red["abiertas"] == [URL.rsplit("/", 1)[0] + "/" + updater.SUMS_NAME]  # no descarga el .exe


def test_sums_sin_linea_del_asset(red):
    red["sums"] = f"{GOOD}  otro.AppImage\n".encode()
    r = updater.download(URL)
    assert r["ok"] is False and NAME in r["error"]


# ---- Fase seguridad 2: URL/nombre confinados, binding en backend, Host ----
@pytest.mark.parametrize("url", [
    "https://github.com/otro/repo/releases/download/v1/" + NAME,
    "https://github.com/saezro/atom-organizer/archive/" + NAME,
    "https://github.com/saezro/atom-organizer/releases/download/v1/..%2F..%2Fevil.exe",
    "https://github.com/saezro/atom-organizer/releases/download/v1/evil.exe",
    "https://evil.com/saezro/atom-organizer/releases/download/v1/" + NAME,
    "http://github.com/saezro/atom-organizer/releases/download/v1/" + NAME,
])
def test_download_rechaza_url_ajena(red, url):
    res = updater.download(url)
    assert res["ok"] is False
    assert red["abiertas"] == []


def test_validate_devuelve_basename():
    assert updater.validate_asset_url(URL) == NAME


def test_install_rechaza_hash_distinto(tmp_path, monkeypatch):
    exe = tmp_path / NAME
    exe.write_bytes(b"abc")
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    lanzado = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: lanzado.append(a))
    res = updater.install(str(exe), expected_sha256="0" * 64)
    assert res["ok"] is False and not lanzado


def _api():
    import app_webview
    api = app_webview.Api.__new__(app_webview.Api)
    api._downloading = False
    api._update_path = None
    api._update_sha256 = None
    api._update_asset_url = None
    api._sink = None
    api._push_update = lambda d: None
    return api


def test_backend_ignora_url_del_js(monkeypatch):
    api = _api()
    monkeypatch.setattr(updater, "check", lambda: {"asset_url": URL})
    api.check_update()
    r = api.download_update("https://evil.com/x.exe")
    assert r["started"] is False


def test_backend_sin_check_no_descarga():
    assert _api().download_update(URL)["started"] is False


def test_install_update_ignora_path_del_js():
    r = _api().install_update("C:/evil.exe")
    assert r["ok"] is False
