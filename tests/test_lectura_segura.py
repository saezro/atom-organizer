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


def test_corta_dos_veces_ok_al_tercero(tmp_path, esperas, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    _parchear_lecturas_cortas(monkeypatch, f, 2)
    assert ls.leer_completo(f) == b"x" * 1000
    assert esperas == [0.5, 1.0]


def test_corta_persistente_lanza(tmp_path, esperas, monkeypatch):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    _parchear_lecturas_cortas(monkeypatch, f, 99)
    with pytest.raises(ls.LecturaIncompleta) as e:
        ls.leer_completo(f, intentos=3)
    assert (e.value.leidos, e.value.esperados) == (500, 1000)
    assert "leídos 500 de 1000 bytes tras 3 intentos" in str(e.value)


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
