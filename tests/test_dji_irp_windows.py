"""
Tests de `dji_irp_windows.py` con mocks: en Linux (banco de tests) no hay
`libdirp.dll` real, así que `ctypes.CDLL` y el `_dll` en sí se sustituyen por
dobles. Cubren: gating de `enabled()`, fallo de carga (`_roto` + CWD
restaurado), carga perezosa cacheada (una sola vez, también entre hilos) y
`measure()` (rc != 0 lanza, rc == 0 escribe el `.raw`).
"""
import ctypes
import os
import sys
import threading
import types

import pytest

import dji_irp_windows


@pytest.fixture(autouse=True)
def _reset_estado_modulo(monkeypatch):
    """El módulo cachea `_dll`/`_roto` a nivel de proceso: cada test parte de
    un estado limpio y lo deja limpio para el siguiente."""
    monkeypatch.setattr(dji_irp_windows, "_dll", None)
    monkeypatch.setattr(dji_irp_windows, "_roto", False)
    yield


# --------------------------------------------------------------------------
# enabled()
# --------------------------------------------------------------------------

def test_enabled_false_fuera_de_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("ATOM_DJI_DLL", raising=False)
    assert dji_irp_windows.enabled() is False


def test_enabled_true_en_windows_por_defecto(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("ATOM_DJI_DLL", raising=False)
    assert dji_irp_windows.enabled() is True


def test_enabled_false_con_env_atom_dji_dll_0(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ATOM_DJI_DLL", "0")
    assert dji_irp_windows.enabled() is False


def test_enabled_false_si_roto(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("ATOM_DJI_DLL", raising=False)
    monkeypatch.setattr(dji_irp_windows, "_roto", True)
    assert dji_irp_windows.enabled() is False


# --------------------------------------------------------------------------
# _load(): fallo de carga
# --------------------------------------------------------------------------

def test_load_fallo_marca_roto_y_restaura_cwd(tmp_path, monkeypatch):
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir()
    prev_cwd = os.getcwd()

    def _cdll_rota(*a, **k):
        raise OSError("libdirp.dll no encontrada o dependencia rota")

    monkeypatch.setattr(ctypes, "CDLL", _cdll_rota)

    with pytest.raises(OSError):
        dji_irp_windows._load(str(sdk_dir))

    assert dji_irp_windows._roto is True
    assert os.getcwd() == prev_cwd, "el CWD debe restaurarse aunque la carga falle"

    # `enabled()` ya no vuelve a intentarlo el resto del proceso.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("ATOM_DJI_DLL", raising=False)
    assert dji_irp_windows.enabled() is False


# --------------------------------------------------------------------------
# _load(): carga OK, perezosa y cacheada (también entre hilos)
# --------------------------------------------------------------------------

def _fake_dll_ok():
    """Doble de la DLL: acepta cualquier asignación de argtypes/restype."""
    return types.SimpleNamespace(
        dirp_create_from_rjpeg=lambda *a, **k: 0,
        dirp_destroy=lambda *a, **k: 0,
        dirp_get_rjpeg_resolution=lambda *a, **k: 0,
        dirp_get_measurement_params=lambda *a, **k: 0,
        dirp_set_measurement_params=lambda *a, **k: 0,
        dirp_measure_ex=lambda *a, **k: 0,
    )


def test_load_ok_restaura_cwd_y_carga_una_sola_vez(tmp_path, monkeypatch):
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir()
    prev_cwd = os.getcwd()

    llamadas = []

    def _cdll_ok(*a, **k):
        llamadas.append((a, k))
        return _fake_dll_ok()

    monkeypatch.setattr(ctypes, "CDLL", _cdll_ok)

    dll1 = dji_irp_windows._load(str(sdk_dir))
    assert os.getcwd() == prev_cwd, "el CWD debe restaurarse tras cargar bien"
    assert len(llamadas) == 1

    dll2 = dji_irp_windows._load(str(sdk_dir))
    assert dll2 is dll1
    assert len(llamadas) == 1, "la DLL no debe recargarse en llamadas posteriores"


def test_load_ok_una_sola_carga_desde_varios_hilos(tmp_path, monkeypatch):
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir()

    llamadas = []
    lock = threading.Lock()

    def _cdll_ok(*a, **k):
        with lock:
            llamadas.append((a, k))
        return _fake_dll_ok()

    monkeypatch.setattr(ctypes, "CDLL", _cdll_ok)

    errores = []

    def _worker():
        try:
            dji_irp_windows._load(str(sdk_dir))
        except Exception as e:  # pragma: no cover - solo para diagnóstico si falla
            errores.append(e)

    hilos = [threading.Thread(target=_worker) for _ in range(16)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores
    assert len(llamadas) == 1, "16 hilos concurrentes deben resultar en UNA sola carga"


# --------------------------------------------------------------------------
# measure()
# --------------------------------------------------------------------------

def _fake_dll_measure(rc_create=0, valores=None):
    """Doble de la DLL para measure(): resolución fija 2x2, rellena el buffer
    de salida con `valores` (o 1.5 en todas las posiciones)."""
    def _create(buf, n, handle_ref):
        handle_ref._obj.value = 111
        return rc_create

    def _resolution(handle, res_ref):
        res_ref._obj.width = 2
        res_ref._obj.height = 2
        return 0

    def _get_params(handle, params_ref):
        return 0

    def _set_params(handle, params_ref):
        return 0

    def _measure_ex(handle, data, nbytes):
        n = len(data)
        for i in range(n):
            data[i] = valores if valores is not None else 1.5
        return 0

    def _destroy(handle):
        return 0

    return types.SimpleNamespace(
        dirp_create_from_rjpeg=_create,
        dirp_destroy=_destroy,
        dirp_get_rjpeg_resolution=_resolution,
        dirp_get_measurement_params=_get_params,
        dirp_set_measurement_params=_set_params,
        dirp_measure_ex=_measure_ex,
    )


def test_measure_rc_no_cero_lanza_excepcion(tmp_path, monkeypatch):
    image_path = tmp_path / "img.jpg"
    image_path.write_bytes(b"contenido-de-prueba")
    raw_out = str(tmp_path / "img.jpg.raw")

    monkeypatch.setattr(dji_irp_windows, "_dll", _fake_dll_measure(rc_create=-16))

    with pytest.raises(RuntimeError, match="create rc=-16"):
        dji_irp_windows.measure(str(image_path), raw_out, 50.0, 0.9, str(tmp_path))

    assert not os.path.exists(raw_out)


def test_measure_rc_cero_escribe_raw(tmp_path, monkeypatch):
    image_path = tmp_path / "img.jpg"
    image_path.write_bytes(b"contenido-de-prueba")
    raw_out = str(tmp_path / "img.jpg.raw")

    monkeypatch.setattr(dji_irp_windows, "_dll", _fake_dll_measure(rc_create=0, valores=1.5))

    dji_irp_windows.measure(str(image_path), raw_out, 50.0, 0.9, str(tmp_path))

    assert os.path.exists(raw_out)
    data = (ctypes.c_float * 4)(1.5, 1.5, 1.5, 1.5)
    assert open(raw_out, "rb").read() == bytes(data)
