"""Vigía de subidas aplazadas: un job con `no_antes` futuro acaba subiendo solo."""
from __future__ import annotations

import threading
import time

import pytest

import app_webview as aw
from atom_core import cola_subidas
from atom_core.credencial import ESTADO_OK, ESTADO_SIN_CONEXION


@pytest.fixture
def api(monkeypatch, tmp_path):
    a = aw.Api(broker=True)
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: tmp_path / "cola.json")
    a.estado = {"e": ESTADO_OK}
    monkeypatch.setattr(a._credencial, "actual", lambda: {"estado": a.estado["e"]})
    return a


def test_job_aplazado_30s_se_sube_sin_reiniciar(api, monkeypatch, tmp_path):
    reloj = {"t": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: reloj["t"])
    carpeta = tmp_path / "v"
    carpeta.mkdir()
    job = cola_subidas.encolar(str(carpeta), "p", 7)
    _aplazar(job["id"], reloj["t"] + 30)
    assert cola_subidas.pendientes() and not cola_subidas.listo(cola_subidas.pendientes()[0])
    lanzadas, dormidas, hecho = [], [], threading.Event()

    def sube(folder, **k):
        lanzadas.append(folder)
        hecho.set()
        return {"started": True}
    monkeypatch.setattr(api, "cloud_upload", sube)

    def esperar(s):                      # reloj simulado: dormir = avanzar el tiempo
        dormidas.append(s)
        reloj["t"] += s
    api._vigia_esperar = esperar
    assert api.cloud_drenar(dormir=lambda s: None) == {"lanzados": 0}
    assert hecho.wait(5)
    assert lanzadas == [str(carpeta)] and 29 <= dormidas[0] <= 31
    for _ in range(100):                  # el hilo termina solo: nada de bucle caliente
        if not api.__dict__.get("_vigia_activo"):
            break
        time.sleep(0.02)
    assert not api._vigia_activo and len(dormidas) == 1


def test_armar_es_idempotente_un_solo_hilo(api, monkeypatch, tmp_path):
    ahora = time.time()
    carpeta = tmp_path / "v"
    carpeta.mkdir()
    job = cola_subidas.encolar(str(carpeta), "p", 7)
    _aplazar(job["id"], ahora + 3600)
    arrancados = []
    monkeypatch.setattr(api, "_vigia_aplazados", lambda *a: arrancados.append(1))
    api._armar_vigia_aplazados()
    api._armar_vigia_aplazados()
    time.sleep(0.1)
    assert len(arrancados) == 1


def test_sin_conexion_el_vigia_sale_y_no_drena(api, monkeypatch, tmp_path):
    carpeta = tmp_path / "v"
    carpeta.mkdir()
    job = cola_subidas.encolar(str(carpeta), "p", 7)
    _aplazar(job["id"], time.time() + 5)
    api.estado["e"] = ESTADO_SIN_CONEXION
    monkeypatch.setattr(api, "cloud_drenar", lambda *a, **k: pytest.fail("sin red"))
    api._vigia_activo = True
    api._vigia_aplazados(esperar=lambda s: pytest.fail("no debe dormir"))
    assert api._vigia_activo is False


def test_drenar_timeout_sale_y_deja_reintento_al_vigia(api, monkeypatch, tmp_path):
    carpeta = tmp_path / "v"
    carpeta.mkdir()
    cola_subidas.encolar(str(carpeta), "p", 7)
    monkeypatch.setattr(api, "cloud_upload", lambda *a, **k: {"started": True})
    api._uploading = True                     # la subida no termina nunca
    api._DRENAR_ESPERA_MAX_S = 3
    armado = []
    monkeypatch.setattr(api, "_armar_vigia_aplazados", lambda inc=False: armado.append(inc))
    dormidas = []
    assert api.cloud_drenar(dormir=dormidas.append) == {"lanzados": 1}
    assert len(dormidas) == 3 and armado == [True]


def _aplazar(job_id, no_antes):
    import json
    ruta = cola_subidas._ruta_cola()
    jobs = json.loads(ruta.read_text(encoding="utf-8"))
    for j in jobs:
        if j["id"] == job_id:
            j["no_antes"] = no_antes
    ruta.write_text(json.dumps(jobs), encoding="utf-8")


def test_vencido_bloqueado_por_otra_subida_queda_vigilado_y_sale_al_quedar_libre(
        api, monkeypatch, tmp_path):
    """Un job cuyo `no_antes` venció mientras había otra subida `busy` no se queda
    huérfano: el vigía lo cuenta y lo lanza cuando la otra subida termina."""
    carpeta = tmp_path / "v"
    carpeta.mkdir()
    job = cola_subidas.encolar(str(carpeta), "p", 7)
    _aplazar(job["id"], time.time() - 5)          # ya vencido
    otra = {"busy": True}
    lanzadas, hecho = [], threading.Event()

    def sube(folder, **k):
        if otra["busy"]:
            return {"started": False, "busy": True, "reason": "Ya hay una subida en curso."}
        lanzadas.append(folder)
        api._cola_subidas_cerrar(k["job_cola"], None)
        hecho.set()
        return {"started": True}
    monkeypatch.setattr(api, "cloud_upload", sube)
    esperas = []

    def esperar(s):
        esperas.append(s)
        otra["busy"] = False                      # la otra subida termina
    api._vigia_esperar = esperar
    assert api.cloud_drenar(dormir=lambda s: None) == {"lanzados": 0}
    assert cola_subidas.pendientes()[0]["intentos"] == 0   # busy no consume intento
    assert hecho.wait(5)
    assert lanzadas == [str(carpeta)] and cola_subidas.pendientes() == []
    assert esperas and esperas[0] == api._VIGIA_REINTENTO_TIMEOUT_S


def test_drenado_es_exclusivo_y_pide_otra_pasada(api, monkeypatch):
    lock = api.__dict__.setdefault("_drenar_lock", threading.Lock())
    lock.acquire()
    try:
        assert api.cloud_drenar() == {"lanzados": 0, "en_curso": True}
        assert api._drenar_otra_vez is True
    finally:
        lock.release()


def test_lanzar_drenado_no_bloquea_al_llamador(api, monkeypatch):
    soltar, empezado = threading.Event(), threading.Event()

    def drenar_lento():
        empezado.set()
        soltar.wait(5)
        return {"lanzados": 0}
    monkeypatch.setattr(api, "cloud_drenar", drenar_lento)
    t0 = time.monotonic()
    api._lanzar_drenado()
    assert time.monotonic() - t0 < 1 and empezado.wait(5)
    soltar.set()


def test_drenar_cola_ilegible_deja_vigia_armado(api, monkeypatch):
    def ilegible(*a, **k):
        raise cola_subidas.ColaIlegible("EIO")
    monkeypatch.setattr(cola_subidas, "pendientes", ilegible)
    arrancados = []
    monkeypatch.setattr(api, "_vigia_aplazados", lambda *a: arrancados.append(1))
    assert api.cloud_drenar(dormir=lambda s: None) == {"lanzados": 0}
    assert arrancados == [1] and api._vigia_incompleto is True
