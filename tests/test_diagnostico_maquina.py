"""Tests de `atom_core/diagnostico_maquina.py`.

Todo el acceso a hardware real queda fuera: `os.name`, `subprocess.run`,
`open` (para /sys/block/.../rotational) y `psutil` se monkeypatchean para
que los tests no dependan de la máquina donde corren.
"""
from __future__ import annotations

import builtins

import pytest

from atom_core import diagnostico_maquina as dm


# --- tipo_disco: Linux -------------------------------------------------------


def _parchea_linux_rotational(monkeypatch, valor: str, dispositivo: str = "sda"):
    """Simula que `ruta` vive en un disco cuyo `rotational` vale `valor`."""
    monkeypatch.setattr(dm.os, "name", "posix")

    class _Stat:
        st_dev = 2049  # mayor=8, menor=1 (arbitrario, no importa el valor real)

    monkeypatch.setattr(dm.os, "stat", lambda ruta: _Stat())
    monkeypatch.setattr(dm.os, "major", lambda dev: 8)
    monkeypatch.setattr(dm.os, "minor", lambda dev: 1)
    monkeypatch.setattr(dm.os.path, "realpath", lambda p: f"/sys/devices/{dispositivo}/{dispositivo}1")
    monkeypatch.setattr(dm.os.path, "basename", lambda p: p.rsplit("/", 1)[-1])
    monkeypatch.setattr(dm.os.path, "dirname", lambda p: p.rsplit("/", 1)[0])

    def _exists(ruta):
        return ruta == f"/sys/block/{dispositivo}/queue/rotational"

    monkeypatch.setattr(dm.os.path, "exists", _exists)

    _open_original = builtins.open

    def _fake_open(ruta, *args, **kwargs):
        if ruta == f"/sys/block/{dispositivo}/queue/rotational":
            import io

            return io.StringIO(valor)
        if ruta == f"/sys/block/{dispositivo}/device/model":
            raise FileNotFoundError()
        return _open_original(ruta, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _fake_open)


def test_tipo_disco_linux_rotational_1_es_hdd(monkeypatch):
    _parchea_linux_rotational(monkeypatch, "1\n")
    resultado = dm.tipo_disco("/mnt/datos/foo")
    assert resultado["tipo"] == "HDD"


def test_tipo_disco_linux_rotational_0_es_ssd(monkeypatch):
    _parchea_linux_rotational(monkeypatch, "0\n")
    resultado = dm.tipo_disco("/mnt/datos/foo")
    assert resultado["tipo"] == "SSD"


def test_tipo_disco_linux_excepcion_da_desconocido(monkeypatch):
    monkeypatch.setattr(dm.os, "name", "posix")

    def _stat_falla(ruta):
        raise OSError("boom")

    monkeypatch.setattr(dm.os, "stat", _stat_falla)
    resultado = dm.tipo_disco("/mnt/datos/foo")
    assert resultado["tipo"] == "desconocido"
    assert resultado["modelo"] is None


# --- tipo_disco: Windows ------------------------------------------------------


class _ResultadoFake:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""


def test_tipo_disco_windows_hdd(monkeypatch):
    monkeypatch.setattr(dm.os, "name", "nt")

    def _run_fake(*args, **kwargs):
        return _ResultadoFake('{"MediaType":"HDD","FriendlyName":"WDC WD10"}')

    monkeypatch.setattr(dm.subprocess, "run", _run_fake)
    resultado = dm.tipo_disco("E:\\datos\\foo")
    assert resultado["tipo"] == "HDD"
    assert resultado["modelo"] == "WDC WD10"
    assert resultado["unidad"] == "E:"


def test_tipo_disco_windows_ssd(monkeypatch):
    monkeypatch.setattr(dm.os, "name", "nt")

    def _run_fake(*args, **kwargs):
        return _ResultadoFake('{"MediaType":"SSD","FriendlyName":"Samsung 970"}')

    monkeypatch.setattr(dm.subprocess, "run", _run_fake)
    resultado = dm.tipo_disco("D:\\datos\\foo")
    assert resultado["tipo"] == "SSD"


