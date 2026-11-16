"""Fase Rust ``indice`` (T2): parseo EXIF/XMP de la cabecera en ``atom_rs.parsear_cabecera``.

Dos bloques:

1. Tests LOCALES (sin Rust): un ``atom_rs`` FALSO en ``sys.modules`` sujeta la
   integración de ``indice._leer_metadatos``:
   - flag off -> no se toca ``atom_rs`` (camino Python de siempre);
   - flag on  -> los campos que da Rust se usan tal cual y NO se ejecutan las réplicas
     Python; un campo que Rust no da (``None``) cae al encadenado original y suma 1 a
     ``rust_parse_fallbacks`` (visible en el log del índice, nunca silencioso);
   - flag on + wheel sin ``parsear_cabecera`` -> ``RustNoDisponible``.

2. Tests con ``atom_rs`` REAL (se saltan limpiamente si el módulo cargado no expone
   ``parsear_cabecera``): equivalencia campo a campo Python vs Rust sobre buffers
   sintéticos (XMP atributo/elemento, truncados, XMP más allá de 256 KiB, TIFF con
   tag 700, Make no DJI, ``gs://``) y, con ``-m rust_ab`` + ``ATOM_RUST_CORPUS``, sobre
   el corpus real (solo lectura, en secuencia).

     python -m pytest tests/test_rust_indice_equiv.py -q
     ATOM_RUST_CORPUS=<dir> python -m pytest tests/test_rust_indice_equiv.py -m rust_ab -q
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import importlib
import io
import math
import os
import struct
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

import exif as exif_mod
from atom_core import almacen, indice, rust_bridge
from atom_core.rust_bridge import RustNoDisponible

from tests import rust_corpus


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    monkeypatch.delenv("ATOM_RUST", raising=False)
    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)


class _Cb:
    def __init__(self):
        self.lineas: list[str] = []

    def emit(self, *a, **k):
        if a:
            self.lineas.append(str(a[0]))


# ---------------------------------------------------------------- utilidades

_NOMBRES_XMP = ("AbsoluteAltitude", "RelativeAltitude", "GimbalRollDegree", "FlightRollDegree",
                "FlightYawDegree", "FlightPitchDegree", "CamReverse", "GimbalReverse", "RtkFlag")


def _xmp_atributos(valores: dict) -> bytes:
    attrs = "\n".join(f'   drone-dji:{k}="{v}"' for k, v in valores.items())
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/">\n <rdf:RDF xmlns:rdf="http://www.w3.org/'
            '1999/02/22-rdf-syntax-ns#">\n  <rdf:Description rdf:about="DJI Meta Data"\n'
            '   xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"\n'
            f'{attrs}>\n  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>').encode("latin-1")


def _xmp_elementos(valores: dict) -> bytes:
    elems = "\r\n".join(f"   <drone-dji:{k}>{v}</drone-dji:{k}>" for k, v in valores.items())
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/">\r\n <rdf:RDF>\r\n  <rdf:Description>\r\n'
            f'{elems}\r\n  </rdf:Description>\r\n </rdf:RDF>\r\n</x:xmpmeta>').encode("latin-1")


_XMP_DJI = {"AbsoluteAltitude": "+512.30", "RelativeAltitude": "+48.30",
            "GimbalRollDegree": "+0.00", "GimbalYawDegree": "-87.40",
            "GimbalPitchDegree": "-45.20", "FlightRollDegree": "+1.10",
            "FlightYawDegree": "-86.90", "FlightPitchDegree": "-2.30",
            "CamReverse": "0", "GimbalReverse": "0", "RtkFlag": "50"}


def _segmento(marcador: int, datos: bytes) -> bytes:
    return bytes((0xFF, marcador)) + struct.pack(">H", len(datos) + 2) + datos


def _exif_bytes(make="DJI", model="FC6310", fecha="2026:08:20 10:11:12",
                lat=(40, 25, 1234, 100), lon=(3, 41, 55, 10), lat_ref="N", lon_ref="W",
                con_gps=True) -> bytes:
    import piexif
    cero = {}
    if make is not None:
        cero[piexif.ImageIFD.Make] = make
    if model is not None:
        cero[piexif.ImageIFD.Model] = model
    exif_ifd = {piexif.ExifIFD.DateTimeOriginal: fecha} if fecha is not None else {}
    gps = {}
    if con_gps:
        gps = {piexif.GPSIFD.GPSLatitudeRef: lat_ref,
               piexif.GPSIFD.GPSLatitude: ((lat[0], 1), (lat[1], 1), (lat[2], lat[3])),
               piexif.GPSIFD.GPSLongitudeRef: lon_ref,
               piexif.GPSIFD.GPSLongitude: ((lon[0], 1), (lon[1], 1), (lon[2], lon[3]))}
    return piexif.dump({"0th": cero, "Exif": exif_ifd, "GPS": gps})


def _jpeg_dji(exif: bytes | None = None, xmp: bytes | None = None, ancho=64, alto=48,
              relleno_app3: int = 0, xmp_tras_relleno: bool = False) -> bytes:
    """JPEG con la disposición de un DJI: SOI, APP1 Exif, APP1 XMP, [APP3 de relleno],
    resto del JPEG de PIL (sin su APP0 JFIF)."""
    b = io.BytesIO()
    Image.new("RGB", (ancho, alto), color=(120, 130, 140)).save(b, format="JPEG")
    pil = b.getvalue()
    resto = pil[2:]
    if resto[:2] == b"\xff\xe0":
        resto = resto[2 + struct.unpack(">H", resto[2:4])[0]:]
    seg_exif = _segmento(0xE1, exif) if exif is not None else b""
    seg_xmp = (_segmento(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp)
               if xmp is not None else b"")
    relleno = b""
    while relleno_app3 > 0:
        n = min(relleno_app3, 65000)
        relleno += _segmento(0xE3, bytes((i * 7) & 0xFF for i in range(n)))
        relleno_app3 -= n
    if xmp_tras_relleno:
        return b"\xff\xd8" + seg_exif + relleno + seg_xmp + resto
    return b"\xff\xd8" + seg_exif + seg_xmp + relleno + resto


_REQUERIDO = os.environ.get("ATOM_RS_REQUIRED") == "1"


def _rs_real():
    """``atom_rs`` real con ``parsear_cabecera``. Sin él, skip limpio en local; con
    ``ATOM_RS_REQUIRED=1`` (job de CI ``rust-wheels.yml``) es un FALLO, para que la
    equivalencia nunca se salte en silencio donde debe ejecutarse."""
    def _no(motivo: str):
        if _REQUERIDO:
            pytest.fail(f"ATOM_RS_REQUIRED=1 y {motivo}")
        pytest.skip(motivo)

    try:
        m = importlib.import_module("atom_rs")
    except Exception as e:  # noqa: BLE001
        _no(f"atom_rs no disponible ({type(e).__name__}): la verificación queda para CI")
    if not hasattr(m, "parsear_cabecera"):
        _no("atom_rs cargado no expone parsear_cabecera (wheel anterior a T2)")
    if m.version_abi() != rust_bridge.ABI_ESPERADA:
        _no(f"atom_rs ABI {m.version_abi()} != {rust_bridge.ABI_ESPERADA}")
    return m


def _leer(ruta, gi, monkeypatch, rust: bool, contador=None):
    if rust:
        monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    else:
        monkeypatch.delenv("ATOM_RUST_FASES", raising=False)
    try:
        return indice._leer_metadatos(ruta, gi, _Cb(), contador_rust=contador)
    finally:
        monkeypatch.delenv("ATOM_RUST_FASES", raising=False)


def _diferencias(a, b) -> list[str]:
    da, db = dataclasses.asdict(a), dataclasses.asdict(b)
    return [f"{k}: py={da[k]!r} rust={db[k]!r}" for k in da if not _igual(da[k], db[k])]


def _igual(x, y) -> bool:
    """``==`` pero con NaN == NaN (GPS con denominador 0 sale NaN en ambos lados),
    también dentro de tuplas/listas/dicts anidados."""
    if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
        return True
    if isinstance(x, (tuple, list)) and isinstance(y, (tuple, list)):
        return type(x) is type(y) and len(x) == len(y) and all(_igual(a, b) for a, b in zip(x, y))
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(_igual(x[k], y[k]) for k in x)
    return x == y


def _gi(logger):
    return exif_mod.GeneralInformationFromImage(logger)


# ------------------------------------------------- 1. tests locales (módulo falso)

def _cab(**kw):
    base = dict(timestamp_iso=None, modelo=None, make=None, gps=None, ancho=None, alto=None,
                pil_estado="incierto", gimbal_yaw=None, gimbal_pitch=None, xmp=None,
                xmp_presente=False, flight_yaw_en_xmp=False, ausentes=())
    base.update(kw)
    return types.SimpleNamespace(**base)


def _falso(parsear=None, con_simbolo=True):
    m = types.ModuleType("atom_rs")
    m.version_abi = lambda: rust_bridge.ABI_ESPERADA
    for nombres in rust_bridge.SIMBOLOS_POR_FASE.values():
        for s in nombres:
            setattr(m, s, lambda *a, **k: None)
    if con_simbolo:
        m.parsear_cabecera = parsear or (lambda buf: _cab())
    elif hasattr(m, "parsear_cabecera"):
        delattr(m, "parsear_cabecera")
    return m


def test_fase_indice_registrada_con_abi_vigente():
    assert rust_bridge.ABI_ESPERADA >= 5
    assert rust_bridge.SIMBOLOS_POR_FASE["indice"] == ("parsear_cabecera",)


def test_local_flag_off_no_toca_atom_rs(tmp_path, monkeypatch, logger):
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))

    def _boom(buf):
        raise AssertionError("flag off: no debe llamarse a Rust")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(_boom))
    dato = indice._leer_metadatos(str(ruta), _gi(logger), _Cb())
    assert dato.modelo == "FC6310" and dato.make == "DJI"
    assert dato.timestamp == dt.datetime(2026, 8, 20, 10, 11, 12)


def test_local_sin_simbolo_lanza(tmp_path, monkeypatch, logger):
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(con_simbolo=False))
    monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    with pytest.raises(RustNoDisponible, match="parsear_cabecera"):
        indice._leer_metadatos(str(ruta), _gi(logger), _Cb())


def test_local_flag_on_usa_rust_sin_replicas(tmp_path, monkeypatch, logger):
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    xmp = ["512.30", "48.30", "0.00", "1.10", "-86.90", "-2.30", "0", "0", "50"]
    vistos = []

    def _parsear(buf):
        vistos.append(len(buf))
        return _cab(timestamp_iso="2030:01:02 03:04:05", modelo="RUST", make=" DJI\x00",
                    gps=(1.5, -2.5), ancho=640, alto=512, pil_estado="abre",
                    gimbal_yaw="-87.40", gimbal_pitch="-45.20", xmp=xmp, xmp_presente=True,
                    flight_yaw_en_xmp=True)
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(_parsear))

    def _boom(*a, **k):
        raise AssertionError("réplica Python ejecutada con la fase Rust activa")
    for f in ("_timestamp_desde_buffer", "_modelo_desde_buffer", "_make_desde_buffer",
              "_gps_desde_buffer", "_dimensiones_desde_buffer", "_bloque_xmp_desde_buffer"):
        monkeypatch.setattr(indice, f, _boom)
    gi = _gi(logger)
    monkeypatch.setattr(gi, "get_gimbal_yaw_pitch", _boom)
    monkeypatch.setattr(gi, "get_xmp_data", _boom)
    contador = indice._ContadorRust()
    dato = _leer(str(ruta), gi, monkeypatch, rust=True, contador=contador)
    assert vistos == [ruta.stat().st_size]
    assert dato.timestamp == dt.datetime(2030, 1, 2, 3, 4, 5)
    assert (dato.modelo, dato.make, dato.gps) == ("RUST", "DJI", (1.5, -2.5))
    assert (dato.yaw, dato.pitch, dato.flight_yaw) == (-87.4, -45.2, -86.9)
    assert dato.meta_leida is True
    p = dato.posicion
    assert (p.ancho_px, p.alto_px, p.altitud_abs, p.altura_relativa, p.gimbal_roll,
            p.flight_yaw, p.lat, p.lon) == (640, 512, "512.30", "48.30", "0.00", "-86.90",
                                             1.5, -2.5)
    assert contador.total == 0


def test_local_flag_on_none_cae_a_python_identico_y_cuenta(tmp_path, monkeypatch, logger):
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(lambda buf: _cab()))
    gi = _gi(logger)
    py = _leer(str(ruta), gi, monkeypatch, rust=False)
    contador = indice._ContadorRust()
    rs = _leer(str(ruta), gi, monkeypatch, rust=True, contador=contador)
    assert _diferencias(py, rs) == []
    # `xmp` no suma: con el fichero entero en el buffer, sin bloque XMP = ausencia cierta.
    assert set(contador.por_campo) == {"timestamp", "modelo", "make", "gps", "dimensiones"}
    assert contador.total == 5 and contador.ejemplos == [str(ruta)]


def _jpeg_roto_tras_256k() -> bytes:
    """Térmica-like: Exif + XMP al principio, APP3 de relleno que lleva el SOS más allá
    de 256 KiB y, ahí, un marcador inválido (0xFF01: «no marker found» en Pillow) antes
    del resto del JPEG. Termina en EOI (`jpeg_sin_eoi=False`): Pillow no lo abre ni con
    el buffer ni con el fichero entero -> fecha/GPS None en Python."""
    sano = _jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI), relleno_app3=300_000)
    corte = sano.index(b"\xff\xdb")  # primer DQT: tras el relleno
    assert corte > indice._BYTES_CABECERA
    roto = sano[:corte] + b"\xff\x01" + sano[corte:]
    assert roto.endswith(b"\xff\xd9")
    return roto


_CAB_CORTO_CON_VALORES = dict(
    timestamp_iso="2026:08:20 10:11:12", modelo="FC6310", make="DJI",
    gps=(40.4, -3.7), ancho=64, alto=48, pil_estado="corto", gimbal_yaw="-87.40",
    gimbal_pitch="-45.20",
    xmp=["512.30", "48.30", "0.00", "1.10", "-86.90", "-2.30", "0", "0", "50"],
    xmp_presente=True, flight_yaw_en_xmp=True)


def test_local_corto_va_siempre_por_python(tmp_path, monkeypatch, logger):
    """`pil_estado="corto"`: fecha, GPS y dimensiones por el camino Python original
    aunque Rust traiga valores (decisión del controlador), y suman al contador."""
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    cab = dict(_CAB_CORTO_CON_VALORES, timestamp_iso="2030:01:02 03:04:05", gps=(9.0, 9.0),
               ancho=1, alto=1)
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(lambda buf: _cab(**cab)))
    gi = _gi(logger)
    py = _leer(str(ruta), gi, monkeypatch, rust=False)
    contador = indice._ContadorRust()
    dato = _leer(str(ruta), gi, monkeypatch, rust=True, contador=contador)
    assert _diferencias(py, dato) == []
    assert (dato.posicion.ancho_px, dato.posicion.alto_px) == (64, 48)  # de Python
    assert dato.timestamp == dt.datetime(2026, 8, 20, 10, 11, 12)
    # `corto` es lo esperado: 1 en `corto`, ningún fallback inesperado.
    assert (contador.total, contador.corto, dict(contador.por_campo)) == (0, 1, {})


def test_local_corto_marcadores_rotos_tras_256k_mismos_none(tmp_path, monkeypatch, logger):
    """Con EOI y marcadores rotos tras 256 KiB, Python da fecha/GPS None; aunque Rust
    (falso) trajera valores en `corto`, el resultado es el de Python."""
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)  # cola/EOI real
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_roto_tras_256k())
    monkeypatch.setitem(sys.modules, "atom_rs",
                        _falso(lambda buf: _cab(**_CAB_CORTO_CON_VALORES)))
    gi = _gi(logger)
    py = _leer(str(ruta), gi, monkeypatch, rust=False)
    assert py.jpeg_sin_eoi is False
    assert py.timestamp is None and py.gps is None
    contador = indice._ContadorRust()
    rus = _leer(str(ruta), gi, monkeypatch, rust=True, contador=contador)
    assert _diferencias(py, rus) == []
    assert (contador.total, contador.corto) == (0, 1)


def test_local_excepcion_de_rust_cuenta_y_lee_con_python(tmp_path, monkeypatch, logger):
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))

    def _revienta(buf):
        raise ValueError("fallo simulado")
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(_revienta))
    gi = _gi(logger)
    py = _leer(str(ruta), gi, monkeypatch, rust=False)
    contador = indice._ContadorRust()
    rus = _leer(str(ruta), gi, monkeypatch, rust=True, contador=contador)
    assert _diferencias(py, rus) == []
    assert (contador.total, dict(contador.por_campo), contador.corto) == (1, {"excepcion": 1}, 0)


def test_local_leer_lote_linea_con_corto(tmp_path, monkeypatch, logger):
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    monkeypatch.setitem(sys.modules, "atom_rs",
                        _falso(lambda buf: _cab(**_CAB_CORTO_CON_VALORES)))
    monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    cb = _Cb()
    indice._leer_lote([str(ruta)], _gi(logger), cb, indice._EmisorIndice(cb, _Cb()),
                      2, 30.0, 30.0, 60.0)
    assert ("rust_parse_fallbacks=0 rust_parse_corto=1 rust_parse_no_soportado=0"
            in "".join(cb.lineas))


def test_local_leer_lote_reporta_contador(tmp_path, monkeypatch, logger):
    buena = tmp_path / "DJI_0001.JPG"
    buena.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(lambda buf: _cab()))
    monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    cb = _Cb()
    emisor = indice._EmisorIndice(cb, _Cb())
    indice._leer_lote([str(buena)], _gi(logger), cb, emisor, 2, 30.0, 30.0, 60.0)
    texto = "".join(cb.lineas)
    assert "rust_parse_fallbacks=5" in texto and "DJI_0001.JPG" in texto


def test_local_ausentes_y_no_soportado_no_son_fallback(tmp_path, monkeypatch, logger):
    """Tag inexistente (``ausentes``) y formato no JPEG: no suman a ``rust_parse_fallbacks``;
    el TIFF/PNG va entero por Python con su contador aparte y Rust ni se llama."""
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(
        lambda buf: _cab(pil_estado="abre", ancho=64, alto=48,
                         ausentes=("timestamp", "gps", "modelo", "make"))))
    gi = _gi(logger)
    py = _leer(str(ruta), gi, monkeypatch, rust=False)
    c = indice._ContadorRust()
    rus = _leer(str(ruta), gi, monkeypatch, rust=True, contador=c)
    assert _diferencias(py, rus) == []
    assert (c.total, c.corto, c.no_soportado) == (0, 0, 0)

    llamadas = []
    monkeypatch.setitem(sys.modules, "atom_rs", _falso(lambda b: llamadas.append(1) or _cab()))
    for nombre, formato in (("a.png", "PNG"), ("a.tif", "TIFF")):
        f = tmp_path / nombre
        Image.new("RGB", (8, 8)).save(f, format=formato)
        py = _leer(str(f), gi, monkeypatch, rust=False)
        c = indice._ContadorRust()
        rus = _leer(str(f), gi, monkeypatch, rust=True, contador=c)
        assert _diferencias(py, rus) == []
        assert (c.total, c.corto, c.no_soportado) == (0, 0, 1)
    assert llamadas == []


def test_diferencias_nan_igual_a_nan():
    a = indice._MetadatosImagen(ruta="r", nombre="n", timestamp=None, modelo=None, yaw=None,
                                gps=(float("nan"), 1.0))
    b = dataclasses.replace(a, gps=(float("nan"), 1.0))
    assert _diferencias(a, b) == []
    assert _diferencias(a, dataclasses.replace(a, gps=(0.0, 1.0))) != []


def test_local_leer_lote_flag_off_sin_linea_rust(tmp_path, logger):
    buena = tmp_path / "DJI_0001.JPG"
    buena.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
    cb = _Cb()
    indice._leer_lote([str(buena)], _gi(logger), cb, indice._EmisorIndice(cb, _Cb()),
                      2, 30.0, 30.0, 60.0)
    assert "rust_parse_fallbacks" not in "".join(cb.lineas)


# ------------------------------------------------- 2. atom_rs real

def _cmp_xmp_python(buf: bytes, gi):
    bloque = indice._bloque_xmp_desde_buffer(buf, "/no/existe")
    return (tuple(gi.get_gimbal_yaw_pitch(None, bloque_xmp=bloque)),
            tuple(gi.get_xmp_data(None, bloque_xmp=bloque)), "FlightYawDegree" in bloque)


@pytest.mark.parametrize("variante", [
    {"GimbalYawDegree": "+12.5", "GimbalPitchDegree": "-0.0", "FlightYawDegree": "1e-3"},
    {"GimbalYawDegree": "-90", "GimbalPitchDegree": ".5", "RtkFlag": "1.2.3",
     "AbsoluteAltitude": "--4", "RelativeAltitude": "²3"},
    {"GimbalPitchDegree": "-45.2"},  # resto ausente -> "0"
])
@pytest.mark.parametrize("forma", ["atributo", "elemento"])
def test_xmp_variantes(variante, forma, tmp_path, monkeypatch, logger):
    rs = _rs_real()
    gi = _gi(logger)
    xmp = (_xmp_atributos if forma == "atributo" else _xmp_elementos)(variante)
    buf = _jpeg_dji(_exif_bytes(), xmp)
    cab = rs.parsear_cabecera(buf)
    gimbal, datos, flight = _cmp_xmp_python(buf, gi)
    assert cab.xmp_presente is True
    assert (cab.gimbal_yaw, cab.gimbal_pitch) == gimbal
    assert tuple(cab.xmp) == datos
    assert cab.flight_yaw_en_xmp is flight
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(buf)
    assert _diferencias(_leer(str(ruta), gi, monkeypatch, False),
                        _leer(str(ruta), gi, monkeypatch, True)) == []
    if "FlightYawDegree" not in variante:
        assert _leer(str(ruta), gi, monkeypatch, True).flight_yaw is None


def test_xmp_tag_duplicado_gana_el_primero(logger):
    rs = _rs_real()
    gi = _gi(logger)
    xmp = _xmp_atributos({"GimbalYawDegree": "abc", "GimbalPitchDegree": "-10"}) + \
        b" " + _xmp_atributos({"GimbalYawDegree": "33.3", "GimbalPitchDegree": "-20"})
    buf = _jpeg_dji(_exif_bytes(), xmp)
    cab = rs.parsear_cabecera(buf)
    gimbal, datos, _flight = _cmp_xmp_python(buf, gi)
    assert (cab.gimbal_yaw, cab.gimbal_pitch) == gimbal
    assert tuple(cab.xmp) == datos


def test_exif_truncado_devuelve_none(tmp_path, monkeypatch, logger):
    rs = _rs_real()
    gi = _gi(logger)
    completo = _jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI))
    # Cortes en mitad del APP1 Exif, en el XMP, a mitad del JPEG y bytes sueltos.
    cortes = sorted({1, 2, 3, 5, 11, 20, 40, 80, 150, 300, len(completo) // 2,
                     len(completo) - 3})
    for n in range(0, len(completo) + 1):
        rs.parsear_cabecera(completo[:n])  # nunca panica ni lanza
    for n in cortes:
        ruta = tmp_path / f"CORTE_{n}.JPG"
        ruta.write_bytes(completo[:n])
        py = _leer(str(ruta), gi, monkeypatch, False)
        rus = _leer(str(ruta), gi, monkeypatch, True)
        assert _diferencias(py, rus) == [], n


def test_xmp_mas_alla_de_256k(tmp_path, monkeypatch, logger):
    rs = _rs_real()
    gi = _gi(logger)
    buf = _jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI), relleno_app3=300_000,
                    xmp_tras_relleno=True)
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(buf)
    cab = rs.parsear_cabecera(buf[:indice._BYTES_CABECERA])
    assert cab.xmp_presente is False
    llamadas = []
    original = exif_mod.leer_bloque_xmp

    def _espia(r):
        llamadas.append(r)
        return original(r)
    monkeypatch.setattr(exif_mod, "leer_bloque_xmp", _espia)
    py = _leer(str(ruta), gi, monkeypatch, False)
    n_py = len(llamadas)
    rus = _leer(str(ruta), gi, monkeypatch, True)
    assert n_py == 1 and len(llamadas) == 2
    assert _diferencias(py, rus) == []
    assert rus.yaw == -87.4


def test_marcadores_rotos_tras_256k_mismos_none(tmp_path, monkeypatch, logger):
    rs = _rs_real()
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)  # cola/EOI real
    gi = _gi(logger)
    buf = _jpeg_roto_tras_256k()
    cab = rs.parsear_cabecera(buf[:indice._BYTES_CABECERA])
    assert cab.pil_estado == "corto"
    assert (cab.timestamp_iso, cab.gps, cab.ancho, cab.alto) == (None, None, None, None)
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(buf)
    py = _leer(str(ruta), gi, monkeypatch, False)
    contador = indice._ContadorRust()
    rus = _leer(str(ruta), gi, monkeypatch, True, contador)
    assert (contador.total, contador.corto) == (0, 1)
    assert py.jpeg_sin_eoi is False
    assert py.timestamp is None and py.gps is None
    assert _diferencias(py, rus) == []


def test_tiff_r_jpeg(tmp_path, monkeypatch, logger):
    rs = _rs_real()
    gi = _gi(logger)
    xmp = _xmp_atributos(_XMP_DJI)
    ruta = tmp_path / "DJI_0001_R.TIFF"
    Image.new("L", (640, 512), color=7).save(str(ruta), format="TIFF", tiffinfo={700: xmp})
    buf = ruta.read_bytes()[:indice._BYTES_CABECERA]
    assert len(buf) == indice._BYTES_CABECERA
    cab = rs.parsear_cabecera(buf)
    if indice._tiff_exif_en_buffer(buf):
        assert indice._tiff_xmp_desde_buffer(buf) is not None
    gimbal, datos, flight = _cmp_xmp_python(buf, gi) if cab.xmp_presente else (None, None, None)
    if cab.xmp_presente:
        assert ((cab.gimbal_yaw, cab.gimbal_pitch), tuple(cab.xmp), cab.flight_yaw_en_xmp) == \
            (gimbal, datos, flight)
    assert _diferencias(_leer(str(ruta), gi, monkeypatch, False),
                        _leer(str(ruta), gi, monkeypatch, True)) == []


def test_make_no_dji_motivo_igual(tmp_path, monkeypatch, logger):
    _rs_real()
    gi = _gi(logger)
    for i, make in enumerate(("Canon", "  SONY\x00", "", None, "dji")):
        ruta = tmp_path / f"IMG_{i}.JPG"
        ruta.write_bytes(_jpeg_dji(_exif_bytes(make=make), _xmp_atributos(_XMP_DJI)))
        py = _leer(str(ruta), gi, monkeypatch, False)
        rus = _leer(str(ruta), gi, monkeypatch, True)
        assert _diferencias(py, rus) == [], make
        assert indice._motivo_no_dji(py) == indice._motivo_no_dji(rus), make


def _tiff_entradas(exif: bytes, ifd: int):
    """(posición absoluta en ``exif``, tag, tipo, count) de las entradas del IFD0 (ifd=0)
    o IFD1 (ifd=1) de un ``piexif.dump`` (big-endian, TIFF en el byte 6)."""
    t = exif[6:]
    assert t[:2] == b"MM"
    off = struct.unpack(">I", t[4:8])[0]
    for _ in range(ifd + 1):
        n = struct.unpack(">H", t[off:off + 2])[0]
        entradas = [(6 + off + 2 + 12 * i, *struct.unpack(">HHI", t[off + 2 + 12 * i:
                                                                   off + 2 + 12 * i + 8]))
                    for i in range(n)]
        sig = struct.unpack(">I", t[off + 2 + 12 * n:off + 6 + 12 * n])[0]
        if ifd == 0 or sig == 0:
            break
        off = sig
    return entradas


def _exif_con_miniatura() -> bytes:
    import piexif
    b = io.BytesIO()
    Image.new("RGB", (8, 8)).save(b, format="JPEG")
    return piexif.dump({"0th": {piexif.ImageIFD.Make: "DJI", piexif.ImageIFD.Model: "FC6310"},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: "2026:08:20 10:11:12"},
                        "1st": {piexif.ImageIFD.Compression: 6}, "thumbnail": b.getvalue()})


def test_exifread_exif_offset_recuento_cero_decide_python(tmp_path, monkeypatch, logger):
    """I2: ExifOffset (0x8769) con recuento 0 -> ExifRead lanza IndexError. Rust no da
    modelo/make (``None``): decide Python y el resultado es el mismo que sin Rust."""
    rs = _rs_real()
    gi = _gi(logger)
    exif = bytearray(_exif_bytes())
    pos = next(p for p, tag, _, _ in _tiff_entradas(bytes(exif), 0) if tag == 0x8769)
    exif[pos + 4:pos + 8] = struct.pack(">I", 0)
    buf = _jpeg_dji(bytes(exif), _xmp_atributos(_XMP_DJI))
    cab = rs.parsear_cabecera(buf)
    assert cab.modelo is None and cab.make is None
    assert "modelo" not in cab.ausentes and "make" not in cab.ausentes
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(buf)
    assert _diferencias(_leer(str(ruta), gi, monkeypatch, False),
                        _leer(str(ruta), gi, monkeypatch, True)) == []


@pytest.mark.parametrize("tag", [0x0201, 0x0202])
def test_exifread_miniatura_jpeg_tipo_no_entero_decide_python(tag, tmp_path, monkeypatch, logger):
    """I2: JPEGInterchangeFormat(Length) de IFD1 con tipo ASCII -> ExifRead lanza TypeError
    en ``extract_jpeg_thumbnail``. Rust devuelve ``None`` y decide Python."""
    rs = _rs_real()
    gi = _gi(logger)
    sano = _exif_con_miniatura()
    assert any(t == tag for _, t, _, _ in _tiff_entradas(sano, 1)), "fixture sin IFD1 JPEG"
    # Control: con la miniatura sana Rust sí da modelo/make.
    cab_sano = rs.parsear_cabecera(_jpeg_dji(sano, _xmp_atributos(_XMP_DJI)))
    assert (cab_sano.modelo, cab_sano.make) == ("FC6310", "DJI")
    exif = bytearray(sano)
    pos = next(p for p, t, _, _ in _tiff_entradas(sano, 1) if t == tag)
    exif[pos + 2:pos + 4] = struct.pack(">H", 2)  # ASCII
    buf = _jpeg_dji(bytes(exif), _xmp_atributos(_XMP_DJI))
    cab = rs.parsear_cabecera(buf)
    assert cab.modelo is None and cab.make is None
    ruta = tmp_path / "DJI_0001.JPG"
    ruta.write_bytes(buf)
    assert _diferencias(_leer(str(ruta), gi, monkeypatch, False),
                        _leer(str(ruta), gi, monkeypatch, True)) == []


def test_ausentes_no_cuentan_como_fallback(tmp_path, monkeypatch, logger):
    """I1: sin GPS, sin Model, sin fecha o sin XMP no es un fallo; un TIFF/PNG va por
    Python con ``rust_parse_no_soportado``."""
    _rs_real()
    gi = _gi(logger)
    contador = indice._ContadorRust()
    casos = [dict(con_gps=False), dict(model=None), dict(fecha=None), dict(make=None)]
    for i, kw in enumerate(casos):
        ruta = tmp_path / f"DJI_{i:04d}.JPG"
        ruta.write_bytes(_jpeg_dji(_exif_bytes(**kw), _xmp_atributos(_XMP_DJI)))
        assert _diferencias(_leer(str(ruta), gi, monkeypatch, False),
                            _leer(str(ruta), gi, monkeypatch, True, contador)) == [], kw
    sin_xmp = tmp_path / "DJI_0099.JPG"
    sin_xmp.write_bytes(_jpeg_dji(_exif_bytes(), None))
    assert _diferencias(_leer(str(sin_xmp), gi, monkeypatch, False),
                        _leer(str(sin_xmp), gi, monkeypatch, True, contador)) == []
    assert (contador.total, contador.corto, contador.no_soportado) == (0, 0, 0), \
        (dict(contador.por_campo), contador.ejemplos)
    for nombre, formato in (("a.png", "PNG"), ("a.tif", "TIFF")):
        f = tmp_path / nombre
        Image.new("RGB", (8, 8)).save(f, format=formato)
        assert _diferencias(_leer(str(f), gi, monkeypatch, False),
                            _leer(str(f), gi, monkeypatch, True, contador)) == []
    assert (contador.total, contador.no_soportado) == (0, 2)


def test_gps_y_fecha_variantes(tmp_path, monkeypatch, logger):
    _rs_real()
    gi = _gi(logger)
    casos = [dict(lat_ref="S", lon_ref="E"), dict(lat=(0, 0, 0, 1)), dict(con_gps=False),
             dict(fecha="2026:13:45 99:00:00"), dict(fecha=None), dict(model=None),
             dict(lat_ref="X")]
    for i, kw in enumerate(casos):
        ruta = tmp_path / f"DJI_{i:04d}.JPG"
        ruta.write_bytes(_jpeg_dji(_exif_bytes(**kw), _xmp_atributos(_XMP_DJI)))
        assert _diferencias(_leer(str(ruta), gi, monkeypatch, False),
                            _leer(str(ruta), gi, monkeypatch, True)) == [], kw


def test_contador_fallbacks_visible(tmp_path, monkeypatch, logger):
    _rs_real()
    gi = _gi(logger)
    buenas = []
    for i in range(3):
        r = tmp_path / f"DJI_{i:04d}.JPG"
        r.write_bytes(_jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI)))
        buenas.append(str(r))
    monkeypatch.setenv("ATOM_RUST_FASES", "indice")
    cb = _Cb()
    indice._leer_lote(buenas, gi, cb, indice._EmisorIndice(cb, _Cb()), 2, 30.0, 30.0, 60.0)
    assert "rust_parse_fallbacks=0" in "".join(cb.lineas)
    rota = tmp_path / "DJI_9999.JPG"
    rota.write_bytes(b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00II*\x00" + b"\x00" * 50)
    cb = _Cb()
    indice._leer_lote(buenas + [str(rota)], gi, cb, indice._EmisorIndice(cb, _Cb()),
                      2, 30.0, 30.0, 60.0)
    texto = "".join(cb.lineas)
    assert "rust_parse_fallbacks=0" not in texto
    assert "rust_parse_fallbacks=" in texto and "rust_parse_corto=" in texto
    assert "DJI_9999.JPG" in texto


def test_paridad_con_gs_uri_sin_cambios(monkeypatch, logger):
    _rs_real()
    gi = _gi(logger)
    buf = _jpeg_dji(_exif_bytes(), _xmp_atributos(_XMP_DJI))
    pedidos = []

    def _leer_cabecera(ruta, n):
        pedidos.append((ruta, n))
        return buf[:n]
    monkeypatch.setattr(almacen, "leer_cabecera", _leer_cabecera)
    ruta = "gs://bucket/PLANTA/DJI_0001.JPG"
    py = _leer(ruta, gi, monkeypatch, False)
    n_py = list(pedidos)
    rus = _leer(ruta, gi, monkeypatch, True)
    assert n_py == [(ruta, indice._BYTES_CABECERA)]
    assert pedidos == n_py * 2
    assert _diferencias(py, rus) == []


# ------------------------------------------------- corpus real (banco / CI con corpus)

_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".tif", ".tiff"}


def _corpus_sintetico(raiz: Path) -> Path:
    """Corpus generado (el repo no lleva imágenes reales): JPEG DJI con variantes de
    GPS/fecha/Model/XMP, un JPEG con el XMP más allá de 256 KiB, PNG y TIFF."""
    d = raiz / "corpus_sintetico"
    d.mkdir()
    xmp = _xmp_atributos(_XMP_DJI)
    variantes = [dict(), dict(lat_ref="S", lon_ref="E"), dict(con_gps=False), dict(model=None),
                 dict(fecha=None), dict(lat=(0, 0, 0, 1)), dict(make="Canon")]
    for i, kw in enumerate(variantes):
        (d / f"DJI_{i:04d}_W.JPG").write_bytes(_jpeg_dji(_exif_bytes(**kw), xmp))
    (d / "DJI_0100_W.JPG").write_bytes(_jpeg_dji(_exif_bytes(), _xmp_elementos(_XMP_DJI)))
    (d / "DJI_0101_W.JPG").write_bytes(_jpeg_dji(_exif_bytes(), None))
    for formato, ext in (("PNG", "png"), ("TIFF", "tif")):
        Image.new("RGB", (16, 16), (10, 20, 30)).save(d / f"IMG_0001.{ext}", format=formato)
    return d


@pytest.mark.rust_ab
def test_equivalencia_campo_a_campo_corpus(tmp_path, monkeypatch, logger):
    _rs_real()
    raiz = rust_corpus.corpus()
    if raiz is None and _REQUERIDO:
        raiz = _corpus_sintetico(tmp_path)  # CI: no hay imágenes en el repo
    if raiz is None:
        pytest.skip(f"sin corpus: define {rust_corpus.VARIABLE}=<dir>")
    gi = _gi(logger)
    rutas = sorted(str(p) for p in Path(raiz).rglob("*") if p.suffix.lower() in _EXTS)
    assert rutas, f"corpus vacío: {raiz}"
    contador = indice._ContadorRust()
    distintas = []
    for ruta in rutas:  # en secuencia, solo lectura
        d = _diferencias(_leer(ruta, gi, monkeypatch, False),
                         _leer(ruta, gi, monkeypatch, True, contador))
        if d:
            distintas.append(f"{ruta}: " + "; ".join(d))
    assert not distintas, f"{len(distintas)} de {len(rutas)} con diferencias:\n" + \
        "\n".join(distintas[:50])
    print(f"\n[rust_ab indice] {len(rutas)} ficheros, 0 diferencias, "
          f"rust_parse_fallbacks={contador.total} {dict(contador.por_campo)} "
          f"rust_parse_corto={contador.corto} "
          f"rust_parse_no_soportado={contador.no_soportado}")
    assert contador.corto >= 0 and contador.no_soportado >= 0
    assert contador.total == 0, (contador.total, dict(contador.por_campo), contador.ejemplos)
