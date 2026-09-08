"""Tests de `atom_core/medicion_recursos.py`.

Todo el `psutil` real queda fuera: se sustituye por un doble de control total
(`_FakePsutil`) y el hilo de fondo real por `_FakeThread` (que nunca ejecuta
`_bucle_muestreo`), así las muestras de CPU se inyectan a mano en las listas
internas. `time.monotonic` también se monkeypatchea con una secuencia fija de
valores, para que ningún test dependa de un `sleep` real ni sea flaky por
scheduling de hilos.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from atom_core import medicion_recursos as mr


MiB = 1024 * 1024


# --- dobles de psutil / threading -------------------------------------------


class _FakePsutil:
    """Doble de `psutil` con control total de disco/CPU/núcleos.

    `disk_snapshots`: lista de `(read_bytes, write_bytes)` (o `None`, o una
    instancia de `Exception`) que se devuelve una por cada llamada sucesiva a
    `disk_io_counters()`, en orden.
    """

    def __init__(self, disk_snapshots=None, cpu_percent_value=50.0, nucleos=4):
        self._disco_iter = iter(disk_snapshots or [])
        self.cpu_percent_value = cpu_percent_value
        self.cpu_percent_llamadas = 0
        self._nucleos = nucleos

    def cpu_percent(self, percpu=False):
        self.cpu_percent_llamadas += 1
        return self.cpu_percent_value

    def disk_io_counters(self):
        item = next(self._disco_iter)
        if isinstance(item, Exception):
            raise item
        if item is None:
            return None
        leidos, escritos = item
        return SimpleNamespace(read_bytes=leidos, write_bytes=escritos)

    def cpu_count(self, logical=True):
        return self._nucleos


class _FakeThread:
    """Doble de `threading.Thread`: nunca ejecuta el `target` de verdad.

    Así el muestreo de fondo queda bajo control del test (se inyecta a mano
    en `_muestras_cpu_fase`/`_muestras_cpu_total`) y no hay carreras con un
    hilo real.
    """

    instancias: list = []

    def __init__(self, target=None, name=None, daemon=None):
        self.target = target
        self.name = name
        self.daemon = daemon
        self._vivo = False
        _FakeThread.instancias.append(self)

    def start(self):
        self._vivo = True

    def is_alive(self):
        return self._vivo

    def join(self, timeout=None):
        self._vivo = False


@pytest.fixture(autouse=True)
def _reset_fake_thread():
    """Cada test arranca con la lista de instancias de `_FakeThread` vacía."""
    _FakeThread.instancias = []
    yield
    _FakeThread.instancias = []


def _secuencia(valores):
    """Devuelve una función sin argumentos que va consumiendo `valores` en orden."""
    it = iter(valores)
    return lambda: next(it)


def _inyectar_muestra(medidor, valor):
    """Simula lo que haría `_bucle_muestreo` en un ciclo: añade `valor` a
    ambas listas (fase y total) bajo el lock del medidor."""
    with medidor._lock:
        medidor._muestras_cpu_fase.append(valor)
        medidor._muestras_cpu_total.append(valor)


def _preparar_medidor(monkeypatch, disk_snapshots, cpu_percent_value=50.0, nucleos=4):
    """Construye un `MedidorRecursos` con `psutil` y `threading.Thread` falseados."""
    fake_psutil = _FakePsutil(
        disk_snapshots=disk_snapshots, cpu_percent_value=cpu_percent_value, nucleos=nucleos
    )
    monkeypatch.setattr(mr, "psutil", fake_psutil)
    monkeypatch.setattr(mr.threading, "Thread", _FakeThread)
    return mr.MedidorRecursos(), fake_psutil


# --- 1. `_veredicto` ---------------------------------------------------------


def test_veredicto_por_debajo_de_40_es_disco():
    assert mr._veredicto(10.0) == "disco"
    assert mr._veredicto(39.9) == "disco"


def test_veredicto_entre_40_y_70_es_mixto():
    assert mr._veredicto(55.0) == "mixto"


def test_veredicto_por_encima_de_70_es_cpu():
    assert mr._veredicto(70.1) == "cpu"
    assert mr._veredicto(95.0) == "cpu"


def test_veredicto_borde_exacto_40_es_mixto():
    assert mr._veredicto(40.0) == "mixto"


def test_veredicto_borde_exacto_70_es_mixto():
    assert mr._veredicto(70.0) == "mixto"


def test_veredicto_none_devuelve_none():
    assert mr._veredicto(None) is None


# --- 2. Flujo normal: iniciar -> abrir_fase -> cerrar_fase -------------------


def test_cerrar_fase_devuelve_contrato_completo_con_mb_por_resta(monkeypatch):
    disco = [
        (0, 0),  # snapshot en iniciar() (total y fase arrancan igual)
        (150 * MiB, 80 * MiB),  # snapshot en abrir_fase()
        (170 * MiB, 95 * MiB),  # snapshot final, dentro de cerrar_fase()
    ]
    medidor, _ = _preparar_medidor(monkeypatch, disco, cpu_percent_value=99.0, nucleos=8)

    # iniciar (t=100) -> abrir_fase (t=110) -> cerrar_fase (t=115): fase de 5s.
    monkeypatch.setattr(mr.time, "monotonic", _secuencia([100.0, 110.0, 115.0]))

    medidor.iniciar()
    medidor.abrir_fase()
    _inyectar_muestra(medidor, 80.0)
    _inyectar_muestra(medidor, 90.0)  # media = 85.0 -> "cpu"

    resumen = medidor.cerrar_fase()

    assert set(resumen.keys()) == {
        "mb_leidos",
        "mb_escritos",
        "mb_por_segundo",
        "cpu_pct",
        "nucleos",
        "veredicto",
    }
    # MB = resta entre el snapshot de abrir_fase y el de cierre, no acumulado bruto.
    assert resumen["mb_leidos"] == 20.0
    assert resumen["mb_escritos"] == 15.0
    # (20 + 15) MB / 5 s
    assert resumen["mb_por_segundo"] == 7.0
    assert resumen["cpu_pct"] == 85.0
    assert resumen["nucleos"] == 8
    assert resumen["veredicto"] == "cpu"


# --- 3. `resumen_total()` agrega todo el run --------------------------------


def test_resumen_total_agrega_todas_las_fases_no_solo_la_ultima(monkeypatch):
    disco = [
        (0, 0),  # iniciar()
        (0, 0),  # abrir_fase() de la fase 1
        (20 * MiB, 15 * MiB),  # cerrar_fase() fase 1
        (20 * MiB, 15 * MiB),  # abrir_fase() de la fase 2
        (50 * MiB, 40 * MiB),  # cerrar_fase() fase 2
        (50 * MiB, 40 * MiB),  # resumen_total()
    ]
    medidor, _ = _preparar_medidor(monkeypatch, disco, nucleos=4)

    monkeypatch.setattr(
        mr.time,
        "monotonic",
        _secuencia([0.0, 10.0, 15.0, 15.0, 25.0, 30.0]),
    )

    medidor.iniciar()

    medidor.abrir_fase()
    _inyectar_muestra(medidor, 20.0)
    _inyectar_muestra(medidor, 30.0)  # media fase 1 = 25.0 -> "disco"
    fase1 = medidor.cerrar_fase()
    assert fase1["veredicto"] == "disco"

    medidor.abrir_fase()
    _inyectar_muestra(medidor, 80.0)
    _inyectar_muestra(medidor, 90.0)  # media fase 2 = 85.0 -> "cpu"
    fase2 = medidor.cerrar_fase()
    assert fase2["veredicto"] == "cpu"

    total = medidor.resumen_total()

    # Disco: acumulado desde iniciar() (0) hasta el snapshot final (50/40 MB).
    assert total["mb_leidos"] == 50.0
    assert total["mb_escritos"] == 40.0
    assert total["mb_por_segundo"] == 3.0  # (50+40) MB / 30 s

    # CPU: media de las CUATRO muestras (20,30,80,90) = 55.0, no solo la fase 2.
    assert total["cpu_pct"] == 55.0
    assert total["veredicto"] == "mixto"


# --- 4. Degradación sin romper ------------------------------------------------


def test_sin_psutil_todo_queda_inerte_sin_lanzar(monkeypatch):
    monkeypatch.setattr(mr, "psutil", None)
    medidor = mr.MedidorRecursos()

    # No debe lanzar en ningún punto del ciclo de vida.
    medidor.iniciar()
    medidor.abrir_fase()
    medidor.detener()

    assert medidor.cerrar_fase() is None
    assert medidor.resumen_total() is None


def test_disk_io_counters_devuelve_none_no_lanza(monkeypatch):
    disco = [None, None, None]  # iniciar, abrir_fase, cerrar_fase
    medidor, _ = _preparar_medidor(monkeypatch, disco)
    monkeypatch.setattr(mr.time, "monotonic", _secuencia([0.0, 0.0, 5.0]))

    medidor.iniciar()
    medidor.abrir_fase()
    resumen = medidor.cerrar_fase()

    assert resumen is not None
    assert resumen["mb_leidos"] == 0.0
    assert resumen["mb_escritos"] == 0.0


def test_disk_io_counters_lanza_permission_error_no_propaga(monkeypatch):
    disco = [PermissionError("sin permiso"), PermissionError("sin permiso"), PermissionError("sin permiso")]
    medidor, _ = _preparar_medidor(monkeypatch, disco)
    monkeypatch.setattr(mr.time, "monotonic", _secuencia([0.0, 0.0, 5.0]))

    medidor.iniciar()
    medidor.abrir_fase()
    resumen = medidor.cerrar_fase()

    assert resumen is not None
    assert resumen["mb_leidos"] == 0.0
    assert resumen["mb_escritos"] == 0.0


# --- 5. Fase demasiado corta: sin veredicto aventurado -----------------------


def test_fase_de_menos_de_2s_no_da_veredicto(monkeypatch):
    disco = [(0, 0), (0, 0), (0, 0)]
    medidor, _ = _preparar_medidor(monkeypatch, disco)
    # abrir_fase en t=10, cerrar_fase en t=11: solo 1s de fase (< 2 * 1.0s).
    monkeypatch.setattr(mr.time, "monotonic", _secuencia([0.0, 10.0, 11.0]))

    medidor.iniciar()
    medidor.abrir_fase()
    _inyectar_muestra(medidor, 90.0)
    _inyectar_muestra(medidor, 90.0)

    resumen = medidor.cerrar_fase()

    assert resumen["veredicto"] is None
    assert resumen["cpu_pct"] == 0.0


# --- 6. `iniciar()` idempotente ----------------------------------------------


def test_iniciar_dos_veces_no_duplica_el_hilo(monkeypatch):
    disco = [(0, 0)]
    medidor, _ = _preparar_medidor(monkeypatch, disco)
    monkeypatch.setattr(mr.time, "monotonic", _secuencia([0.0]))

    medidor.iniciar()
    assert len(_FakeThread.instancias) == 1
    assert _FakeThread.instancias[0].is_alive()

    # Segunda llamada: el hilo ya está vivo, no debe crear otro.
    medidor.iniciar()
    assert len(_FakeThread.instancias) == 1


# --- 7. `detener()` sin `iniciar()` previo -----------------------------------


def test_detener_sin_haber_iniciado_no_lanza(monkeypatch):
    medidor, _ = _preparar_medidor(monkeypatch, disk_snapshots=[])

    medidor.detener()  # no debe lanzar

    assert medidor._hilo is None
