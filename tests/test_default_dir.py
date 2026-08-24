import os
import sys

import app_webview


def test_no_linux_devuelve_siempre_el_home(monkeypatch):
    # Windows/pywebview es produccion actual: no debe cambiar nunca.
    monkeypatch.setattr(sys, "platform", "win32")
    res = app_webview.Api().default_dir()
    assert res == {"ok": True, "path": os.path.expanduser("~")}


def test_linux_sin_candidatos_devuelve_el_listado_del_home(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    import glob

    monkeypatch.setattr(glob, "glob", lambda patron: [])
    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["path"] == os.path.expanduser("~")
    # Mismo shape que list_dir(): dirs/files/parent, no solo ok/path.
    assert "dirs" in res and "files" in res and "parent" in res


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


def test_linux_candidato_en_otro_dispositivo_se_usa(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
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
            # Mismo stat real (para que isdir/etc sigan funcionando), pero
            # con st_dev distinto al de la raiz: es lo que lo marca como
            # "disco extra".
            campos = list(original)
            campos[2] = real_stat("/").st_dev + 1
            return os.stat_result(campos)
        return original

    monkeypatch.setattr(os, "stat", _stat_fake)

    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["path"] == str(disco)
    # Ya trae el listado del disco, sin necesitar una segunda llamada.
    assert [f["name"] for f in res["files"]] == ["archivo.txt"]
    assert res["dirs"] == []


def test_linux_no_lee_el_disco_entero_para_ver_si_esta_vacio(monkeypatch, tmp_path):
    # Regresion: la comprobacion de "esta vacio" debe pararse en la primera
    # entrada (scandir perezoso), no leer el directorio completo (listdir)
    # como antes. El listado final SI usa listdir (una vez, dentro de
    # list_dir, para construir la respuesta), asi que lo que se comprueba
    # aqui es que os.listdir se llama como mucho una vez sobre el
    # candidato (la del listado final), no dos (chequeo + listado).
    monkeypatch.setattr(sys, "platform", "linux")
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

    real_listdir = os.listdir
    llamadas = []

    def _listdir_contado(ruta, *a, **kw):
        if str(ruta) == str(disco):
            llamadas.append(ruta)
        return real_listdir(ruta, *a, **kw)

    monkeypatch.setattr(os, "listdir", _listdir_contado)

    res = app_webview.Api().default_dir()
    assert res["ok"] is True
    assert res["path"] == str(disco)
    assert len(llamadas) == 1


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
