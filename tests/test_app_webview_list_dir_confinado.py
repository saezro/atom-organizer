"""Confinamiento del selector de carpetas del kiosco (`Api.list_dir` en
Linux/Raspberry Pi): en la pantalla táctil de 480x320 el operador no puede
salir de los discos externos montados ni hurgar por el sistema de ficheros.

El backend es la autoridad (no solo la UI): estas pruebas llaman
directamente a `Api.list_dir`/`Api.default_dir` con `estado_lan.es_raspberry`
forzado a True y `estado_lan.discos_externos` monkeypatcheado a discos
ficticios en `tmp_path`, sin depender de que la máquina que corre el test
tenga un disco USB real montado ni sea una Raspberry Pi de verdad.
"""
from __future__ import annotations

import os

import pytest

import app_webview as aw


@pytest.fixture(autouse=True)
def forzar_linux(monkeypatch):
    monkeypatch.setattr(aw.sys, "platform", "linux")
    monkeypatch.setattr(aw.estado_lan, "es_raspberry", lambda: True)


@pytest.fixture
def discos(tmp_path, monkeypatch):
    """Dos discos ficticios montados, con carpetas de sistema y un symlink
    que se escapa de uno de ellos, para ejercitar el confinamiento."""
    disco1 = tmp_path / "media" / "USB_HDD"
    disco2 = tmp_path / "media" / "PLANTA_C"
    fuera = tmp_path / "fuera_del_disco"
    for d in (disco1, disco2, fuera):
        d.mkdir(parents=True)

    (disco1 / "FOTOS").mkdir()
    (disco1 / "$RECYCLE.BIN").mkdir()
    (disco1 / "System Volume Information").mkdir()
    (disco1 / "lost+found").mkdir()
    (disco1 / ".oculta").mkdir()
    (disco1 / "FOTOS" / "PLANTA_A").mkdir()
    os.symlink(fuera, disco1 / "enlace_fuera")

    lista = [
        {"nombre": "USB_HDD", "punto_montaje": str(disco1), "libre_gb": 12.3, "total_gb": 64.0},
        {"nombre": "PLANTA_C", "punto_montaje": str(disco2), "libre_gb": 3.0, "total_gb": 32.0},
    ]
    monkeypatch.setattr(aw.estado_lan, "discos_externos", lambda: lista)
    return {"disco1": disco1, "disco2": disco2, "fuera": fuera}


def _api():
    return aw.Api(broker=True)


# ---- raíz = lista de discos --------------------------------------------------

def test_raiz_sin_path_es_la_lista_de_discos(discos):
    r = _api().list_dir(None)
    assert r["ok"] is True
    assert r["is_root"] is True
    assert r["parent"] is None
    nombres = {d["name"] for d in r["dirs"]}
    assert nombres == {"USB_HDD", "PLANTA_C"}
    assert r["files"] == []
    disco1 = next(d for d in r["dirs"] if d["name"] == "USB_HDD")
    assert disco1["libre_gb"] == 12.3
    assert disco1["total_gb"] == 64.0


def test_default_dir_arranca_en_la_lista_de_discos(discos):
    r = _api().default_dir()
    assert r["ok"] is True
    assert r["is_root"] is True
    assert {d["name"] for d in r["dirs"]} == {"USB_HDD", "PLANTA_C"}


def test_raiz_sin_discos_montados_es_lista_vacia(monkeypatch):
    monkeypatch.setattr(aw.estado_lan, "discos_externos", lambda: [])
    r = _api().list_dir(None)
    assert r["ok"] is True
    assert r["is_root"] is True
    assert r["dirs"] == []


# ---- dentro de un disco: confinado -------------------------------------------

def test_lista_dentro_del_disco_filtra_carpetas_de_sistema_y_ocultas(discos):
    r = _api().list_dir(str(discos["disco1"]))
    assert r["ok"] is True
    assert r["is_root"] is False
    assert r["disk_name"] == "USB_HDD"
    assert r["rel_parts"] == []
    # raiz del disco: sin ".. subir" (no hay a donde subir dentro del confinamiento)
    assert r["parent"] is None
    nombres = {d["name"] for d in r["dirs"]}
    assert nombres == {"FOTOS"}
    assert "$RECYCLE.BIN" not in nombres
    assert "System Volume Information" not in nombres
    assert "lost+found" not in nombres
    assert ".oculta" not in nombres
    assert "enlace_fuera" not in nombres  # symlink que escapa del disco


def test_subcarpeta_trae_rel_parts_y_parent_dentro_del_disco(discos):
    ruta = str(discos["disco1"] / "FOTOS")
    r = _api().list_dir(ruta)
    assert r["ok"] is True
    assert r["disk_name"] == "USB_HDD"
    assert r["rel_parts"] == ["FOTOS"]
    assert r["parent"] == str(discos["disco1"])
    assert {d["name"] for d in r["dirs"]} == {"PLANTA_A"}


def test_ruta_fuera_de_todo_disco_se_rechaza(discos):
    r = _api().list_dir(str(discos["fuera"]))
    assert r["ok"] is False
    assert "error" in r


def test_dotdot_escapando_del_disco_se_rechaza(discos):
    # Sube desde dentro del disco hasta salirse de /media por completo.
    escapada = str(discos["disco1"] / ".." / ".." / "fuera_del_disco")
    r = _api().list_dir(escapada)
    assert r["ok"] is False


def test_symlink_como_path_directo_tambien_se_rechaza(discos):
    r = _api().list_dir(str(discos["disco1"] / "enlace_fuera"))
    assert r["ok"] is False
