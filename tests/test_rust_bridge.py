import sys
import types

import pytest

from atom_core import rust_bridge
from atom_core.rust_bridge import RustNoDisponible


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    monkeypatch.delenv("ATOM_RUST", raising=False)
    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)


def _falso(abi=None, simbolos=True):
    m = types.ModuleType("atom_rs")
    m.version_abi = lambda: rust_bridge.ABI_ESPERADA if abi is None else abi
    m.version = lambda: "9.9.9"
    if simbolos:
        for nombres in rust_bridge.SIMBOLOS_POR_FASE.values():
            for n in nombres:
                setattr(m, n, lambda *a, **k: None)
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


def test_abi_esperada_es_tres():
    # Debe coincidir con `ABI` de rust/atom_rs/src/lib.rs (3 = sonda libjpeg, P).
    assert rust_bridge.ABI_ESPERADA == 3


def test_modulo_sin_simbolo_de_fase_activa_lanza(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    with pytest.raises(RustNoDisponible, match="termica_procesar"):
        rust_bridge.modulo()
    with pytest.raises(RustNoDisponible, match="termica_procesar"):
        rust_bridge.exigir("termica")


def test_modulo_no_exige_simbolos_de_fases_inactivas(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    falso = _falso(simbolos=False)
    monkeypatch.setitem(sys.modules, "atom_rs", falso)
    assert rust_bridge.modulo() is falso
    assert rust_bridge.exigir("indice") is falso


def _correr_task(organize, task="task_que_no_existe"):
    eventos = []
    organize.run_task(task, {}, lambda kind, msg=None: eventos.append((kind, msg)))
    return eventos


def test_run_task_loguea_rust_al_arrancar(monkeypatch):
    from atom_core import organize

    monkeypatch.setenv("ATOM_RUST", "1")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso())
    eventos = _correr_task(organize)
    lineas = [m for _, m in eventos if isinstance(m, str) and m.startswith("[rust] atom_rs ")]
    assert len(lineas) == 1
    for f in rust_bridge.FASES:
        assert f in lineas[0]
    # Tras la comprobación sigue el run normal (aquí: task desconocido).
    assert ("error", "Task desconocido: task_que_no_existe") in eventos


def test_run_task_aborta_al_arrancar_si_rust_no_disponible(monkeypatch):
    from atom_core import organize

    monkeypatch.setenv("ATOM_RUST", "1")
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    eventos = _correr_task(organize)
    errores = [m for k, m in eventos if k == "error"]
    assert len(errores) == 1 and errores[0].startswith("[rust] ")
    assert not any("Task desconocido" in str(m) for _, m in eventos)


def test_run_task_aborta_al_arrancar_si_wheel_sin_termica_procesar(monkeypatch):
    """Flag termica + wheel vieja (ABI correcta pero sin `termica_procesar`): error
    visible al ARRANCAR, no a mitad del vuelo ni retroceso a Python."""
    from atom_core import organize

    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    eventos = _correr_task(organize, "split_images")
    errores = [m for k, m in eventos if k == "error"]
    assert len(errores) == 1
    assert errores[0].startswith("[rust] ") and "termica_procesar" in errores[0]


def test_run_organize_delega_en_run_task(monkeypatch):
    from atom_core import organize

    llamadas = []
    monkeypatch.setattr(organize, "run_task", lambda *a, **k: llamadas.append(a))
    organize.run_organize({"x": 1}, lambda *a: None)
    assert len(llamadas) == 1 and llamadas[0][0] == "split_images"
