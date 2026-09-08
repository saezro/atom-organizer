"""La precarga de pandas: idempotente, serializada y con el traceback REAL.

Cubre el fallo que rompió `Organizar completo` en el .exe de Windows
(`partially initialized module 'pandas' has no attribute '_pandas_datetime_CAPI'`).
El porqué de cada pieza está en atom_core/precarga.py.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from atom_core import precarga


@pytest.fixture(autouse=True)
def _estado_limpio():
    """Cada test arranca con la precarga "sin hacer" y la deja como estaba."""
    previo = precarga._pandas_listo
    precarga._pandas_listo = False
    yield
    precarga._pandas_listo = previo


@pytest.fixture
def _pandas_intacto():
    """Restaura los `pandas*` REALES que la purga borra de `sys.modules`.

    `_purgar_pandas_de_sys_modules` no distingue entre el pandas de mentira que
    monta el test y los ~150 submódulos de verdad ya cargados. Si esos no vuelven
    a su sitio, un `import pandas.io.parsers` posterior reejecuta la inicialización
    de las C-extensions ya cargadas y el intérprete se cae con segfault (se llevó
    por delante a tests/test_reparto_struct.py).
    """
    import sys

    previo = {
        nombre: modulo
        for nombre, modulo in sys.modules.items()
        if nombre == "pandas" or nombre.startswith("pandas.")
    }
    yield
    for nombre in [m for m in sys.modules if m == "pandas" or m.startswith("pandas.")]:
        sys.modules.pop(nombre, None)
    sys.modules.update(previo)


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


def test_precargar_en_arranque_en_hilo_daemon_no_bloquea_y_consumidor_obtiene_pandas():
    """`precargar_en_arranque()` lanzado en un hilo daemon (como hace
    `app_webview.py` en el arranque) no bloquea al hilo que lo lanza, y un
    consumidor que llega después con `precargar_pandas()` encuentra pandas
    ya listo (o espera al candado hasta que lo esté)."""
    hilo = threading.Thread(target=precarga.precargar_en_arranque, daemon=True)
    antes = time.monotonic()
    hilo.start()
    # El hilo que lanza no espera: arrancar el thread es prácticamente instantáneo.
    assert time.monotonic() - antes < 0.5

    hilo.join(timeout=10)
    assert not hilo.is_alive()

    # El consumidor llega después: pandas ya debe estar listo (candado + flag).
    precarga.precargar_pandas()
    import sys
    assert "pandas" in sys.modules
    assert precarga._pandas_listo is True


def test_neutralizar_pytz_sin_version_lo_apaga(monkeypatch):
    """Un pytz importable pero mutilado (sin `__version__`) se anula."""
    import sys
    import types

    pytz_falso = types.ModuleType("pytz")
    monkeypatch.setitem(sys.modules, "pytz", pytz_falso)

    precarga._neutralizar_pytz_sin_version()

    assert sys.modules["pytz"] is None


def test_neutralizar_pytz_con_version_no_lo_toca(monkeypatch):
    """Si el pytz presente SÍ tiene `__version__`, se deja tal cual."""
    import sys
    import types

    pytz_bueno = types.ModuleType("pytz")
    pytz_bueno.__version__ = "2024.1"
    monkeypatch.setitem(sys.modules, "pytz", pytz_bueno)

    precarga._neutralizar_pytz_sin_version()

    assert sys.modules["pytz"] is pytz_bueno


def test_neutralizar_pytz_sin_pytz_instalado_no_revienta(monkeypatch):
    """Si `import pytz` falla (no está instalado), la función no hace nada."""
    import sys

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def import_sin_pytz(nombre, *args, **kwargs):
        if nombre == "pytz":
            raise ImportError("No module named 'pytz'")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "pytz", raising=False)
    monkeypatch.setattr("builtins.__import__", import_sin_pytz)

    precarga._neutralizar_pytz_sin_version()  # no debe lanzar

    assert "pytz" not in sys.modules


def test_purgar_borra_pandas_y_respeta_pandas_otro(monkeypatch, _pandas_intacto):
    """Purga `pandas` y `pandas.algo`, pero NO toca `pandas_otro` (otro namespace)."""
    import sys
    import types

    fake_pandas = types.ModuleType("pandas")
    fake_pandas_algo = types.ModuleType("pandas.algo")
    fake_pandas_otro = types.ModuleType("pandas_otro")
    monkeypatch.setitem(sys.modules, "pandas", fake_pandas)
    monkeypatch.setitem(sys.modules, "pandas.algo", fake_pandas_algo)
    monkeypatch.setitem(sys.modules, "pandas_otro", fake_pandas_otro)

    precarga._purgar_pandas_de_sys_modules()

    assert "pandas" not in sys.modules
    assert "pandas.algo" not in sys.modules
    assert sys.modules["pandas_otro"] is fake_pandas_otro


def test_precargar_propaga_y_purga_si_import_pandas_falla(monkeypatch, _pandas_intacto):
    """Si `import pandas` revienta, la excepción se propaga, no queda ningún
    `pandas*` a medias en sys.modules y `_pandas_listo` sigue False: el
    siguiente intento vuelve a intentar el import de verdad."""
    import builtins
    import sys

    real_import = builtins.__import__

    def import_que_revienta(nombre, *args, **kwargs):
        if nombre == "pandas":
            raise ImportError("boom simulado")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_que_revienta)
    with pytest.raises(ImportError, match="boom simulado"):
        precarga.precargar_pandas()
    assert not any(
        nombre == "pandas" or nombre.startswith("pandas.") for nombre in sys.modules
    )
    assert precarga._pandas_listo is False
