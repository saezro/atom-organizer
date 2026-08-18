import os

import app_webview


def test_lista_carpetas_y_ficheros_ordenados(tmp_path):
    # Subcarpeta propia: el fixture autouse `_subidas_log_aislado` de
    # conftest.py ya crea "Logs-subidas" directamente en tmp_path, y eso
    # contaminaria el listado si se usara tmp_path a pelo.
    base = tmp_path / "base"
    base.mkdir()
    (base / "zeta").mkdir()
    (base / "alfa").mkdir()
    (base / "b.txt").write_text("hola", encoding="utf-8")
    (base / "a.txt").write_text("x", encoding="utf-8")

    res = app_webview.Api().list_dir(str(base))

    assert res["ok"] is True
    assert [d["name"] for d in res["dirs"]] == ["alfa", "zeta"]
    assert [f["name"] for f in res["files"]] == ["a.txt", "b.txt"]
    assert res["files"][1]["size"] == 4
    assert res["path"] == str(base)


def test_expone_el_padre_para_poder_subir(tmp_path):
    hija = tmp_path / "hija"
    hija.mkdir()
    res = app_webview.Api().list_dir(str(hija))
    assert res["parent"] == str(tmp_path)


def test_la_raiz_no_tiene_padre():
    res = app_webview.Api().list_dir(os.path.abspath(os.sep))
    assert res["ok"] is True
    assert res["parent"] is None


def test_sin_ruta_arranca_en_el_home():
    res = app_webview.Api().list_dir(None)
    assert res["ok"] is True
    assert res["path"] == os.path.expanduser("~")


def test_ruta_inexistente_devuelve_error_no_excepcion(tmp_path):
    res = app_webview.Api().list_dir(str(tmp_path / "no-existe"))
    assert res["ok"] is False
    assert "error" in res


def test_una_entrada_ilegible_no_tumba_el_listado(tmp_path, monkeypatch):
    # Misma razon que en el test de arriba: subcarpeta propia para no
    # arrastrar "Logs-subidas" del fixture autouse de conftest.py.
    base = tmp_path / "base"
    base.mkdir()
    (base / "buena").mkdir()
    real = os.stat

    def _stat_selectivo(ruta, *a, **kw):
        if "mala" in str(ruta):
            raise PermissionError("denegado")
        return real(ruta, *a, **kw)

    (base / "mala.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(os, "stat", _stat_selectivo)
    res = app_webview.Api().list_dir(str(base))
    assert res["ok"] is True
    assert [d["name"] for d in res["dirs"]] == ["buena"]
