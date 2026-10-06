"""Destino válido = vacío o ya organizado (manifiesto legible); lock de destino."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest

import app_webview as aw
from atom_core import lock_destino, organize
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO, Manifiesto, destino_organizado

from .test_organize_stats_fases import _de_tipo, _emisor


def _organizado(destino):
    m = Manifiesto(destino / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db")
    (destino / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True, exist_ok=True)
    m.crear_esquema()
    m.cerrar()


def _estado(destino):
    return aw.Api.folder_is_empty(object(), str(destino))


def test_vacio_ok(tmp_path):
    d = tmp_path / "vacio"
    d.mkdir()
    r = _estado(d)
    assert r["empty"] and not r["organizado"], (r, os.listdir(d))


def test_con_marcador_valido_ok(tmp_path):
    _organizado(tmp_path)
    (tmp_path / "RGB").mkdir()
    assert destino_organizado(tmp_path)
    r = _estado(tmp_path)
    assert not r["empty"] and r["organizado"]


def test_no_vacio_sin_marcador_rechazado(tmp_path):
    (tmp_path / "PB1").mkdir()
    r = _estado(tmp_path)
    assert not r["empty"] and not r["organizado"]


def test_marcador_corrupto_rechazado(tmp_path):
    (tmp_path / NOMBRE_CARPETA_MANIFIESTO).mkdir()
    (tmp_path / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db").write_bytes(b"esto no es sqlite" * 50)
    (tmp_path / "RGB").mkdir()
    assert not destino_organizado(tmp_path)
    assert not _estado(tmp_path)["organizado"]


def test_guard_de_arranque_rechaza_marcador_corrupto(monkeypatch, tmp_path):
    (tmp_path / NOMBRE_CARPETA_MANIFIESTO).mkdir()
    (tmp_path / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db").write_bytes(b"basura" * 50)
    (tmp_path / "RGB").mkdir()
    eventos, emit = _emisor()
    organize.run_task("split_images", {"destino": str(tmp_path), "estadillo": "/x.csv"}, emit)
    assert any("no está vacía" in str(e) for e in _de_tipo(eventos, "error"))


def test_manifiesto_vacio_con_solo_organizado_aceptado(tmp_path):
    d = tmp_path / "salida"
    (d / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True)
    (d / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db").write_bytes(b"")
    assert destino_organizado(d)
    assert _estado(d)["empty"], "`.organizado` no cuenta como residuo"


def test_db_con_tablas_ajenas_rechazada(tmp_path):
    (tmp_path / NOMBRE_CARPETA_MANIFIESTO).mkdir()
    con = sqlite3.connect(tmp_path / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db")
    con.execute("CREATE TABLE otra_cosa (x)")
    con.commit()
    con.close()
    assert not destino_organizado(tmp_path)


def test_db_bloqueada_por_escritor_activo_es_organizado(tmp_path):
    _organizado(tmp_path)
    db = tmp_path / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db"
    con = sqlite3.connect(db, isolation_level=None)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("BEGIN EXCLUSIVE")
    try:
        assert destino_organizado(tmp_path)
    finally:
        con.execute("ROLLBACK")
        con.close()


# --- lock de destino (lock del SO, dos procesos reales) -----------------------

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
_HIJO = (
    "import sys; sys.path.insert(0, sys.argv[1])\n"
    "from atom_core import lock_destino\n"
    "l = lock_destino.adquirir(sys.argv[2])\n"
    "print('OK', flush=True)\n"
    "sys.stdin.readline()\n")


def _lanzar_hijo(destino):
    p = subprocess.Popen([sys.executable, "-c", _HIJO, _SRC, str(destino)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "OK"
    return p


def test_lock_dos_procesos_segundo_rechaza_y_tras_matar_el_primero_adquiere(tmp_path):
    d = tmp_path / "salida"
    d.mkdir()
    hijo = _lanzar_hijo(d)
    try:
        with pytest.raises(lock_destino.DestinoOcupado) as e:
            lock_destino.adquirir(str(d))
        assert f"pid {hijo.pid}" in str(e.value) and "run.lock" in str(e.value)
        # run completo rechazado por el lock, sin tocar el lock del otro
        eventos, emit = _emisor()
        organize.run_task("split_images", {"destino": str(d), "estadillo": "/x.csv"}, emit)
        assert any("Otro organizado" in str(x) for x in _de_tipo(eventos, "error")), eventos
        hijo.kill()  # muerte abrupta: el SO libera el lock
        hijo.wait()
        lock = lock_destino.adquirir(str(d))
        lock_destino.liberar(lock)
    finally:
        hijo.kill()
        hijo.wait()


def test_lock_liberar_permite_readquirir(tmp_path):
    l1 = lock_destino.adquirir(str(tmp_path))
    with pytest.raises(lock_destino.DestinoOcupado):
        lock_destino.adquirir(str(tmp_path))
    lock_destino.liberar(l1)
    lock_destino.liberar(lock_destino.adquirir(str(tmp_path)))


def test_lock_en_ruta_imposible_da_mensaje_claro(tmp_path):
    f = tmp_path / "fichero"
    f.write_text("x")
    with pytest.raises(lock_destino.DestinoOcupado) as e:
        lock_destino.adquirir(str(f))
    assert "No se puede crear el bloqueo" in str(e.value)
