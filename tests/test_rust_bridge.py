import sys
import types

import pytest

from atom_core import rust_bridge
from atom_core.rust_bridge import RustNoDisponible


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    monkeypatch.delenv("ATOM_RUST", raising=False)
    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)


def _falso(abi=None):
    m = types.ModuleType("atom_rs")
    m.version_abi = lambda: rust_bridge.ABI_ESPERADA if abi is None else abi
    m.version = lambda: "9.9.9"
    return m


def test_flag_off_no_importa_modulo(monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    assert rust_bridge.fase_activa("rgb") is False
    assert rust_bridge.exigir("rgb") is None


def test_flag_on_sin_modulo_lanza(monkeypatch):
    monkeypatch.setenv("ATOM_RUST", "1")
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    with pytest.raises(RustNoDisponible, match="atom_rs") as ei:
        rust_bridge.exigir("rgb")
    assert sys.executable in str(ei.value)


def test_flag_parcial(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    assert rust_bridge.fase_activa("termica")
    assert not rust_bridge.fase_activa("indice")


def test_fase_desconocida():
    with pytest.raises(ValueError):
        rust_bridge.fase_activa("xyz")


def test_abi_distinta_lanza(monkeypatch):
    monkeypatch.setenv("ATOM_RUST", "1")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(abi=rust_bridge.ABI_ESPERADA + 1))
    with pytest.raises(RustNoDisponible, match="ABI"):
        rust_bridge.exigir("indice")


def test_run_organize_loguea_rust(monkeypatch):
    from atom_core import organize

    monkeypatch.setenv("ATOM_RUST", "1")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso())
    llamadas = []
    monkeypatch.setattr(organize, "run_task", lambda *a, **k: llamadas.append(a))
    eventos = []
    organize.run_organize({}, lambda kind, msg: eventos.append((kind, msg)))
    lineas = [m for _, m in eventos if isinstance(m, str) and m.startswith("[rust] atom_rs ")]
    assert len(lineas) == 1
    for f in rust_bridge.FASES:
        assert f in lineas[0]
    assert len(llamadas) == 1


def test_run_organize_aborta_si_rust_no_disponible(monkeypatch):
    from atom_core import organize

    monkeypatch.setenv("ATOM_RUST", "1")
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    llamadas = []
    monkeypatch.setattr(organize, "run_task", lambda *a, **k: llamadas.append(a))
    eventos = []
    organize.run_organize({}, lambda kind, msg: eventos.append((kind, msg)))
    assert eventos and eventos[0][0] == "error"
    assert llamadas == []
