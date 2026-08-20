"""Caché de inventario por prefijo (`Api._inv`, `_inventario_cacheado`,
`_inventario_precalentar`).

Antes era un slot único: si el operario cambiaba de inspección deprisa,
quedaban dos listados en vuelo y el que tardara más (el de la inspección ya
abandonada) pisaba el recién calculado del prefijo bueno. Ahora es un dict
por prefijo (`self._inv: dict[str, dict]`). Se cubre que dos prefijos no se
pisan (incluido el orden de terminación invertido, que era justo el bug), que
no se lanzan dos hilos para el mismo prefijo, que el TTL caduca, y que al
escribir una entrada nueva se podan las de otros prefijos ya caducadas.

Sigue las convenciones de `test_app_webview_broker.py`: `Api(broker=True)`
+ `sin_cliente_oauth` para no depender de `google_client.json`, e import
directo de `app_webview` (no requiere `webview` para importarse).
"""
from __future__ import annotations

import pytest

import app_webview as aw
from atom_core import cloud_upload


@pytest.fixture(autouse=True)
def sin_cliente_oauth(monkeypatch):
    from atom_core import cloud_config

    monkeypatch.setattr(cloud_config, "load_client", lambda base_dir=None: None)


class _AuthFalso:
    """`_get_auth()` de sobra para `_inventario_precalentar`: solo necesita
    `is_logged_in() -> True`."""

    def is_logged_in(self) -> bool:
        return True


class _HiloCapturado:
    """Sustituye `threading.Thread`: no lanza el hilo real, guarda el target
    para que el test decida cuándo (y en qué orden) correrlo. Así se puede
    forzar determinísticamente el caso "el hilo lanzado primero termina
    después" sin sleeps ni carreras reales."""

    instancias: list["_HiloCapturado"] = []

    def __init__(self, target=None, daemon=None, **kw):
        self.target = target
        _HiloCapturado.instancias.append(self)

    def start(self) -> None:
        pass  # el test decide cuándo correr `self.target()`


@pytest.fixture
def api(monkeypatch):
    """`Api(broker=True)` con auth falsa siempre logueada y `Thread` capturado
    en vez de lanzado de verdad."""
    _HiloCapturado.instancias.clear()
    a = aw.Api(broker=True)
    monkeypatch.setattr(a, "_get_auth", lambda: _AuthFalso())
    monkeypatch.setattr(aw.threading, "Thread", _HiloCapturado)
    return a


def _reloj(monkeypatch, inicio: float = 1000.0):
    """Reloj controlado a mano: `estado["t"]` es el valor que devolverá la
    próxima llamada a `time.monotonic()`; `avanzar()` lo mueve."""
    estado = {"t": inicio}
    monkeypatch.setattr(aw.time, "monotonic", lambda: estado["t"])

    def avanzar(delta: float) -> None:
        estado["t"] += delta

    return avanzar


def _listador(remotos_por_prefijo: dict[str, set]):
    """Reemplaza `cloud_upload.listar_objetos_remotos`: devuelve lo que le
    corresponda a cada prefijo, según un dict fijado por el test."""

    def fake(bucket, prefix, auth, **kw):
        return remotos_por_prefijo[prefix]

    return fake


def test_precalentar_dos_prefijos_lanza_dos_hilos(api, monkeypatch):
    monkeypatch.setattr(cloud_upload, "listar_objetos_remotos",
                        _listador({"P1/": {"a"}, "P2/": {"b"}}))
    api._inventario_precalentar("P1/")
    api._inventario_precalentar("P2/")
    assert len(_HiloCapturado.instancias) == 2
    prefijos_en_hilos = api._inv_hilos
    assert prefijos_en_hilos == {"P1/", "P2/"}


def test_prefijos_distintos_no_se_pisan_aunque_el_primero_termine_ultimo(api, monkeypatch):
    """El bug original: P1 se lanza antes que P2 pero su listado (más largo)
    termina después. Con caché por prefijo, cada uno debe quedarse con LO
    SUYO pase lo que pase con el orden de terminación."""
    monkeypatch.setattr(cloud_upload, "listar_objetos_remotos",
                        _listador({"P1/": {"solo-de-P1"}, "P2/": {"solo-de-P2"}}))

    api._inventario_precalentar("P1/")
    api._inventario_precalentar("P2/")
    assert len(_HiloCapturado.instancias) == 2
    hilo_p1, hilo_p2 = _HiloCapturado.instancias

    # P2 (lanzado segundo) termina PRIMERO; P1 termina después.
    hilo_p2.target()
    hilo_p1.target()

    assert api._inventario_cacheado("P1/") == {"solo-de-P1"}
    assert api._inventario_cacheado("P2/") == {"solo-de-P2"}


def test_no_lanza_dos_hilos_para_el_mismo_prefijo_en_vuelo(api, monkeypatch):
    monkeypatch.setattr(cloud_upload, "listar_objetos_remotos",
                        _listador({"P1/": {"a"}}))
    api._inventario_precalentar("P1/")
    assert len(_HiloCapturado.instancias) == 1
    # Segunda llamada mientras el primer hilo sigue "en vuelo" (no se ha
    # corrido su target, así que sigue en `_inv_hilos`): no debe lanzar otro.
    api._inventario_precalentar("P1/")
    assert len(_HiloCapturado.instancias) == 1


def test_ttl_caduca_y_cacheado_devuelve_none(api, monkeypatch):
    avanzar = _reloj(monkeypatch)
    monkeypatch.setattr(cloud_upload, "listar_objetos_remotos",
                        _listador({"P1/": {"a"}}))
    api._inventario_precalentar("P1/")
    _HiloCapturado.instancias[0].target()

    assert api._inventario_cacheado("P1/") == {"a"}
    avanzar(api.INV_TTL + 1)
    assert api._inventario_cacheado("P1/") is None


def test_entradas_caducadas_de_otros_prefijos_se_podan_al_escribir(api, monkeypatch):
    avanzar = _reloj(monkeypatch)
    monkeypatch.setattr(cloud_upload, "listar_objetos_remotos",
                        _listador({"P1/": {"a"}, "P2/": {"b"}}))

    api._inventario_precalentar("P1/")
    _HiloCapturado.instancias[0].target()
    assert "P1/" in api._inv

    avanzar(api.INV_TTL + 1)  # P1 caduca

    api._inventario_precalentar("P2/")
    _HiloCapturado.instancias[1].target()

    # Al escribir P2, la poda debe haber quitado el P1 ya caducado del dict
    # interno (no solo dejar de servirlo por `_inventario_cacheado`).
    assert "P1/" not in api._inv
    assert api._inventario_cacheado("P2/") == {"b"}
