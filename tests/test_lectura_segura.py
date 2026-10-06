import pytest

from atom_core import lectura_segura as ls


@pytest.fixture
def esperas(monkeypatch):
    llamadas = []
    monkeypatch.setattr(ls.time, "sleep", lambda s: llamadas.append(s))
    return llamadas


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


def test_camino_normal_sin_esperas(tmp_path, esperas):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    assert ls.leer_completo(f) == b"x" * 1000
    assert esperas == []


def test_corta_una_vez_ok_al_segundo(tmp_path, esperas, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    _parchear_lecturas_cortas(monkeypatch, f, 1)
    assert ls.leer_completo(f) == b"x" * 1000
    assert esperas == [0.5]


def test_corta_persistente_lanza(tmp_path, esperas, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    _parchear_lecturas_cortas(monkeypatch, f, 99)
    with pytest.raises(ls.LecturaIncompleta) as e:
        ls.leer_completo(f, intentos=3)
    assert (e.value.leidos, e.value.esperados) == (500, 1000)
    # EOF estable (500 y 500): falla en el 2.º intento, sin agotar los 3.
    assert "leídos 500 de 1000 bytes tras 2 intentos" in str(e.value)


def test_bytes_origen_mayor_que_stat(tmp_path, esperas):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    with pytest.raises(ls.LecturaIncompleta):
        ls.leer_completo(f, intentos=2, bytes_origen=200)


def test_copiar_completo(tmp_path, esperas):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"abc")
    ls.copiar_completo(f, tmp_path / "b.jpg")
    assert (tmp_path / "b.jpg").read_bytes() == b"abc"


def test_bytes_origen_distinto_de_stat_falla_al_momento_sin_esperas(tmp_path, esperas):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    for indexado in (200, 50):  # mayor o menor que el stat
        with pytest.raises(ls.LecturaIncompleta) as e:
            ls.leer_completo(f, intentos=5, bytes_origen=indexado)
        assert "el fichero cambió desde el índice: {0} vs 100 bytes".format(indexado) in str(e.value)
    assert esperas == []


def test_bytes_origen_igual_a_stat_ok(tmp_path, esperas):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    assert ls.leer_completo(f, bytes_origen=100) == b"x" * 100


def test_eof_estable_falla_en_segundo_intento(tmp_path, esperas, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    estado = _parchear_lecturas_cortas(monkeypatch, f, 99)
    with pytest.raises(ls.LecturaIncompleta) as e:
        ls.leer_completo(f, intentos=5)
    assert estado["lecturas"] == 2
    assert e.value.intentos == 2 and len(esperas) == 1


def test_lectura_que_crece_sigue_reintentando(tmp_path, esperas, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    tamanos = iter([200, 400, 600, 800])
    real_open = open

    class _Crece:
        def __init__(self, fh):
            self.fh = fh
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.fh.close()
        def read(self, *a):
            if a:
                return self.fh.read(*a)
            return self.fh.read()[: next(tamanos, 1000)]

    def fake_open(ruta, modo="r", *a, **k):
        fh = real_open(ruta, modo, *a, **k)
        return _Crece(fh) if str(ruta) == str(f) and modo == "rb" else fh

    monkeypatch.setattr("builtins.open", fake_open)
    assert ls.leer_completo(f, intentos=5) == b"x" * 1000
