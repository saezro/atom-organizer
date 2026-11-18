"""Fase Rust ``io`` (4a, ABI 7): ``atom_rs.leer_completo`` y ``jpeg_integro_por_cola``
frente a ``atom_core.lectura_segura`` (referencia).

Cada caso de lectura se ejecuta con Python (helpers de ``helpers_lectura``) y con Rust
(puntos de inyección de la feature ``inyeccion-fallos``: la wheel de TEST) y se comparan
bytes, tipo de excepción tras ``rust_bridge.traducir_error``, ``str(exc)``, atributos y
la secuencia de esperas. En Rust las esperas no duermen (``_sin_dormir``): se compara la
secuencia calculada.

Sin la extensión (o sin la inyección) los tests de paridad se SALTAN en local; con
``ATOM_RUST_AB_ESTRICTO=1`` o ``ATOM_RS_REQUIRED=1`` FALLAN. Los tests de
``traducir_error`` usan una excepción falsa y corren siempre.

    python -m pytest tests/test_rust_lectura_paridad.py -q
    ATOM_RS_REQUIRED=1 ATOM_RUST_AB_ESTRICTO=1 python -m pytest tests/test_rust_lectura_paridad.py -v -rfs
"""
from __future__ import annotations

import sys
import importlib
import os
import pickle

import pytest

from atom_core import lectura_segura as ls
from atom_core import rust_bridge
from tests.helpers_lectura import parchear_guion, parchear_tam_stat

def _estricto():
    return (os.environ.get("ATOM_RUST_AB_ESTRICTO") == "1"
            or os.environ.get("ATOM_RS_REQUIRED") == "1")

_ATRIBUTOS = ("ruta", "leidos", "esperados", "intentos", "actual", "indexado")


# --------------------------------------------------------------------------- fixture

@pytest.fixture
def rs():
    """``atom_rs`` real con la fase ``io`` y la inyección compilada."""
    def _no(motivo):
        if _estricto():
            pytest.fail(f"ESTRICTO: {motivo}", pytrace=False)
        pytest.skip(motivo)

    try:
        m = importlib.import_module("atom_rs")
    except Exception as e:  # noqa: BLE001
        _no(f"atom_rs no disponible ({type(e).__name__}): la verificación queda para CI")
    for s in rust_bridge.SIMBOLOS_POR_FASE["io"]:
        if not hasattr(m, s):
            _no(f"atom_rs no expone {s} (wheel anterior a 4a)")
    if m.version_abi() != rust_bridge.ABI_ESPERADA:
        _no(f"atom_rs ABI {m.version_abi()} != {rust_bridge.ABI_ESPERADA}")
    if not (hasattr(m, "tiene_inyeccion") and m.tiene_inyeccion()):
        _no("atom_rs sin la feature inyeccion-fallos (wheel de release): no hay inyección")
    m._reset()
    yield m
    m._reset()


# --------------------------------------------------------------------------- runners

def _resumen(exc):
    return (type(exc).__name__, str(exc),
            {k: getattr(exc, k) for k in _ATRIBUTOS if hasattr(exc, k)})


def _python(monkeypatch, f, guion=None, stat=None, **kw):
    """Camino Python con los helpers; devuelve (resultado, esperas, eventos)."""
    esperas, eventos = [], []
    with monkeypatch.context() as mp:
        mp.setattr(ls.time, "sleep", lambda s: esperas.append(s))
        if guion is not None:
            parchear_guion(mp, f, guion, eventos=eventos)
        if stat is not None:
            parchear_tam_stat(mp, f, stat)
        try:
            res = ("ok", ls.leer_completo(f, **kw))
        except Exception as e:  # noqa: BLE001
            res = ("err", e)
    return res, esperas, eventos


def _rust(rs, f, guion=None, stat=None, **kw):
    rs._reset()
    rs._sin_dormir(True)
    if guion is not None:
        rs._guion_lecturas(str(f), guion)
    if stat is not None:
        rs._tam_stat_falso(str(f), stat)
    try:
        res = ("ok", rs.leer_completo(str(f), **kw))
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "ErrorFila":
            # Las esperas del error son las calculadas hasta el fallo.
            assert list(e.esperas) == rs._esperas_log()
        res = ("err", rust_bridge.traducir_error(e))
    eventos = [ev.split(":", 1)[0] for ev in rs._eventos() if ev.endswith(":" + str(f))]
    return res, rs._esperas_log(), eventos


