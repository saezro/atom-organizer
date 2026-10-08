"""Limpieza de `*.parcial.*` huérfanos que deja un kill duro en el destino."""
import os

from atom_core import apply
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO

EXC = (NOMBRE_CARPETA_MANIFIESTO,)


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_nombre_coincide_con_ruta_parcial():
    n = os.path.basename(apply.ruta_parcial("/a/DJI_0001.JPG"))
    assert n.startswith("DJI_0001.parcial.") and n.endswith(".JPG")
    assert apply.es_nombre_parcial(n)


def test_borra_huerfanos_en_subcarpetas(tmp_path):
    a = _touch(tmp_path / "A" / "B" / "DJI_1.parcial.JPG")
    b = _touch(tmp_path / "C.parcial.tif")
    assert apply.limpiar_parciales_huerfanos(str(tmp_path), EXC) == 2
    assert not a.exists() and not b.exists()


def test_no_toca_normales_ni_nombres_parecidos(tmp_path):
    ok = [_touch(tmp_path / n) for n in
          ("DJI_1.JPG", "parcial.JPG", "x.parcial", "parcialmente.JPG", "a.parcial.JPG.bak")]
    (tmp_path / "d.parcial.JPG").mkdir()
    assert apply.limpiar_parciales_huerfanos(str(tmp_path), EXC) == 0
    assert all(p.exists() for p in ok) and (tmp_path / "d.parcial.JPG").is_dir()


def test_no_toca_organizado_ni_symlinks(tmp_path):
    org = _touch(tmp_path / NOMBRE_CARPETA_MANIFIESTO / "z.parcial.JPG")
    fuera = _touch(tmp_path.parent / (tmp_path.name + "_fuera") / "f.parcial.JPG")
    os.symlink(fuera, tmp_path / "l.parcial.JPG")
    os.symlink(fuera.parent, tmp_path / "dirlink")
    assert apply.limpiar_parciales_huerfanos(str(tmp_path), EXC) == 0
    assert org.exists() and fuera.exists()


def test_destino_inexistente_no_hace_nada(tmp_path):
    assert apply.limpiar_parciales_huerfanos(str(tmp_path / "nuevo"), EXC) == 0


def test_fallo_al_borrar_no_aborta(tmp_path, monkeypatch):
    a = _touch(tmp_path / "a.parcial.JPG")
    b = _touch(tmp_path / "b.parcial.JPG")
    real = os.remove

    def falla(p):
        if p == str(a):
            raise PermissionError("no")
        real(p)
    monkeypatch.setattr(os, "remove", falla)
    assert apply.limpiar_parciales_huerfanos(str(tmp_path), EXC) == 1
    assert a.exists() and not b.exists()
