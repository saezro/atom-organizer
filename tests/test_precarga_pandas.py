"""La precarga de pandas: idempotente, serializada y con el traceback REAL.

Cubre el fallo que rompió `Organizar completo` en el .exe de Windows
(`partially initialized module 'pandas' has no attribute '_pandas_datetime_CAPI'`).
El porqué de cada pieza está en atom_core/precarga.py.
"""
from __future__ import annotations

import logging
import threading

import pytest

from atom_core import precarga


@pytest.fixture(autouse=True)
def _estado_limpio():
    """Cada test arranca con la precarga "sin hacer" y la deja como estaba."""
    previo = precarga._pandas_listo
    precarga._pandas_listo = False
    yield
    precarga._pandas_listo = previo


def test_precargar_deja_pandas_importado():
    precarga.precargar_pandas()
    import sys
    assert "pandas" in sys.modules
    assert precarga._pandas_listo is True


def test_precargar_es_idempotente(monkeypatch):
    """La segunda llamada no vuelve a importar: sale por el flag."""
    precarga.precargar_pandas()
    llamadas = []
    real = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def espia(nombre, *args, **kwargs):
        if nombre == "pandas":
            llamadas.append(nombre)
        return real(nombre, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", espia)
    precarga.precargar_pandas()
    assert llamadas == []


def test_precargar_serializa_entre_hilos():
    """Con N hilos entrando a la vez, el import ocurre UNA sola vez.

    Es exactamente el escenario que reventaba: el worker del pipeline y el de
    detección de estadillos disparando el primer import de pandas a la vez.
    """
    dentro = []
    barrera = threading.Barrier(8)

    original = precarga.precargar_pandas

    def instrumentada():
        # Se reimplementa el cuerpo para contar cuántas veces se entra a la
        # zona crítica con el flag aún a False (o sea, cuántos imports reales).
        with precarga._candado:
            if precarga._pandas_listo:
                return
            dentro.append(threading.current_thread().name)
            import pandas  # noqa: F401
            precarga._pandas_listo = True

    def worker():
        barrera.wait()
        instrumentada()

    hilos = [threading.Thread(target=worker) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(dentro) == 1
    assert original is precarga.precargar_pandas  # no se ha parcheado el módulo


def test_precargar_en_arranque_registra_el_traceback_y_no_propaga(monkeypatch, caplog):
    """Si pandas está roto en el bundle, la app abre igual pero queda escrito.

    Ese traceback es el error REAL del empaquetado (p. ej. "DLL load failed"),
    el que el `_pandas_datetime_CAPI` enmascara.
    """
    def revienta():
        raise ImportError("DLL load failed while importing np_datetime")

    monkeypatch.setattr(precarga, "precargar_pandas", revienta)
    with caplog.at_level(logging.ERROR, logger="atom_core.precarga"):
        precarga.precargar_en_arranque()  # no debe propagar
    assert "DLL load failed" in caplog.text