def _paridad(monkeypatch, rs, f, guion=None, stat=None, **kw):
    py, esp_py, ev_py = _python(monkeypatch, f, guion=guion, stat=stat, **kw)
    ru, esp_ru, ev_ru = _rust(rs, f, guion=guion, stat=stat, **kw)
    assert py[0] == ru[0], f"Python={py!r} Rust={ru!r}"
    if py[0] == "ok":
        assert ru[1] == py[1]
    else:
        assert _resumen(ru[1]) == _resumen(py[1])
        assert isinstance(ru[1], type(py[1])) and type(ru[1]) is type(py[1])
    assert esp_ru == esp_py
    if guion is not None:
        assert ev_ru == ev_py
    return py, esp_py, ev_py


def _fichero(tmp_path, n=1000, nombre="a.jpg"):
    f = tmp_path / nombre
    f.write_bytes(bytes(i % 251 for i in range(n)))
    return f


# --------------------------------------------------------------------------- lectura

def test_camino_normal_sin_esperas(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path)
    py, esperas, _ = _paridad(monkeypatch, rs, f, guion=[-1])
    assert py[0] == "ok" and esperas == []


def test_lectura_truncada_una_vez(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path)
    py, esperas, _ = _paridad(monkeypatch, rs, f, guion=[500, -1])
    assert py[0] == "ok" and esperas == [0.5]


def test_lectura_truncada_persistente_eof_estable(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path)
    py, esperas, _ = _paridad(monkeypatch, rs, f, guion=[500, 500], intentos=5)
    assert isinstance(py[1], ls.LecturaIncompleta) and py[1].intentos == 2
    assert "leídos 500 de 1000 bytes tras 2 intentos" in str(py[1])
    assert esperas == [0.5]


@pytest.mark.parametrize("extra,esperadas", [
    ({}, [0.5, 1, 2, 4]),
    ({"backoff_max": 1.5}, [0.5, 1, 1.5, 1.5]),
])
def test_lectura_que_crece_sigue_reintentando(tmp_path, monkeypatch, rs, extra, esperadas):
    f = _fichero(tmp_path, 100)
    py, esperas, _ = _paridad(monkeypatch, rs, f, guion=[10, 20, 30, 40, 50], intentos=5, **extra)
    assert isinstance(py[1], ls.LecturaIncompleta) and py[1].intentos == 5
    assert esperas == esperadas


def test_fichero_cambia_de_tamano(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 100)
    for indexado in (200, 50):
        py, esperas, _ = _paridad(monkeypatch, rs, f, intentos=5, bytes_origen=indexado)
        assert isinstance(py[1], ls.FicheroCambiado) and esperas == []
        assert f"el fichero cambió desde el índice: {indexado} vs 100 bytes" in str(py[1])


def test_bytes_origen_mayor_que_stat(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 100)
    py, _, _ = _paridad(monkeypatch, rs, f, intentos=2, bytes_origen=200)
    assert isinstance(py[1], ls.LecturaIncompleta)  # FicheroCambiado es subclase


def test_bytes_origen_igual_a_stat_ok(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 100)
    py, _, _ = _paridad(monkeypatch, rs, f, bytes_origen=100)
    assert py[0] == "ok"


def test_bytes_origen_cero_no_comprueba(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 100)
    py, _, _ = _paridad(monkeypatch, rs, f, bytes_origen=0)
    assert py[0] == "ok"


@pytest.mark.parametrize("guion,intentos,eventos", [
    ([500, -1], 5, ["leer", "hidratar", "leer"]),
    ([10, 20, 30, 40, 50], 5, ["leer", "hidratar", "leer", "hidratar", "leer", "hidratar", "leer", "hidratar", "leer"]),
])
def test_hidrata_entre_intentos(tmp_path, monkeypatch, rs, guion, intentos, eventos):
    f = _fichero(tmp_path, 1000)
    _, _, ev = _paridad(monkeypatch, rs, f, guion=guion, intentos=intentos)
    assert ev == eventos  # (_paridad ya exige que Rust registre lo mismo)


def test_stat_falso_mayor_que_el_fichero(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 100)
    py, esperas, _ = _paridad(monkeypatch, rs, f, stat=200, intentos=5)
    assert (py[1].leidos, py[1].esperados, py[1].intentos) == (100, 200, 2)
    assert esperas == [0.5]


