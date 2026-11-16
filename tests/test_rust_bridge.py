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


def test_abi_esperada_es_seis():
    # Debe coincidir con `ABI` de rust/atom_rs/src/lib.rs (6 = fase rgb, T3).
    assert rust_bridge.ABI_ESPERADA == 6


def test_fase_subida_registrada():
    assert "subida" in rust_bridge.FASES
    assert rust_bridge.SIMBOLOS_POR_FASE["subida"] == ("subir_lote",)


def test_flag_fases_subida_activa_solo_subida(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "subida")
    assert rust_bridge.fases_activas() == ("subida",)
    assert rust_bridge.fase_activa("subida")
    for otra in ("termica", "indice", "rgb"):
        assert not rust_bridge.fase_activa(otra)


def test_atom_rust_activa_subida(monkeypatch):
    monkeypatch.setenv("ATOM_RUST", "1")
    assert rust_bridge.fase_activa("subida")


def test_subida_sin_simbolo_lanza_con_nombre(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "subida")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    with pytest.raises(RustNoDisponible, match="subir_lote"):
        rust_bridge.exigir("subida")
    with pytest.raises(RustNoDisponible, match="subir_lote"):
        rust_bridge.modulo()


def test_subida_con_simbolo_ok(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "subida")
    falso = _falso()
    monkeypatch.setitem(sys.modules, "atom_rs", falso)
    assert rust_bridge.exigir("subida") is falso


def test_subida_abi_vieja_lanza(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "subida")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(abi=rust_bridge.ABI_ESPERADA - 1))
    with pytest.raises(RustNoDisponible, match="ABI"):
        rust_bridge.exigir("subida")


def test_modulo_sin_simbolo_de_fase_activa_lanza(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    with pytest.raises(RustNoDisponible, match="termica_procesar"):
        rust_bridge.modulo()
    with pytest.raises(RustNoDisponible, match="termica_procesar"):
        rust_bridge.exigir("termica")


def test_modulo_no_exige_simbolos_de_fases_inactivas(monkeypatch):
    # Con solo `rgb` activa, una wheel que solo trae `rgb_procesar` (sin los de
    # termica/indice/subida) sigue valiendo.
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    falso = _falso(simbolos=False)
    falso.rgb_procesar = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "atom_rs", falso)
    assert rust_bridge.modulo() is falso
    assert rust_bridge.exigir("rgb") is falso


def test_rgb_registrada_con_simbolo():
    assert rust_bridge.SIMBOLOS_POR_FASE["rgb"] == ("rgb_procesar",)


def test_rgb_sin_simbolo_lanza_con_nombre(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    with pytest.raises(RustNoDisponible, match="rgb_procesar"):
        rust_bridge.exigir("rgb")


def test_rgb_modo_por_defecto_es_a(monkeypatch):
    monkeypatch.delenv("ATOM_RUST_RGB_MODO", raising=False)
    assert rust_bridge.rgb_modo() == "a"


def test_modo_b_bloqueado_sin_aprobacion(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setenv("ATOM_RUST_RGB_MODO", "b")
    monkeypatch.delenv("ATOM_RUST_RGB_B_APROBADO", raising=False)
    monkeypatch.setitem(sys.modules, "atom_rs", _falso())
    with pytest.raises(RustNoDisponible, match="requiere OK de Rodrigo para entregas a cliente"):
        rust_bridge.rgb_modo()
    # Y el arranque del run (`modulo()`) también aborta, no a mitad del lote.
    with pytest.raises(RustNoDisponible, match="requiere OK de Rodrigo para entregas a cliente"):
        rust_bridge.modulo()


def test_modo_b_aprobado_sigue_sin_implementar(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_RGB_MODO", "b")
    monkeypatch.setenv("ATOM_RUST_RGB_B_APROBADO", "1")
    with pytest.raises(RustNoDisponible, match="aplazado"):
        rust_bridge.rgb_modo()


def test_modo_desconocido(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_RGB_MODO", "z")
    with pytest.raises(ValueError):
        rust_bridge.rgb_modo()


def test_indice_sin_simbolo_lanza_con_nombre(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(simbolos=False))
    with pytest.raises(RustNoDisponible, match="parsear_cabecera"):
        rust_bridge.exigir("indice")


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
