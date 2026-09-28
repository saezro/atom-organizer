import os
import sys

import app_webview


def test_no_linux_devuelve_siempre_el_home(monkeypatch):
    # Windows/pywebview es produccion actual: no debe cambiar nunca.
    monkeypatch.setattr(sys, "platform", "win32")
    res = app_webview.Api().default_dir()
    assert res == {"ok": True, "path": os.path.expanduser("~")}


def test_linux_no_raspberry_devuelve_siempre_el_home(monkeypatch):
    # PC/AppImage de escritorio en Linux: aunque sys.platform sea "linux",
    # sin ser una Raspberry real el comportamiento es el mismo que en
    # Windows (navegacion libre, arranca en home), no el confinamiento
    # a discos externos del kiosco.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app_webview.estado_lan, "es_raspberry", lambda: False)
    res = app_webview.Api().default_dir()
    assert res == {"ok": True, "path": os.path.expanduser("~")}


def test_raspberry_sin_discos_devuelve_la_lista_vacia_de_discos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app_webview.estado_lan, "es_raspberry", lambda: True)
    monkeypatch.setattr(app_webview.estado_lan, "discos_externos", lambda: [])
    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["is_root"] is True
    assert res["dirs"] == []


def test_linux_candidato_en_el_mismo_dispositivo_se_descarta(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    import glob

    disco = tmp_path / "media" / "pi" / "USB"
    disco.mkdir(parents=True)
    (disco / "archivo.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        glob, "glob",
        lambda patron: [str(disco)] if patron == "/media/*/*" else [],
    )
    # Mismo st_dev que la raiz -> no es un disco "extra", se descarta.
    real_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda ruta, *a, **kw: real_stat("/"))

    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["path"] == os.path.expanduser("~")


def test_raspberry_con_un_disco_devuelve_la_lista_de_discos_no_su_contenido(monkeypatch, tmp_path):
    # default_dir() en la Raspberry es list_dir(None): el nivel superior es
    # SIEMPRE la lista de discos (aunque haya solo uno), nunca se entra sola
    # dentro de el -eso lo decide el operador tocando en la UI-.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app_webview.estado_lan, "es_raspberry", lambda: True)

    disco = tmp_path / "media" / "pi" / "USB"
    disco.mkdir(parents=True)
    (disco / "archivo.txt").write_text("x", encoding="utf-8")

    lista = [{"nombre": "USB", "punto_montaje": str(disco), "libre_gb": 1.0, "total_gb": 2.0}]
    monkeypatch.setattr(app_webview.estado_lan, "discos_externos", lambda: lista)

    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["is_root"] is True
    assert [d["name"] for d in res["dirs"]] == ["USB"]


def test_linux_excepcion_en_el_escaneo_cae_al_home(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    import glob

    def _boom(patron):
        raise OSError("disco a medio montar")

    monkeypatch.setattr(glob, "glob", _boom)
    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["path"] == os.path.expanduser("~")


def test_disco_externo_sin_candidatos_devuelve_none(monkeypatch):
    import glob

    monkeypatch.setattr(glob, "glob", lambda patron: [])
    assert app_webview._disco_externo() is None


def test_disco_externo_con_candidato_devuelve_la_ruta(monkeypatch, tmp_path):
    import glob

    disco = tmp_path / "media" / "pi" / "USB"
    disco.mkdir(parents=True)
    (disco / "archivo.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        glob, "glob",
        lambda patron: [str(disco)] if patron == "/media/*/*" else [],
    )

    real_stat = os.stat

    def _stat_fake(ruta, *a, **kw):
        original = real_stat(ruta, *a, **kw)
        if str(ruta) == str(disco):
            campos = list(original)
            campos[2] = real_stat("/").st_dev + 1
            return os.stat_result(campos)
        return original

    monkeypatch.setattr(os, "stat", _stat_fake)

    assert app_webview._disco_externo() == str(disco)


def test_disco_estado_no_linux_siempre_desconectado(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(app_webview, "_disco_externo", lambda: "/media/pi/USB")
    res = app_webview.Api().disco_estado()
    assert res == {"ok": True, "conectado": False}


def test_disco_estado_linux_sin_disco(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app_webview, "_disco_externo", lambda: None)
    res = app_webview.Api().disco_estado()
    assert res == {"ok": True, "conectado": False}


def test_disco_estado_linux_con_disco(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app_webview, "_disco_externo", lambda: "/media/pi/USB")
    res = app_webview.Api().disco_estado()
    assert res == {"ok": True, "conectado": True}


def test_disco_estado_excepcion_devuelve_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom():
        raise OSError("disco a medio montar")

    monkeypatch.setattr(app_webview, "_disco_externo", _boom)
    res = app_webview.Api().disco_estado()
    assert res["ok"] is False
    assert "error" in res
