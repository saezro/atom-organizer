"""Helpers de los tests de `lectura_segura` (y de su paridad con Rust).

Movidos de `tests/test_lectura_segura.py` sin cambiar su comportamiento:
`esperas` (fixture que captura `time.sleep`) y `_parchear_lecturas_cortas`.
`parchear_guion` los generaliza a un guion de tamaños por lectura entera, el mismo
contrato que `atom_rs._guion_lecturas` (la n-ésima lectura entera devuelve
`guion[n]` bytes; `-1` o fuera de la lista = completa).
"""
import pytest

from atom_core import lectura_segura as ls


@pytest.fixture
def esperas(monkeypatch):
    llamadas = []
    monkeypatch.setattr(ls.time, "sleep", lambda s: llamadas.append(s))
    return llamadas


def parchear_guion(monkeypatch, fichero, guion, eventos=None):
    """Las lecturas ENTERAS de `fichero` (`f.read()` sin argumento) devuelven los
    tamaños de `guion`; las lecturas por bloques (hidratación) son normales.
    `estado["lecturas"]` cuenta lecturas enteras. Si `eventos` es una lista, se le
    añade "leer"/"hidratar" por apertura (el primer `read` decide qué es)."""
    real_open = open
    estado = {"n": 0, "lecturas": 0}

    class _Guion:
        def __init__(self, f):
            self.f = f
            self.marcado = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def read(self, *a):
            if a:  # hidratación por bloques: lectura normal
                if eventos is not None and not self.marcado:
                    eventos.append("hidratar")
                self.marcado = True
                return self.f.read(*a)
            datos = self.f.read()
            if eventos is not None:
                eventos.append("leer")
            idx = estado["lecturas"]
            estado["lecturas"] += 1
            if idx < len(guion) and guion[idx] >= 0:
                return datos[: guion[idx]]
            return datos

    def fake_open(ruta, modo="r", *a, **k):
        f = real_open(ruta, modo, *a, **k)
        if str(ruta) == str(fichero) and modo == "rb":
            estado["n"] += 1
            return _Guion(f)
        return f

    monkeypatch.setattr("builtins.open", fake_open)
    return estado


def parchear_tam_stat(monkeypatch, fichero, tam):
    """`os.stat(fichero).st_size` declara `tam` bytes (los demás ficheros, normal)."""
    import types
    real_stat = ls.os.stat

    def fake_stat(ruta, *a, **k):
        if str(ruta) == str(fichero):
            return types.SimpleNamespace(st_size=tam)
        return real_stat(ruta, *a, **k)

    monkeypatch.setattr(ls.os, "stat", fake_stat)


def _parchear_lecturas_cortas(monkeypatch, fichero, cortas):
    """Las `cortas` primeras aperturas devuelven la mitad de los bytes."""
    real_open = open
    estado = {"n": 0}

    class _Corto:
        def __init__(self, f, estado, cortas):
            self.f, self.estado, self.cortas = f, estado, cortas
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.f.close()
        def read(self, *a):
            if a:  # hidratación por bloques: lectura normal
                return self.f.read(*a)
            datos = self.f.read()
            self.estado["lecturas"] = self.estado.get("lecturas", 0) + 1
            if self.estado["lecturas"] <= self.cortas:
                return datos[: len(datos) // 2]
            return datos

    def fake_open(ruta, modo="r", *a, **k):
        f = real_open(ruta, modo, *a, **k)
        if str(ruta) == str(fichero) and modo == "rb":
            estado["n"] += 1
            return _Corto(f, estado, cortas)
        return f

    monkeypatch.setattr("builtins.open", fake_open)
    return estado