@pytest.mark.parametrize("intentos", [0, 1, -3])
def test_intentos_limite_sin_panic(tmp_path, monkeypatch, rs, intentos):
    f = _fichero(tmp_path, 1000)
    _paridad(monkeypatch, rs, f, guion=[500], intentos=intentos)


def test_fichero_vacio(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 0)
    py, _, _ = _paridad(monkeypatch, rs, f)
    assert py == ("ok", b"")


def test_ruta_inexistente_mismo_error(tmp_path, monkeypatch, rs):
    py, _, _ = _paridad(monkeypatch, rs, tmp_path / "no_existe.jpg")
    assert isinstance(py[1], FileNotFoundError)


def test_ruta_gs_es_error_visible_en_rust(rs):
    with pytest.raises(ValueError, match="gs://"):
        rs.leer_completo("gs://bucket/x.jpg")


def test_espera_negativa_mismo_error(tmp_path, monkeypatch, rs):
    """`time.sleep(-1)` real de Python lanza ValueError; Rust igual (sin panic)."""
    f = _fichero(tmp_path, 1000)
    with pytest.raises(ValueError) as ep:
        with monkeypatch.context() as mp:
            parchear_guion(mp, f, [500, -1])
            ls.leer_completo(f, backoff=-1.0)
    rs._reset()
    rs._guion_lecturas(str(f), [500, -1])
    with pytest.raises(ValueError) as er:
        rs.leer_completo(str(f), backoff=-1.0)
    assert str(er.value) == str(ep.value)


# --------------------------------------------------------------------------- picklado

def test_excepciones_picklables(tmp_path, monkeypatch, rs):
    f = _fichero(tmp_path, 1000)
    casos = [
        _rust(rs, f, guion=[500, 500]),
        _rust(rs, f, bytes_origen=7),
        _rust(rs, tmp_path / "no_existe.jpg"),
    ]
    for (kind, exc), _esp, _ev in casos:
        assert kind == "err"
        copia = pickle.loads(pickle.dumps(exc))
        assert type(copia) is type(exc) and _resumen(copia) == _resumen(exc)
    # La ErrorFila cruda también viaja entre procesos (atributos incluidos).
    rs._reset()
    rs._guion_lecturas(str(f), [500, 500])
    with pytest.raises(Exception) as ei:
        rs.leer_completo(str(f))
    cruda = pickle.loads(pickle.dumps(ei.value))
    assert type(cruda) is type(ei.value) and cruda.tipo == "lectura_incompleta"
    assert (cruda.leidos, cruda.esperados, cruda.intentos) == (500, 1000, 2)


# --------------------------------------------------------------------------- cola JPEG

