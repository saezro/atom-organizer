"""`_CollectingProgress.emit` no debe dejar pasar basura al log crudo del modal.

El filtro original era `if payload is not None`, que dejaba pasar el entero `0`
(progreso numérico de Qt, sin uso aquí) y cualquier otro no-string, inundando el
log con líneas "0". Ver `pipeline.py::_CollectingProgress.emit`.
"""
from pipeline import _CollectingProgress


def test_descarta_cero_entero():
    p = _CollectingProgress()
    p.emit(0)
    assert p.messages == []


def test_descarta_none():
    p = _CollectingProgress()
    p.emit(None)
    p.emit()
    assert p.messages == []


def test_descarta_string_vacio_o_solo_espacios():
    p = _CollectingProgress()
    p.emit("")
    p.emit("   ")
    p.emit("\t\n")
    assert p.messages == []


def test_acumula_string_normal():
    p = _CollectingProgress()
    p.emit("procesando imagen 3 de 10")
    assert p.messages == ["procesando imagen 3 de 10"]


def test_acumula_puntos_regresion_contador_imagenes():
    # organize.py::_on_log cuenta estos "." para el "N de M analizadas" del modal.
    p = _CollectingProgress()
    p.emit("...")
    p.emit(".")
    assert p.messages == ["...", "."]