def test_tipo_disco_windows_timeout_da_desconocido(monkeypatch):
    monkeypatch.setattr(dm.os, "name", "nt")

    def _run_falla(*args, **kwargs):
        raise dm.subprocess.TimeoutExpired(cmd="powershell", timeout=10)

    monkeypatch.setattr(dm.subprocess, "run", _run_falla)
    resultado = dm.tipo_disco("E:\\datos\\foo")
    assert resultado["tipo"] == "desconocido"
    assert resultado["modelo"] is None
    assert resultado["unidad"] == "E:"


def test_tipo_disco_windows_sin_letra_da_desconocido(monkeypatch):
    monkeypatch.setattr(dm.os, "name", "nt")
    resultado = dm.tipo_disco("\\\\servidor\\recurso\\foo")
    assert resultado["tipo"] == "desconocido"


# --- sonda_inicial ------------------------------------------------------------


def test_sonda_inicial_sin_psutil_no_lanza_y_devuelve_dict_completo(monkeypatch):
    monkeypatch.setattr(dm, "psutil", None)
    monkeypatch.setattr(
        dm, "tipo_disco", lambda ruta: {"tipo": "desconocido", "modelo": None, "unidad": str(ruta)}
    )
    resultado = dm.sonda_inicial("/a", "/b")

    for clave in (
        "disco_origen",
        "disco_destino",
        "mismo_disco",
        "nucleos",
        "ram_total_gb",
        "ram_libre_gb",
        "cpu_ocupada_pct",
        "maquina_ocupada",
        "texto",
    ):
        assert clave in resultado

    assert resultado["cpu_ocupada_pct"] == 0.0
    assert resultado["maquina_ocupada"] is False
    assert isinstance(resultado["texto"], str) and resultado["texto"]


def test_sonda_inicial_texto_menciona_mecanico_y_avisa_mismo_disco(monkeypatch):
    disco_hdd = {"tipo": "HDD", "modelo": "WDC", "unidad": "E:"}

    def _tipo_disco_fake(ruta):
        return dict(disco_hdd)

    monkeypatch.setattr(dm, "tipo_disco", _tipo_disco_fake)

    class _PsutilFake:
        @staticmethod
        def cpu_count(logical=True):
            return 8

        @staticmethod
        def virtual_memory():
            from types import SimpleNamespace

            return SimpleNamespace(total=16 * 1024 ** 3, available=9.2 * 1024 ** 3)

        @staticmethod
        def cpu_percent(interval=0.5):
            return 12.0

    monkeypatch.setattr(dm, "psutil", _PsutilFake)

    resultado = dm.sonda_inicial("E:\\origen", "E:\\destino")

    assert resultado["mismo_disco"] is True
    assert resultado["maquina_ocupada"] is False
    assert "mecánico" in resultado["texto"]
    assert "MISMO disco mecánico" in resultado["texto"]


def test_sonda_inicial_maquina_ocupada(monkeypatch):
    monkeypatch.setattr(
        dm, "tipo_disco", lambda ruta: {"tipo": "SSD", "modelo": None, "unidad": str(ruta)}
    )

    class _PsutilFake:
        @staticmethod
        def cpu_count(logical=True):
            return 4

        @staticmethod
        def virtual_memory():
            from types import SimpleNamespace

            return SimpleNamespace(total=8 * 1024 ** 3, available=1 * 1024 ** 3)

        @staticmethod
        def cpu_percent(interval=0.5):
            return 80.0

    monkeypatch.setattr(dm, "psutil", _PsutilFake)

    resultado = dm.sonda_inicial("/a", "/b")

    assert resultado["maquina_ocupada"] is True
    assert "otros procesos usando la máquina" in resultado["texto"]


# --- Registro global del tipo de disco de ORIGEN ---------------------------


def test_registrar_y_consultar_tipo_disco_origen():
    dm.olvidar_tipo_disco_origen()
    assert dm.tipo_disco_origen_detectado() is None

    dm.registrar_tipo_disco_origen("HDD")
    assert dm.tipo_disco_origen_detectado() == "HDD"

    dm.registrar_tipo_disco_origen("SSD")
    assert dm.tipo_disco_origen_detectado() == "SSD"


def test_olvidar_tipo_disco_origen_resetea():
    dm.registrar_tipo_disco_origen("HDD")
    assert dm.tipo_disco_origen_detectado() == "HDD"

    dm.olvidar_tipo_disco_origen()
    assert dm.tipo_disco_origen_detectado() is None
