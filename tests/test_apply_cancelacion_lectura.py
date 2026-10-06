"""Cancelación y timeout en la validación previa de JPG y en el aforo (apply)."""
import threading
import time

import pytest

from atom_core import apply, cancelacion


class _Senal:
    def emit(self, *a, **k):
        pass


class _Manif:
    def __init__(self):
        self.fallidas = {}

    def marcar_fallida(self, fila_id, motivo):
        self.fallidas[fila_id] = motivo


@pytest.fixture(autouse=True)
def _limpia():
    cancelacion.limpiar()
    yield
    cancelacion.limpiar()


def _filas(n):
    return [{"id": i, "ruta_origen": f"/x/{i}.jpg"} for i in range(n)]


def test_validacion_cancelada_no_lee_y_sale(monkeypatch):
    llamadas = []
    monkeypatch.setattr(apply, "_motivo_jpeg_truncado", lambda r: llamadas.append(r))
    cancelacion.solicitar()
    with pytest.raises(cancelacion.RunCancelado):
        apply._validar_jpeg_origen(_Manif(), _filas(5), _Senal())
    assert llamadas == []


def test_validacion_timeout_cuenta_como_fallido(monkeypatch):
    monkeypatch.setattr(apply, "TIMEOUT_LECTURA_JPEG_S", 0.2)
    liberar = threading.Event()

    def motivo(ruta):
        if ruta.endswith("/0.jpg"):
            liberar.wait(5)
        return None

    monkeypatch.setattr(apply, "_motivo_jpeg_truncado", motivo)
    m = _Manif()
    try:
        ok = apply._validar_jpeg_origen(m, _filas(2), _Senal())
    finally:
        liberar.set()
    assert m.fallidas == {0: "timeout lectura"}
    assert [f["id"] for f in ok] == [1]


def test_aforo_cancelacion_durante_espera():
    aforo = apply._AforoDinamico(1)
    assert aforo.adquirir() is True  # agota el único permiso
    threading.Timer(0.3, cancelacion.solicitar).start()
    t0 = time.monotonic()
    assert aforo.adquirir() is False
    assert time.monotonic() - t0 < 3
    aforo.liberar()
    cancelacion.limpiar()
    assert aforo.adquirir() is True
