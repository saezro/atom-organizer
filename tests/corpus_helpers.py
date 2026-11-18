"""Helpers de corpus sintético para tests/golden/generar.py (no son tests).

Extraídos de los antiguos tests/test_rust_{indice_equiv,rgb_ab,termica_ab}.py.
"""
from __future__ import annotations

import io
import os
import struct
from pathlib import Path

import numpy as np
import piexif
from PIL import Image

from atom_core import apply, indice

# --- índice / EXIF / XMP

def _xmp_atributos(valores: dict) -> bytes:
    attrs = "\n".join(f'   drone-dji:{k}="{v}"' for k, v in valores.items())
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/">\n <rdf:RDF xmlns:rdf="http://www.w3.org/'
            '1999/02/22-rdf-syntax-ns#">\n  <rdf:Description rdf:about="DJI Meta Data"\n'
            '   xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"\n'
            f'{attrs}>\n  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>').encode("latin-1")



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


# --- RGB

_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/'
        b'22-rdf-syntax-ns#"><rdf:Description xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"'
        b' drone-dji:RelativeAltitude="+48.30" drone-dji:GimbalYawDegree="-87.40"'
        b' drone-dji:GimbalPitchDegree="-90.00"/></rdf:RDF></x:xmpmeta>')



def _origen(ruta: Path, w: int = 640, h: int = 480, semilla: int = 1, orientacion: int = 1,
            xmp: bytes | None = _XMP, comentario: bytes | None = None) -> bytes:
    """JPEG RGB sintético con EXIF (Make/Model/fecha/GPS/Orientation) y XMP DJI."""
    a = np.random.RandomState(semilla).randint(0, 256, size=(h, w, 3), dtype=np.uint8)
    # Mezcla con un gradiente suave: ni ruido puro ni imagen trivial.
    a[..., 0] = (a[..., 0] // 4 + (np.arange(w, dtype=np.uint32)[None, :] * 192 // max(w - 1, 1))
                 ).astype(np.uint8)
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"DJI", piexif.ImageIFD.Model: b"M3T",
                piexif.ImageIFD.Orientation: orientacion},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:08:20 10:11:12"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: ((40, 1), (25, 1), (6048, 100)),
                piexif.GPSIFD.GPSLongitudeRef: b"W",
                piexif.GPSIFD.GPSLongitude: ((3, 1), (42, 1), (1368, 100))},
    })
    bruto = ruta.with_suffix(".bruto.jpg")
    Image.fromarray(a).save(bruto, quality=92, exif=exif, **({"comment": comentario} if comentario else {}))
    if xmp:
        apply.insertar_xmp_app1(str(bruto), str(ruta), xmp)
        bruto.unlink()
    else:
        bruto.replace(ruta)
    return ruta.read_bytes()


def _muta_bytes_extra(d: bytes) -> bytes:
    i = d.index(b"\xff\xc4")  # antes del primer DHT
    return d[:i] + b"\x00\x01\x02" + d[i:]


def _muta_hueco_de_ceros(d: bytes) -> bytes:
    i = d.index(b"\xff\xda") + 200
    return d[:i] + b"\x00" * 300 + d[i + 300:]


def _muta_scan_corrupto(d: bytes) -> bytes:
    i = d.index(b"\xff\xda") + 400
    return d[:i] + bytes((b ^ 0x5A) for b in d[i:i + 64]) + d[i + 64:]


def _muta_truncado_con_eoi(d: bytes) -> bytes:
    return d[:len(d) * 6 // 10] + b"\xff\xd9"


def _muta_marcador_invalido(d: bytes) -> bytes:
    i = d.index(b"\xff\xda") + 100
    return d[:i] + b"\xff\x01" + d[i:]



# --- térmica

_IMG = "DJI_0001_T.JPG"
_W, _H = 64, 48


def _raw_sintetico(w: int = _W, h: int = _H, semilla: int | None = None) -> bytes:
    """float32 LE deterministas en ~15-55 °C (cruza los umbrales 20/40 y 0/70)."""
    if semilla is None:
        i = np.arange(w * h, dtype=np.int64)
        v = 15.0 + ((i * 37) % 4000) / 100.0
    else:
        v = 15.0 + np.random.RandomState(semilla).rand(w * h) * 40.0
    return v.astype("<f4").tobytes()


def _jpg_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(128, 64, 32)).save(buf, format="JPEG")
    return buf.getvalue()


def _carpeta(tmp_path: Path, nombre: str, jpg: bytes, image_name: str = _IMG) -> Path:
    raiz = tmp_path / nombre
    (raiz / "Escala_de_grises").mkdir(parents=True)
    (raiz / "Color_gradiente").mkdir()
    (raiz / image_name).write_bytes(jpg)
    return raiz


def _salidas(raiz: Path, image_name: str = _IMG) -> dict[str, Path]:
    return {
        "tiff": raiz / (os.path.splitext(image_name)[0] + ".tiff"),
        "gris": raiz / "Escala_de_grises" / image_name,
        "color": raiz / "Color_gradiente" / image_name,
    }
