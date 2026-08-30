"""Capa de almacenamiento intercambiable (backend LOCAL).

Estos tests fijan el contrato de `Almacen` sobre el backend `AlmacenLocal`, que
es un envoltorio fino sobre `pathlib`/`shutil` con la MISMA semántica que ya
tiene el pipeline hoy contra el mount de gcsfuse (crea directorios padre al
publicar/mover, `mover` sobrescribe con `os.replace`). El backend GCS por API
llegará en una fase posterior y deberá cumplir este mismo contrato.
"""
from pathlib import Path

import pytest

from atom_core.almacen import AlmacenLocal


def _crear(base: Path, relativo: str, contenido: str = "x") -> Path:
    ruta = base / relativo
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido)
    return ruta


def _raiz(tmp_path: Path) -> Path:
    """Subcarpeta aislada del `Logs-subidas/` que el fixture autouse
    `_subidas_log_aislado` escribe en `tmp_path` (ver conftest.py)."""
    raiz = tmp_path / "almacen"
    raiz.mkdir()
    return raiz


def test_listar_recursivo(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "a.txt")
    _crear(raiz, "sub/b.txt")
    _crear(raiz, "sub/hondo/c.txt")
    almacen = AlmacenLocal(raiz)
    assert sorted(almacen.listar("")) == ["a.txt", "sub/b.txt", "sub/hondo/c.txt"]


def test_listar_con_prefijo(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "a.txt")
    _crear(raiz, "sub/b.txt")
    _crear(raiz, "sub/hondo/c.txt")
    almacen = AlmacenLocal(raiz)
    assert sorted(almacen.listar("sub")) == ["sub/b.txt", "sub/hondo/c.txt"]


def test_listar_rutas_relativas_con_slash(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "sub/hondo/c.txt")
    almacen = AlmacenLocal(raiz)
    rutas = almacen.listar("")
    assert rutas == ["sub/hondo/c.txt"]
    assert "\\" not in rutas[0]


def test_existe(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "a.txt")
    almacen = AlmacenLocal(raiz)
    assert almacen.existe("a.txt") is True
    assert almacen.existe("no_existe.txt") is False


def test_abrir_local_devuelve_la_ruta_real_sin_copiar(tmp_path):
    raiz = _raiz(tmp_path)
    original = _crear(raiz, "a.txt", "contenido")
    almacen = AlmacenLocal(raiz)
    with almacen.abrir_local("a.txt") as ruta:
        assert isinstance(ruta, Path)
        assert ruta == original
        assert ruta.read_text() == "contenido"


def test_publicar_crea_directorios_intermedios(tmp_path):
    origen = tmp_path / "origen.txt"
    origen.write_text("hola")
    raiz = _raiz(tmp_path) / "raiz"
    almacen = AlmacenLocal(raiz)
    almacen.publicar(origen, "carpeta/sub/destino.txt")
    destino = raiz / "carpeta" / "sub" / "destino.txt"
    assert destino.read_text() == "hola"


def test_mover_a_carpeta_inexistente(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "origen.txt", "hola")
    almacen = AlmacenLocal(raiz)
    almacen.mover("origen.txt", "nueva/carpeta/destino.txt")
    assert not (raiz / "origen.txt").exists()
    assert (raiz / "nueva" / "carpeta" / "destino.txt").read_text() == "hola"


def test_mover_sobrescribe_destino_existente(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "origen.txt", "nuevo")
    _crear(raiz, "destino.txt", "viejo")
    almacen = AlmacenLocal(raiz)
    almacen.mover("origen.txt", "destino.txt")
    assert not (raiz / "origen.txt").exists()
    assert (raiz / "destino.txt").read_text() == "nuevo"


def test_borrar(tmp_path):
    raiz = _raiz(tmp_path)
    _crear(raiz, "a.txt")
    almacen = AlmacenLocal(raiz)
    almacen.borrar("a.txt")
    assert not (raiz / "a.txt").exists()
