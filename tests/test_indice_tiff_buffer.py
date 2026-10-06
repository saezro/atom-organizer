"""TIFF (térmicos): si IFD0 + sub-IFD EXIF/GPS caben en la cabecera de
`_BYTES_CABECERA`, `_leer_metadatos` NO relee el fichero entero para GPS
(antes `_exif_entero_en_buffer` devolvía False para todo TIFF > 256 KB y el
GPS hacía `open().read()` completo: caro con origen en Google Drive).
Si algún IFD/valor cae fuera del buffer, se lee entero como siempre."""
import builtins
import os
import struct

import numpy as np
import piexif
import pytest
from PIL import Image

from atom_core.indice import (_BYTES_CABECERA, _exif_entero_en_buffer,
                              _gps_desde_buffer, _leer_metadatos,
                              _modelo_desde_buffer)

GPS_ESPERADO = (40 + 26 / 60 + 4 / 3600, -(3 + 44 / 60 + 30 / 3600))


class _Callback:
    def emit(self, *a, **k):
        pass


@pytest.fixture
def exif_obj(organizer_logger_stub):
    import exif as exif_mod
    return exif_mod.GeneralInformationFromImage(organizer_logger_stub)


def _exif_dict():
    return {
        "0th": {piexif.ImageIFD.Make: b"DJI", piexif.ImageIFD.Model: b"M3T"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:01:02 03:04:05"},
        "GPS": {1: b"N", 2: ((40, 1), (26, 1), (4, 1)),
                3: b"W", 4: ((3, 1), (44, 1), (30, 1))},
    }


_XMP = ("<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf="
        "'http://www.w3.org/1999/02/22-rdf-syntax-ns#'><rdf:Description rdf:about=''"
        " xmlns:drone-dji='http://www.dji.com/drone-dji/1.0/'"
        " drone-dji:GimbalYawDegree='-45.5' drone-dji:GimbalPitchDegree='-88.0'/>"
        "</rdf:RDF></x:xmpmeta>")


def _crear_tiff(path, exif_dict=None, xmp_relleno=None):
    """`xmp_relleno=None` -> sin XMP; 0 -> XMP pequeño; N -> XMP precedido de
    N bytes de comentario (para empujar su fin más allá de la cabecera)."""
    px = (np.random.RandomState(0).rand(600, 600) * 255).astype("uint8")
    ex = Image.Exif()
    ex.load(piexif.dump(exif_dict or _exif_dict()))
    if xmp_relleno is not None:
        ex[700] = ("<!--" + "x" * xmp_relleno + "-->" + _XMP).encode("latin-1")
    kw = {"exif": ex}
    Image.fromarray(px).save(path, **kw)
    assert os.path.getsize(path) > _BYTES_CABECERA
    return path


def _mover_ifd_gps_al_final(src, dst):
    """Copia del TIFF con el GPS IFD movido más allá de la cabecera: se
    añade al final del fichero y se reapunta el tag 0x8825 de IFD0."""
    data = bytearray(open(src, "rb").read())
    orden = "<" if data[:2] == b"II" else ">"
    off0 = struct.unpack_from(orden + "I", data, 4)[0]
    n = struct.unpack_from(orden + "H", data, off0)[0]
    for i in range(n):
        e = off0 + 2 + 12 * i
        if struct.unpack_from(orden + "H", data, e)[0] == 0x8825:
            gps_off = struct.unpack_from(orden + "I", data, e + 8)[0]
            ng = struct.unpack_from(orden + "H", data, gps_off)[0]
            bloque_len = 2 + 12 * ng + 4
            copia = bytearray(data[gps_off:gps_off + bloque_len])
            # los valores >4 bytes del GPS siguen en su sitio (dentro de la
            # cabecera); solo la tabla de entradas se desplaza al final.
            nuevo = len(data) + (len(data) % 2)
            data += b"\x00" * (nuevo - len(data)) + copia
            struct.pack_into(orden + "I", data, e + 8, nuevo)
            break
    else:
        raise AssertionError("sin tag GPS")
    open(dst, "wb").write(bytes(data))
    return dst


def _espiar_open(monkeypatch, ruta):
    registro = {"aperturas": 0, "reads_completos": 0}
    open_real = builtins.open

    class _Fd:
        def __init__(self, fd):
            self._fd = fd

        def read(self, *a):
            if not a or a[0] is None or a[0] < 0 or a[0] > _BYTES_CABECERA:
                registro["reads_completos"] += 1
            return self._fd.read(*a)

        def __getattr__(self, k):
            return getattr(self._fd, k)

        def __enter__(self):
            self._fd.__enter__()
            return self

        def __exit__(self, *a):
            return self._fd.__exit__(*a)

    def open_espia(archivo, *a, **k):
        fd = open_real(archivo, *a, **k)
        if isinstance(archivo, (str, os.PathLike)) and os.fspath(archivo) == ruta:
            registro["aperturas"] += 1
            return _Fd(fd)
        return fd

    monkeypatch.setattr(builtins, "open", open_espia)
    return registro


def test_tiff_con_ifds_en_cabecera_usa_buffer(tmp_path):
    ruta = _crear_tiff(str(tmp_path / "t.tif"))
    buf = open(ruta, "rb").read(_BYTES_CABECERA)
    assert _exif_entero_en_buffer(buf) is True


def test_tiff_datos_iguales_buffer_vs_fichero_entero(tmp_path):
    ruta = _crear_tiff(str(tmp_path / "t.tif"))
    completo = open(ruta, "rb").read()
    parcial = completo[:_BYTES_CABECERA]
    assert _gps_desde_buffer(parcial) == pytest.approx(GPS_ESPERADO)
    assert _gps_desde_buffer(parcial) == _gps_desde_buffer(completo)
    assert _modelo_desde_buffer(parcial) == _modelo_desde_buffer(completo) == "M3T"


def test_tiff_leer_metadatos_sin_lectura_completa(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_tiff(str(tmp_path / "t.tif"))
    ref = _leer_metadatos(ruta, exif_obj, _Callback())  # referencia (sin espía)
    reg = _espiar_open(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert reg["reads_completos"] == 0, reg
    assert r.gps == pytest.approx(GPS_ESPERADO)
    assert (r.timestamp, r.modelo, r.make, r.gps) == (ref.timestamp, ref.modelo, ref.make, ref.gps)
    assert r.modelo == "M3T" and r.make == "DJI"


def test_tiff_gps_ifd_fuera_del_buffer_lee_completo(tmp_path, exif_obj, monkeypatch):
    base = _crear_tiff(str(tmp_path / "base.tif"))
    ruta = _mover_ifd_gps_al_final(base, str(tmp_path / "gps_fuera.tif"))
    # el GPS IFD ahora cae fuera de la cabecera
    buf = open(ruta, "rb").read(_BYTES_CABECERA)
    assert _exif_entero_en_buffer(buf) is False
    reg = _espiar_open(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert reg["reads_completos"] >= 1  # comportamiento actual: lectura completa
    assert r.gps == pytest.approx(GPS_ESPERADO)
    assert r.modelo == "M3T"


def test_tiff_corrupto_o_bigtiff_no_se_fia():
    rel = b"\x00" * _BYTES_CABECERA
    assert _exif_entero_en_buffer((b"II*\x00" + rel)[:_BYTES_CABECERA]) is False
    assert _exif_entero_en_buffer((b"II+\x00" + rel)[:_BYTES_CABECERA]) is False
    # IFD0 apuntando fuera del buffer
    assert _exif_entero_en_buffer((b"MM\x00*" + struct.pack(">I", 10**8) + rel)[:_BYTES_CABECERA]) is False


@pytest.mark.parametrize("relleno", [None, 0])
def test_tiff_xmp_ausente_o_dentro_sin_lectura_completa(tmp_path, exif_obj, monkeypatch, relleno):
    ruta = _crear_tiff(str(tmp_path / "t.tif"), xmp_relleno=relleno)
    reg = _espiar_open(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert reg["reads_completos"] == 0, reg
    assert r.gps == pytest.approx(GPS_ESPERADO)
    if relleno is not None:
        assert r.yaw == pytest.approx(-45.5)


def test_tiff_xmp_fuera_del_buffer_lee_completo(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_tiff(str(tmp_path / "t.tif"), xmp_relleno=_BYTES_CABECERA + 5000)
    buf = open(ruta, "rb").read(_BYTES_CABECERA)
    assert _exif_entero_en_buffer(buf) is False  # valor del 700 fuera -> no se fía
    reg = _espiar_open(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert reg["reads_completos"] >= 1
    assert r.yaw == pytest.approx(-45.5)
    assert r.gps == pytest.approx(GPS_ESPERADO)


@pytest.mark.parametrize("tag", [273, 279, 700])
def test_tiff_valor_de_ifd0_fuera_del_buffer_no_usa_buffer(tmp_path, tag):
    """PIL aborta en silencio (GPS `{}`/`None`, 279) o falla al abrir (273)
    si un valor de IFD0 cae fuera del buffer: nunca debe fiarse del recorte."""
    ruta = _crear_tiff(str(tmp_path / "t.tif"))
    data = bytearray(open(ruta, "rb").read())
    o = struct.unpack_from("<I", data, 4)[0]
    n = struct.unpack_from("<H", data, o)[0]
    tags = [struct.unpack_from("<H", data, o + 2 + 12 * i)[0] for i in range(n)]
    assert tag in tags or tag == 700
    if tag == 700:
        pytest.skip("700 cubierto por test_tiff_xmp_fuera_del_buffer_lee_completo")
    e = o + 2 + 12 * tags.index(tag)
    struct.pack_into("<HII", data, e + 2, 1, 1000, len(data) - 500)
    assert _exif_entero_en_buffer(bytes(data[:_BYTES_CABECERA])) is False
