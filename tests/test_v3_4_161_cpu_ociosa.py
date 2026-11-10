"""v3.4.161: RGB sobre-suscrito en Cloud Run, bytes_origen sin metadatos, cuota cgroup, telemetría."""
import io
import json

import pytest
import utils

from atom_core import apply as apply_mod
from atom_core import paralelismo, phases

_ENVS = ("CLOUD_RUN_JOB", "CLOUD_RUN_EXECUTION", "CLOUD_RUN_TASK_INDEX", "K_SERVICE", "ATOM_RGB_WORKERS")


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    for e in _ENVS:
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setattr(utils, "cpus_efectivas", lambda: 4)
    monkeypatch.setattr(utils, "workers_para_lote", lambda mb_por_worker=utils.MB_POR_WORKER: 3)


def test_local_sin_cambio_aunque_remoto(monkeypatch):
    monkeypatch.setattr(utils, "memoria_cgroup_mb", lambda: 100000.0)
    assert paralelismo.maximo_rgb(remoto=True) == 3


def test_cloud_run_remoto_doble_de_vcpu(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", "x")
    monkeypatch.setattr(utils, "memoria_cgroup_mb", lambda: 100000.0)
    assert paralelismo.maximo_rgb(remoto=True) == 8


def test_cloud_run_execution_tambien(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "x")
    monkeypatch.setattr(utils, "memoria_cgroup_mb", lambda: None)
    assert paralelismo.maximo_rgb(remoto=True) == 8


def test_cloud_run_local_no_sobresuscribe(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", "x")
    assert paralelismo.maximo_rgb(remoto=False) == 3


def test_cloud_run_tope_por_ram(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_JOB", "x")
    monkeypatch.setattr(utils, "memoria_cgroup_mb", lambda: 5 * utils.MB_POR_WORKER + 10.0)
    assert paralelismo.maximo_rgb(remoto=True) == 5


def test_override_env(monkeypatch):
    monkeypatch.setenv("ATOM_RGB_WORKERS", "11")
    assert paralelismo.maximo_rgb() == 11
    monkeypatch.setenv("ATOM_RGB_WORKERS", "basura")
    assert paralelismo.maximo_rgb() == 3


def test_bytes_origen_evita_metadatos(monkeypatch):
    def _no(_):
        raise AssertionError("no debe pedir metadatos")

    class _Stop(Exception):
        pass

    def _leer(ruta, bytes_origen=None):
        assert bytes_origen == 1234
        raise _Stop

    monkeypatch.setattr(apply_mod, "_tamano_ruta", _no)
    monkeypatch.setattr(apply_mod.lectura_segura, "leer_completo", _leer)
    fila = {"ruta_origen": "gs://b/a.jpg", "bytes_origen": 1234, "angulo_giro": 0}
    with pytest.raises(_Stop):
        apply_mod._escribir_salidas_de_fila(fila, type("C", (), {"compress_level": 5})(), type("P", (), {"_ROTATION_JPEG_QUALITY": 90})())


def test_sin_bytes_cae_a_metadatos(monkeypatch):
    llamadas = []

    class _Stop(Exception):
        pass

    monkeypatch.setattr(apply_mod, "_tamano_ruta", lambda r: llamadas.append(r) or 77)

    def _leer(ruta, bytes_origen=None):
        assert bytes_origen == 77
        raise _Stop

    monkeypatch.setattr(apply_mod.lectura_segura, "leer_completo", _leer)
    fila = {"ruta_origen": "gs://b/a.jpg", "angulo_giro": 0}
    with pytest.raises(_Stop):
        apply_mod._escribir_salidas_de_fila(fila, type("C", (), {"compress_level": 5})(), type("P", (), {"_ROTATION_JPEG_QUALITY": 90})())
    assert llamadas == ["gs://b/a.jpg"]


def test_cuota_manda_en_cloud_run(monkeypatch):
    monkeypatch.undo()  # recupera cpus_efectivas real
    monkeypatch.setattr(utils.os, "sched_getaffinity", lambda _p: {0, 1, 2}, raising=False)
    monkeypatch.setattr(utils, "_cuota_cgroup_cpu", lambda: 4)
    assert utils.cpus_efectivas() == 3  # fuera de Cloud Run: mínimo
    monkeypatch.setenv("CLOUD_RUN_JOB", "x")
    assert utils.cpus_efectivas() == 4


def test_telemetria_campos_nuevos(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(utils, "memoria_limite_cgroup_mb", lambda: 8192.0)
    host = type("H", (), {"fase": "rgb", "ejecucion": "e", "shard_index": 0, "shard_count": 1})()
    cfg = type("C", (), {"output_folder": str(tmp_path)})()
    cb = type("CB", (), {"emit": lambda self, m: None})()
    phases._emitir_telemetria(host, cfg, {"imagenes": 1}, 10.0, 5.0, True, cb)
    dato = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert dato["rss_pico_mb"] > 0
    assert dato["mem_limite_mb"] == 8192.0
