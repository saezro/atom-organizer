"""`--check-rust` del ejecutable: exit 0 si la validación completa pasa, 1 si falla."""
import types

from atom_core import rust_bridge
from atom_core.rust_bridge import RustNoDisponible


def test_check_cli_ok(monkeypatch, capsys):
    falso = types.SimpleNamespace(version_abi=lambda: rust_bridge.ABI_ESPERADA)
    monkeypatch.setattr(rust_bridge, "modulo", lambda fases=None: falso)
    assert rust_bridge.check_cli() == 0
    assert f"atom_rs ABI {rust_bridge.ABI_ESPERADA} OK" in capsys.readouterr().out


def test_check_cli_fallo(monkeypatch, capsys):
    def _falla(fases=None):
        raise RustNoDisponible("ABI incompatible")
    monkeypatch.setattr(rust_bridge, "modulo", _falla)
    assert rust_bridge.check_cli() == 1
    assert "ABI incompatible" in capsys.readouterr().err
