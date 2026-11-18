import sys
import types

import pytest

from atom_core import rust_bridge
from atom_core.rust_bridge import RustNoDisponible


def _falso(abi=None, simbolos=True):
    m = types.ModuleType("atom_rs")
    m.version_abi = lambda: rust_bridge.ABI_ESPERADA if abi is None else abi
    m.version = lambda: "9.9.9"
    if simbolos:
        for nombres in rust_bridge.SIMBOLOS_POR_FASE.values():
            for n in nombres:
                setattr(m, n, lambda *a, **k: None)
    return m


def test_sin_modulo_lanza_con_interprete(monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    with pytest.raises(RustNoDisponible, match="atom_rs") as ei:
        rust_bridge.exigir("rgb")
    assert sys.executable in str(ei.value)
    with pytest.raises(RustNoDisponible, match="atom_rs"):
        rust_bridge.modulo()


def test_exigir_nunca_devuelve_none(monkeypatch):
    falso = _falso()
    monkeypatch.setitem(sys.modules, "atom_rs", falso)
    for f in rust_bridge.FASES:
        assert rust_bridge.exigir(f) is falso


def test_no_hay_flags_ni_modos():
    for n in ("fase_activa", "fases_activas", "rgb_modo"):
        assert not hasattr(rust_bridge, n)


def test_fase_desconocida(monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", _falso())
    with pytest.raises(ValueError):
        rust_bridge.exigir("xyz")


def test_abi_distinta_lanza(monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(abi=rust_bridge.ABI_ESPERADA + 1))
    with pytest.raises(RustNoDisponible, match="ABI"):
        rust_bridge.exigir("indice")


def test_abi_vieja_lanza(monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(abi=rust_bridge.ABI_ESPERADA - 1))
    with pytest.raises(RustNoDisponible, match="ABI"):
        rust_bridge.exigir("subida")


def test_abi_esperada_es_ocho():
    # Debe coincidir con `ABI` de rust/atom_rs/src/lib.rs (8 = solo Rust).
    assert rust_bridge.ABI_ESPERADA == 8


def test_fases_registradas_con_simbolos():
    assert set(rust_bridge.FASES) == {"termica", "indice", "rgb", "subida", "io"}
    assert rust_bridge.SIMBOLOS_POR_FASE["subida"] == ("subir_lote",)
    assert rust_bridge.SIMBOLOS_POR_FASE["io"] == ("leer_completo", "jpeg_integro_por_cola")
    assert rust_bridge.SIMBOLOS_POR_FASE["rgb"] == ("rgb_procesar",)


@pytest.mark.parametrize("fase,simbolo", [
    ("termica", "termica_procesar"), ("indice", "parsear_cabecera"),
    ("rgb", "rgb_procesar"), ("subida", "subir_lote"),
    ("io", "leer_completo"), ("io", "jpeg_integro_por_cola"),
])
def test_wheel_sin_simbolo_lanza_con_nombre(monkeypatch, fase, simbolo):
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    with pytest.raises(RustNoDisponible, match=simbolo):
        rust_bridge.exigir(fase)
    with pytest.raises(RustNoDisponible, match=simbolo):
        rust_bridge.modulo()


def test_exigir_solo_valida_simbolos_de_su_fase(monkeypatch):
    falso = _falso(simbolos=False)
    falso.rgb_procesar = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "atom_rs", falso)
    assert rust_bridge.exigir("rgb") is falso
    with pytest.raises(RustNoDisponible):
        rust_bridge.modulo()


def _correr_task(organize, task="task_que_no_existe"):
    eventos = []
    organize.run_task(task, {}, lambda kind, msg=None: eventos.append((kind, msg)))
    return eventos


def test_run_task_loguea_rust_al_arrancar(monkeypatch):
    from atom_core import organize

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

    monkeypatch.setitem(sys.modules, "atom_rs", None)
    eventos = _correr_task(organize)
    errores = [m for k, m in eventos if k == "error"]
    assert len(errores) == 1 and errores[0].startswith("[rust] ")
    assert not any("Task desconocido" in str(m) for _, m in eventos)


def test_run_task_aborta_al_arrancar_si_wheel_sin_termica_procesar(monkeypatch):
    """Wheel vieja (ABI correcta pero sin `termica_procesar`): error visible al
    ARRANCAR, no a mitad del vuelo ni retroceso a Python."""
    from atom_core import organize

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