def _seg(marcador, payload):
    return b"\xff" + bytes([marcador]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _casos_cola(base):
    eoi = base.index(b"\xff\xd9") + 2
    hasta_eoi = base[:eoi]
    sos = base.index(b"\xff\xda")
    soi_app1_miniatura = b"\xff\xd8" + _seg(0xE1, b"Exif\x00\x00" + b"\xff\xd9" + b"\x11" * 8)
    relleno_app2 = _seg(0xE2, b"\x11" * 60000) + _seg(0xE2, b"\x11" * 60000)
    cabecera_scan = _seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")
    return {
        "intacto": base,
        "hasta_eoi": hasta_eoi,
        "cola_a_ceros": base[: len(base) // 2] + b"\x00" * (len(base) // 2),
        "todo_a_ceros": b"\x00" * 4096,
        "sin_ffd9": base[:sos + 20],
        "truncado_a_mitad_de_scan": base[: (sos + eoi) // 2],
        "ffd9_solo_en_miniatura_exif": (soi_app1_miniatura + relleno_app2
                                        + cabecera_scan + b"\x55" * 64),
        "payload_rjpeg_tras_eoi": hasta_eoi + bytes((i % 250) + 1 for i in range(70000)),
        "payload_no_cero_70k": hasta_eoi + b"\x11" * 70000,
        "relleno_dji_4097": hasta_eoi + b"\x00" * 4097,
        "relleno_dji_mas_payload": hasta_eoi + b"\x00" * 4097 + b"\x22" * 70000,
        "vacio": b"",
        "solo_soi": b"\xff\xd8",
        "sin_soi": b"\x00\x01\x02\x03\x04\x05",
        "basura_entre_segmentos": b"\xff\xd8\x00\x00" + b"\x11" * 70000,
        "stuffing_y_rst": (b"\xff\xd8" + cabecera_scan + b"\x11\xff\x00\xff\xd0\x22" * 4
                           + b"\x33" * 70000 + b"\xff\xd9" + b"\x44" * 70000),
        "marcador_01_sin_longitud": b"\xff\xd8\xff\x01\xff\xd9" + b"\x11" * 70000,
    }


def _py_cola(d):
    return bool(ls.jpeg_cola_valida(d[-ls.VENTANA_COLA_JPEG:])
                or ls.jpeg_eoi_en_cola_util(d) or ls.jpeg_eoi_por_segmentos(d))


_NOMBRES_COLA = list(_casos_cola(b"\xff\xd8\xff\xda\x00\x02\xff\xd9").keys())


@pytest.mark.parametrize("caso", _NOMBRES_COLA)
def test_integridad_cola(tmp_path, make_dji_jpeg, rs, caso):
    ruta = tmp_path / "base.jpg"
    make_dji_jpeg(str(ruta))
    d = _casos_cola(ruta.read_bytes())[caso]
    assert rs.jpeg_integro_por_cola(d) is _py_cola(d)


def test_integridad_cola_resultados_esperados(tmp_path, make_dji_jpeg, rs):
    ruta = tmp_path / "base.jpg"
    make_dji_jpeg(str(ruta))
    casos = _casos_cola(ruta.read_bytes())
    for nombre, esperado in {
        "intacto": True, "hasta_eoi": True, "relleno_dji_4097": True,
        "payload_rjpeg_tras_eoi": True, "relleno_dji_mas_payload": True,
        "cola_a_ceros": False, "todo_a_ceros": False, "vacio": False,
        "truncado_a_mitad_de_scan": False, "ffd9_solo_en_miniatura_exif": False,
    }.items():
        assert rs.jpeg_integro_por_cola(casos[nombre]) is esperado, nombre


# ------------------------------------------------------------ traducir_error (sin Rust)

class ErrorFila(Exception):  # imita atom_rs.ErrorFila: el puente la reconoce por nombre
    pass


def _fila(tipo, mensaje="m", **attrs):
    e = ErrorFila(mensaje)
    base = dict(ruta=None, leidos=None, esperados=None, intentos=None, actual=None,
                indexado=None, errno=None, esperas=[])
    base.update(attrs, tipo=tipo)
    for k, v in base.items():
        setattr(e, k, v)
    return e


def test_traducir_lectura_incompleta_y_cambiado():
    e = rust_bridge.traducir_error(_fila(
        "lectura_incompleta", ruta="/x", leidos=5, esperados=9, intentos=3))
    ref = ls.LecturaIncompleta("/x", 5, 9, 3)
    assert type(e) is ls.LecturaIncompleta and str(e) == str(ref)
    c = rust_bridge.traducir_error(_fila("fichero_cambiado", ruta="/x", actual=100, indexado=200))
    ref = ls.FicheroCambiado("/x", 100, 200)
    assert type(c) is ls.FicheroCambiado and str(c) == str(ref)
    for exc in (e, c):
        copia = pickle.loads(pickle.dumps(exc))
        assert type(copia) is type(exc) and _resumen(copia) == _resumen(exc)


def test_traducir_jpeg_truncado_origen():
    e = rust_bridge.traducir_error(_fila("jpeg_truncado_origen", ruta="/a/b.JPG"))
    assert type(e) is OSError
    assert str(e) == "JPEG truncado en origen (cola a ceros/sin EOI): /a/b.JPG"


def test_traducir_io_con_errno_da_la_subclase_de_python():
    import errno
    e = rust_bridge.traducir_error(_fila("io", "x", ruta="/no/existe", errno=errno.ENOENT))
    assert type(e) is FileNotFoundError
    with pytest.raises(FileNotFoundError) as ref:
        open("/no/existe", "rb")
    assert str(e) == str(ref.value)
    copia = pickle.loads(pickle.dumps(e))
    assert type(copia) is FileNotFoundError and str(copia) == str(e)


def test_traducir_io_errno_13_da_permissionerror():
    import errno
    e = rust_bridge.traducir_error(_fila("io", "x", ruta="/p", errno=errno.EACCES))
    assert type(e) is PermissionError


@pytest.mark.parametrize("win,esperado", [(3, "FileNotFoundError"), (5, "PermissionError")])
def test_traducir_io_winerror_en_windows(monkeypatch, win, esperado):
    # En Linux `OSError(None, msg, ruta, winerror)` ignora winerror: se comprueba
    # que el puente lo pasa (4.º arg) en vez de usar errno; en Windows real, la subclase.
    es_windows = sys.platform == "win32"
    llamadas = []

    def falso(*a):
        llamadas.append(a)
        return OSError(*a[:3])

    monkeypatch.setattr(rust_bridge.sys, "platform", "win32")
    monkeypatch.setattr(rust_bridge, "OSError", falso, raising=False)
    rust_bridge.traducir_error(_fila("io", "msg", ruta="C:\\x", errno=None, winerror=win))
    assert llamadas == [(None, "msg", "C:\\x", win)]
    if es_windows:
        monkeypatch.undo()
        e = rust_bridge.traducir_error(_fila("io", "msg", ruta="C:\\x", errno=None, winerror=win))
        assert type(e).__name__ == esperado


def test_traducir_xmp_verificacion_panic_y_desconocido():
    from atom_core.apply import XmpDemasiadoGrandeError
    x = rust_bridge.traducir_error(_fila("xmp_grande", "El bloque XMP (1 B) supera"))
    assert type(x) is XmpDemasiadoGrandeError and str(x) == "El bloque XMP (1 B) supera"
    v = rust_bridge.traducir_error(_fila("verificacion", "no decodifica"))
    assert type(v) is OSError and str(v) == "no decodifica"
    p = rust_bridge.traducir_error(_fila("panic", "boom"))
    assert type(p) is rust_bridge.PanicRust and str(p) == "boom"
    assert type(pickle.loads(pickle.dumps(p))) is rust_bridge.PanicRust
    d = rust_bridge.traducir_error(_fila("inventado"))
    assert type(d) is RuntimeError and "inventado" in str(d)


def test_traducir_otra_excepcion_se_devuelve_igual():
    e = ValueError("x")
    assert rust_bridge.traducir_error(e) is e



# --------------------------------------------------------------------------- wrapper (T3.1)

class _RsFalso:
    """Doble de ``atom_rs`` para probar la delegación de ``lectura_segura`` sin wheel."""
    def __init__(self, excepcion=None):
        self.llamadas = []
        self._exc = excepcion

    def leer_completo(self, *a):
        self.llamadas.append(a)
        if self._exc is not None:
            raise self._exc
        return b"rs"

    def jpeg_integro_por_cola(self, datos):
        self.llamadas.append(datos)
        return True


def test_wrapper_delega_en_rust_y_normaliza_argumentos(tmp_path, monkeypatch):
    rs = _RsFalso()
    monkeypatch.setattr(rust_bridge, "exigir", lambda fase: rs)
    assert ls.leer_completo(tmp_path / "a.jpg", bytes_origen=0) == b"rs"
    assert rs.llamadas == [(str(tmp_path / "a.jpg"), 5, 0.5, 8.0, None)]
    assert ls.jpeg_integro_por_cola(b"xx") is True


def test_wrapper_traduce_error_fila(tmp_path, monkeypatch):
    class ErrorFila(Exception):
        tipo = "lectura_incompleta"
        ruta = "r"
        leidos, esperados, intentos = 1, 2, 3

    monkeypatch.setattr(rust_bridge, "exigir", lambda fase: _RsFalso(ErrorFila("x")))
    with pytest.raises(ls.LecturaIncompleta) as ei:
        ls.leer_completo(tmp_path / "a.jpg")
    assert "leídos 1 de 2 bytes tras 3 intentos (r)" in str(ei.value)


def test_wrapper_sin_fase_usa_python(tmp_path, monkeypatch):
    monkeypatch.setattr(rust_bridge, "exigir", lambda fase: None)
    f = _fichero(tmp_path)
    assert ls.leer_completo(f) == f.read_bytes()
    ls.copiar_completo(f, tmp_path / "b.jpg")
    assert (tmp_path / "b.jpg").read_bytes() == f.read_bytes()
