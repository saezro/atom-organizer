"""Fix 3.4.115: JPEG truncado en origen (apply) y excepción de cabecera (indice)."""
import io

from PIL import Image as PILImage

from atom_core import apply, indice


class _Senal:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *a, **k):
        self.mensajes.append(valor)


class _Manif:
    def __init__(self):
        self.fallidas = {}

    def marcar_fallida(self, fila_id, motivo):
        self.fallidas[fila_id] = motivo


def _jpeg_bytes():
    b = io.BytesIO()
    PILImage.new("RGB", (64, 64), (200, 10, 10)).save(b, "JPEG")
    return b.getvalue()


def _validar(tmp_path, contenidos):
    filas = []
    for i, c in enumerate(contenidos):
        p = tmp_path / f"i{i}.jpg"
        p.write_bytes(c)
        filas.append({"id": i, "ruta_origen": str(p)})
    m, s = _Manif(), _Senal()
    return apply._validar_jpeg_origen(m, filas, s), m, s


def test_jpeg_valido_pasa(tmp_path):
    ok, m, s = _validar(tmp_path, [_jpeg_bytes()])
    assert len(ok) == 1 and not m.fallidas and not s.mensajes


def test_jpeg_con_cola_a_ceros_rechazado(tmp_path):
    data = _jpeg_bytes()
    ok, m, s = _validar(tmp_path, [data[:-2] + b"\x00" * 50_000, data])
    assert [f["id"] for f in ok] == [1]
    assert "truncado" in m.fallidas[0]
    assert any("1 JPEG" in x for x in s.mensajes)


def test_jpeg_sin_eoi_rechazado(tmp_path):
    data = _jpeg_bytes()
    ok, m, _ = _validar(tmp_path, [data[:-2] + b"\x11" * 5000])
    assert not ok and 0 in m.fallidas


def test_jpeg_con_datos_tras_eoi_pasa(tmp_path):
    ok, m, _ = _validar(tmp_path, [_jpeg_bytes() + b"\x01\x02RJPEG" * 100])
    assert len(ok) == 1 and not m.fallidas


def test_jpeg_cola_valida_pura():
    from atom_core import lectura_segura as ls
    assert ls.jpeg_cola_valida(b"\x01\xff\xd9\x02")
    assert not ls.jpeg_cola_valida(b"")
    assert not ls.jpeg_cola_valida(b"\x00" * 4096)
    assert not ls.jpeg_cola_valida(b"\x01" * 4096)


def test_rgb_origen_servido_corto_es_fallido(tmp_path, monkeypatch):
    data = _jpeg_bytes() + b"\x07" * 20000
    p = tmp_path / "c.jpg"
    p.write_bytes(data)
    real_open = open

    class _Corto:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._f.close()

        def fileno(self):
            return self._f.fileno()

        def seek(self, n):
            return self._f.seek(n)

        def read(self, n=-1):
            return b""  # DriveFS: EOF prematuro

    monkeypatch.setattr("builtins.open", lambda r, m="r", *a, **k:
                        _Corto(real_open(r, m)) if str(r) == str(p) else real_open(r, m, *a, **k))
    m = _Manif()
    ok = apply._validar_jpeg_origen(m, [{"id": 5, "ruta_origen": str(p)}], _Senal())
    assert not ok and "incompleto en Drive" in m.fallidas[5]


def test_cabecera_con_excepcion_se_excluye(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    p = tmp_path / "a.jpg"
    p.write_bytes(_jpeg_bytes())
    assert indice._leer_cabecera(str(p))[1] is False

    def _boom(*a, **k):
        raise RuntimeError("fallo")
    monkeypatch.setattr(indice.os, "fstat", _boom)
    assert indice._leer_cabecera(str(p)) == (None, True)
    assert indice._leer_cabecera(str(tmp_path / "no_existe.jpg"))[1] is True


def test_indice_lectura_corta_excluye(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    p = tmp_path / "b.jpg"
    p.write_bytes(_jpeg_bytes() + b"\x07" * 200_000)
    real_stat = indice.os.fstat

    class _R:
        st_size = 5_500_000

    monkeypatch.setattr(indice.os, "fstat", lambda fd: _R())
    cab, medias = indice._leer_cabecera(str(p))
    assert medias is True
    monkeypatch.setattr(indice.os, "fstat", real_stat)
    assert indice._leer_cabecera(str(p))[1] is False


def test_worker_rechaza_cola_a_ceros():
    from atom_core import lectura_segura as ls
    datos = _jpeg_bytes()[:-2] + b"\x00" * 50_000
    assert not ls.jpeg_cola_valida(datos[-ls.VENTANA_COLA_JPEG:])
