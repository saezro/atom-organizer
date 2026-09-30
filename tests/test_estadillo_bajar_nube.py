import pytest

import app_webview
from atom_core import cloud_upload


class _AuthFake:
    """Lo mínimo que `estadillo_bajar_nube` mira del auth: si hay sesión."""

    def __init__(self, logueado=True):
        self._logueado = logueado

    def is_logged_in(self):
        return self._logueado


@pytest.fixture
def api():
    return app_webview.Api()


@pytest.fixture
def api_con_sesion(api, monkeypatch):
    monkeypatch.setattr(api, "_get_auth", lambda: _AuthFake())
    return api


def _remotos(prefix, nombres):
    """`listar_objetos_remotos` real devuelve dict nombre->RemoteObject; aquí
    solo hace falta que las claves sean los nombres completos del objeto."""
    return {f"{prefix.rstrip('/')}/{n}": cloud_upload.RemoteObject(f"{prefix.rstrip('/')}/{n}", 10)
            for n in nombres}


def test_baja_csv_y_xlsx_ignorando_manifest_y_normalizado(api_con_sesion, monkeypatch, tmp_path):
    monkeypatch.setattr(
        cloud_upload, "listar_objetos_remotos",
        lambda bucket, prefix, auth, **kw: _remotos(
            prefix, ["01__abcd1234.csv", "manifest.json", "estadillo.json"]),
    )
    descargas = []
    monkeypatch.setattr(
        cloud_upload, "descargar_objeto",
        lambda bucket, name, auth, dest_path, **kw: descargas.append((name, dest_path)) or dest_path.write_text("x"),
    )
    monkeypatch.setattr(
        "atom_core.google_auth.estadillos_recibidos_dir", lambda: tmp_path)

    res = api_con_sesion.estadillo_bajar_nube("MI_PLANTA")

    assert res["ok"] is True
    assert res["error"] is None
    assert len(res["rutas"]) == 1
    assert res["rutas"][0]["nombre"] == "01__abcd1234.csv"
    assert len(descargas) == 1


def test_error_sin_ningun_estadillo(api_con_sesion, monkeypatch, tmp_path):
    monkeypatch.setattr(
        cloud_upload, "listar_objetos_remotos",
        lambda bucket, prefix, auth, **kw: _remotos(prefix, ["manifest.json", "estadillo.json"]),
    )
    monkeypatch.setattr(
        "atom_core.google_auth.estadillos_recibidos_dir", lambda: tmp_path)

    res = api_con_sesion.estadillo_bajar_nube("MI_PLANTA")

    assert res["ok"] is False
    assert res["rutas"] == []
    assert res["error"]


def test_falla_open_sin_login(api, monkeypatch):
    monkeypatch.setattr(api, "_get_auth", lambda: _AuthFake(logueado=False))

    res = api.estadillo_bajar_nube("MI_PLANTA")

    assert res["ok"] is False
    assert res["error"]


def test_no_sobrescribe_anade_sufijo(api_con_sesion, monkeypatch, tmp_path):
    monkeypatch.setattr(
        cloud_upload, "listar_objetos_remotos",
        lambda bucket, prefix, auth, **kw: _remotos(prefix, ["01__abcd1234.csv"]),
    )
    destino_dir = tmp_path / "nube"
    destino_dir.mkdir(parents=True)
    (destino_dir / "01__abcd1234.csv").write_text("ya existía")

    monkeypatch.setattr(
        cloud_upload, "descargar_objeto",
        lambda bucket, name, auth, dest_path, **kw: dest_path.write_text("nuevo"),
    )
    monkeypatch.setattr(
        "atom_core.google_auth.estadillos_recibidos_dir", lambda: tmp_path)

    res = api_con_sesion.estadillo_bajar_nube("MI_PLANTA")

    assert res["ok"] is True
    assert res["rutas"][0]["nombre"] == "01__abcd1234_1.csv"
    assert (destino_dir / "01__abcd1234.csv").read_text() == "ya existía"


def test_construye_el_prefijo_bajo_actual(api_con_sesion, monkeypatch, tmp_path):
    llamadas = []

    def _fake(bucket, prefix, auth, **kw):
        llamadas.append(prefix)
        return {}

    monkeypatch.setattr(cloud_upload, "listar_objetos_remotos", _fake)
    monkeypatch.setattr(
        "atom_core.google_auth.estadillos_recibidos_dir", lambda: tmp_path)

    api_con_sesion.estadillo_bajar_nube("MI_PLANTA")

    assert len(llamadas) == 1
    assert llamadas[0].endswith("/ESTADILLOS/actual/")
