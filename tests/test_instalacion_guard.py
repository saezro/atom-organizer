"""Guard de arranque (Windows): Setup en curso y mezcla exe/_internal."""
from types import SimpleNamespace

from atom_core import instalacion_guard as g


def _fake_ctypes(mutex_existe: bool):
    avisos, cerrados = [], []

    def open_mutex(acceso, herencia, nombre):
        assert nombre == "ATOM-Organizer-Setup"
        return 1234 if mutex_existe else 0

    k32 = SimpleNamespace(OpenMutexW=open_mutex,
                          CloseHandle=lambda h: cerrados.append(h))
    u32 = SimpleNamespace(MessageBoxW=lambda *a: avisos.append(a[1]))
    return SimpleNamespace(windll=SimpleNamespace(kernel32=k32, user32=u32)), avisos, cerrados


def test_mutex_existe_avisa_y_sale(tmp_path):
    ct, avisos, cerrados = _fake_ctypes(True)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path,
                                ctypes_mod=ct, es_windows=True) is True
    assert "se está actualizando" in avisos[0]
    assert cerrados == [1234]


def test_sin_mutex_sigue(tmp_path):
    ct, avisos, _ = _fake_ctypes(False)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path,
                                ctypes_mod=ct, es_windows=True) is False
    assert avisos == []


def test_version_distinta_avisa_y_sale(tmp_path):
    (tmp_path / ".build-version").write_text("1.0.1\n", encoding="utf-8")
    ct, avisos, _ = _fake_ctypes(False)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path,
                                ctypes_mod=ct, es_windows=True) is True
    assert "programa 1.0.0" in avisos[0] and "archivos 1.0.1" in avisos[0]


def test_version_igual_sigue(tmp_path):
    (tmp_path / ".build-version").write_text("1.0.0", encoding="utf-8")
    ct, avisos, _ = _fake_ctypes(False)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path,
                                ctypes_mod=ct, es_windows=True) is False
    assert avisos == []


def test_sin_marker_sigue(tmp_path):
    ct, avisos, _ = _fake_ctypes(False)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path,
                                ctypes_mod=ct, es_windows=True) is False


def test_no_windows_no_hace_nada(tmp_path):
    ct, avisos, _ = _fake_ctypes(True)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path,
                                ctypes_mod=ct, es_windows=False) is False
    assert avisos == []


def test_version_importable_sin_qt():
    import subprocess, sys
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0,'src'); import version; "
                        "assert not any(m.startswith(('PySide6','webview')) for m in sys.modules)"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_tras_actualizar_espera_a_que_desaparezca_el_mutex(tmp_path):
    estado = {"n": 0}
    avisos = []

    def open_mutex(a, h, n):
        estado["n"] += 1
        return 1 if estado["n"] <= 4 else 0

    ct = SimpleNamespace(windll=SimpleNamespace(
        kernel32=SimpleNamespace(OpenMutexW=open_mutex, CloseHandle=lambda h: None),
        user32=SimpleNamespace(MessageBoxW=lambda *a: avisos.append(a[1]))))
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path, ctypes_mod=ct,
                                es_windows=True, tras_actualizar=True,
                                sleep=lambda s: None) is False
    assert avisos == []


def test_tras_actualizar_mutex_persistente_sale(tmp_path):
    ct, avisos, _ = _fake_ctypes(True)
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path, ctypes_mod=ct,
                                es_windows=True, tras_actualizar=True,
                                sleep=lambda s: None) is True
    assert "se está actualizando" in avisos[0]


def test_sin_flag_no_espera(tmp_path):
    ct, avisos, _ = _fake_ctypes(True)
    def no_dormir(s):
        raise AssertionError("no debe esperar sin --tras-actualizar")
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path, ctypes_mod=ct,
                                es_windows=True, sleep=no_dormir) is True


def test_access_denied_cuenta_como_existe(tmp_path):
    avisos = []
    ct = SimpleNamespace(
        get_last_error=lambda: 5,
        windll=SimpleNamespace(
            kernel32=SimpleNamespace(OpenMutexW=lambda *a: 0, CloseHandle=lambda h: None),
            user32=SimpleNamespace(MessageBoxW=lambda *a: avisos.append(a[1]))))
    assert g.setup_en_marcha(ct) is True
    assert g.comprobar_arranque(version_programa="1.0.0", base=tmp_path, ctypes_mod=ct,
                                es_windows=True) is True


def test_null_con_otro_error_no_existe():
    ct = SimpleNamespace(
        get_last_error=lambda: 2,
        windll=SimpleNamespace(
            kernel32=SimpleNamespace(OpenMutexW=lambda *a: 0, CloseHandle=lambda h: None)))
    assert g.setup_en_marcha(ct) is False
