"""`_leer_metadatos` lee cada imagen UNA sola vez.

Antes abría el fichero hasta 4 veces por imagen (timestamp, modelo,
yaw/pitch, lat-lon-alt), caro en lotes servidos desde HDD. Ahora lee una
cabecera en memoria y de ahí saca timestamp/modelo/yaw con réplicas puras de
las funciones originales; solo cae a la función ORIGINAL (por ruta, con su
propia apertura) cuando la réplica rápida falla o no encuentra el dato.

El campo `gps` (`leerLatitudLongitudAltitud_exif_DJI`) queda FUERA del
atajo a propósito: esa función vive en `exif.MetaLocation`, no en
`exif.GeneralInformationFromImage` (el tipo real del objeto que
`atom_core/phases.py` pasa a `construir_indice`), así que hoy en producción
SIEMPRE lanza `AttributeError` y `gps` sale `None` en todas las imágenes.
Implementar una lectura real de EXIF ahí habría cambiado ese resultado
(dejaría de ser `None`), así que se deja intacta: una única llamada, igual
que antes. Ver `test_gps_siempre_none_porque_el_metodo_no_existe`.

Lo que se prueba aquí es que el atajo NO cambia NINGÚN resultado frente a
llamar a las funciones originales de `exif.GeneralInformationFromImage`
directamente, en los casos que importan:

- imagen completa (EXIF + XMP);
- sin bloque XMP;
- XMP con `\\r\\n` (CRLF), que es lo que fuerza la normalización de saltos de
  línea a que abre `exif.leer_bloque_xmp` en modo texto;
- fichero vacío/corrupto (debe caer al mismo camino de error que antes, sin
  reventar distinto);

y que en el caso normal el fichero se abre una sola vez.
"""
import builtins
import os

import piexif
import pytest
from PIL import Image

import exif as exif_mod
from atom_core.indice import _leer_metadatos, _MetadatosImagen


class _Callback:
    """Doble mínimo del signal Qt: el código bajo prueba solo llama `.emit`."""

    def __init__(self):
        self.mensajes = []

    def emit(self, *args, **kwargs):
        self.mensajes.append((args, kwargs))


def _xmp_block(yaw: str, pitch: str, saltos: str = "\n") -> bytes:
    """Bloque XMP DJI mínimo, con el separador de línea que se pida (para
    forzar el caso CRLF/CR que `leer_bloque_xmp` normaliza)."""
    xmp = (
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
        + saltos +
        "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        + saltos +
        "<rdf:Description rdf:about=''"
        " xmlns:drone-dji='http://www.dji.com/drone-dji/1.0/'"
        f" drone-dji:GimbalYawDegree='{yaw}'"
        f" drone-dji:GimbalPitchDegree='{pitch}'"
        "/>"
        + saltos +
        "</rdf:RDF></x:xmpmeta>"
    )
    return xmp.encode("latin-1")


def _crear_jpeg(path: str, *, con_exif=True, con_gps=True, con_model=True,
                con_timestamp=True, con_xmp=True, saltos_xmp="\n",
                relleno_xmp=0) -> str:
    """JPEG sintético con EXIF (piexif) + XMP en texto plano (como las
    fotos DJI reales): mismo patrón que `make_dji_jpeg` de conftest, pero
    con control fino de qué campos lleva, para poder aislar cada caso."""
    img = Image.new("RGB", (64, 48), color=(120, 130, 140))

    exif_bytes = None
    if con_exif:
        zeroth_ifd = {}
        exif_ifd = {}
        gps_ifd = {}
        if con_model:
            zeroth_ifd[piexif.ImageIFD.Model] = "M3T"
        if con_timestamp:
            exif_ifd[piexif.ExifIFD.DateTimeOriginal] = "2024:06:01 10:30:00"
        if con_gps:
            gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = "N"
            gps_ifd[piexif.GPSIFD.GPSLatitude] = ((40, 1), (25, 1), (10000, 100))
            gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = "W"
            gps_ifd[piexif.GPSIFD.GPSLongitude] = ((3, 1), (42, 1), (13700, 100))
            gps_ifd[piexif.GPSIFD.GPSAltitude] = (5000, 100)
        exif_dict = {"0th": zeroth_ifd, "Exif": exif_ifd, "GPS": gps_ifd}
        exif_bytes = piexif.dump(exif_dict)

    if exif_bytes:
        img.save(path, format="JPEG", exif=exif_bytes)
    else:
        img.save(path, format="JPEG")
    img.close()

    if con_xmp:
        bloque = _xmp_block("-45.5", "-88.0", saltos=saltos_xmp)
        with open(path, "ab") as fh:
            if relleno_xmp:
                # Bytes con \r y \n sueltos ANTES del XMP: si la normalización
                # de saltos de línea del atajo fuera distinta a la de
                # `leer_bloque_xmp`, esto desplazaría los índices del find().
                fh.write((b"\r\n\r" * (relleno_xmp // 4 + 1))[:relleno_xmp])
            fh.write(bloque)

    return path


@pytest.fixture
def exif_obj(organizer_logger_stub):
    return exif_mod.GeneralInformationFromImage(organizer_logger_stub)


def _leer_metadatos_original(ruta: str, exif_obj, callback) -> _MetadatosImagen:
    """Los mismos 4 campos, pero SIEMPRE por las 4 funciones originales
    (una apertura de fichero cada una) — el comportamiento de referencia
    contra el que se compara el atajo."""
    nombre = os.path.basename(ruta)
    try:
        timestamp = exif_obj.get_timestamp_from_image(ruta)
    except Exception:
        timestamp = None
    try:
        modelo = exif_obj.get_model(ruta, callback)
    except Exception:
        modelo = None
    try:
        yaw = float(exif_obj.get_gimbal_yaw_pitch(ruta)[0])
    except Exception:
        yaw = None
    try:
        gps = exif_obj.leerLatitudLongitudAltitud_exif_DJI(ruta, callback)
    except Exception:
        gps = None
    return _MetadatosImagen(ruta=ruta, nombre=nombre, timestamp=timestamp,
                            modelo=modelo, yaw=yaw, gps=gps)


def _comparar(ruta, exif_obj):
    rapido = _leer_metadatos(ruta, exif_obj, _Callback())
    original = _leer_metadatos_original(ruta, exif_obj, _Callback())
    assert rapido.ruta == original.ruta
    assert rapido.nombre == original.nombre
    assert rapido.timestamp == original.timestamp
    assert rapido.modelo == original.modelo
    assert rapido.yaw == original.yaw
    assert rapido.gps == original.gps
    return rapido


def test_imagen_completa_da_los_mismos_4_campos(tmp_path, exif_obj):
    ruta = _crear_jpeg(str(tmp_path / "completa.jpg"))
    rapido = _comparar(ruta, exif_obj)
    # Y de paso, que de verdad se leyó algo (no son todo `None` por casualidad).
    assert rapido.timestamp is not None
    assert rapido.modelo == "M3T"
    assert rapido.yaw == pytest.approx(-45.5)


def test_gps_siempre_none_porque_el_metodo_no_existe(tmp_path, exif_obj):
    """`GeneralInformationFromImage` -el tipo real de `exif_management_obj`
    que llega a `_leer_metadatos` en producción- no tiene
    `leerLatitudLongitudAltitud_exif_DJI` (vive en `exif.MetaLocation`). El
    atajo no debe "arreglar" eso: `gps` tiene que seguir saliendo `None`,
    igual que con las 4 funciones originales."""
    assert not hasattr(exif_obj, "leerLatitudLongitudAltitud_exif_DJI")
    ruta = _crear_jpeg(str(tmp_path / "completa.jpg"))
    rapido = _leer_metadatos(ruta, exif_obj, _Callback())
    assert rapido.gps is None


def test_imagen_sin_exif_dan_los_mismos_campos_none(tmp_path, exif_obj):
    ruta = _crear_jpeg(str(tmp_path / "sin_exif.jpg"), con_exif=False, con_xmp=False)
    rapido = _comparar(ruta, exif_obj)
    assert rapido.timestamp is None
    assert rapido.gps is None


def test_imagen_sin_xmp_no_inventa_yaw(tmp_path, exif_obj):
    ruta = _crear_jpeg(str(tmp_path / "sin_xmp.jpg"), con_xmp=False)
    rapido = _comparar(ruta, exif_obj)
    assert rapido.yaw == 0.0  # default de `get_gimbal_yaw_pitch` sin XMP


def test_xmp_con_crlf_dentro_de_la_cabecera(tmp_path, exif_obj):
    """El XMP viene con `\\r\\n` (como algunas cámaras) Y con basura `\\r`/`\\n`
    sueltos justo delante, dentro de la ventana de cabecera: si la
    normalización de saltos de línea del atajo divergiera un solo carácter
    de la de `leer_bloque_xmp`, el índice del `</x:xmpmeta` se movería y el
    yaw saldría distinto o "0"."""
    ruta = _crear_jpeg(str(tmp_path / "crlf.jpg"), saltos_xmp="\r\n", relleno_xmp=4000)
    rapido = _comparar(ruta, exif_obj)
    assert rapido.yaw == pytest.approx(-45.5)


def test_xmp_mas_alla_del_limite_de_cabecera_cae_al_fallback(tmp_path, exif_obj):
    """XMP empujado más allá de `_BYTES_CABECERA`: el atajo no lo encuentra
    en el buffer corto y tiene que caer a `leer_bloque_xmp(ruta)` (relectura
    completa), pero el resultado tiene que seguir siendo idéntico."""
    ruta = _crear_jpeg(str(tmp_path / "xmp_lejos.jpg"),
                       relleno_xmp=exif_mod._XMP_HEADER_BYTES + 4096)
    rapido = _comparar(ruta, exif_obj)
    assert rapido.yaw == pytest.approx(-45.5)


def test_fichero_vacio_cae_al_mismo_camino_de_error_que_antes(tmp_path, exif_obj):
    ruta = str(tmp_path / "vacio.jpg")
    with open(ruta, "wb") as fh:
        fh.write(b"")
    rapido = _comparar(ruta, exif_obj)
    assert rapido.timestamp is None
    assert rapido.modelo is None
    # Sin XMP, `get_gimbal_yaw_pitch` no inventa nada: devuelve el "0" de
    # siempre, no `None` (no hay excepción que atrapar en ese campo).
    assert rapido.yaw == 0.0
    assert rapido.gps is None


def test_fichero_corrupto_cae_al_mismo_camino_de_error_que_antes(tmp_path, exif_obj):
    ruta = str(tmp_path / "corrupto.jpg")
    with open(ruta, "wb") as fh:
        fh.write(b"esto no es un jpeg de verdad, solo basura binaria \x00\x01\x02" * 50)
    rapido = _comparar(ruta, exif_obj)
    assert rapido.timestamp is None
    assert rapido.modelo is None
    assert rapido.yaw == 0.0
    assert rapido.gps is None


def test_ruta_inexistente_no_revienta(tmp_path, exif_obj):
    ruta = str(tmp_path / "no_existe.jpg")
    rapido = _comparar(ruta, exif_obj)
    assert rapido.timestamp is None
    assert rapido.modelo is None
    assert rapido.gps is None


def test_caso_normal_abre_el_fichero_una_sola_vez(tmp_path, exif_obj, monkeypatch):
    """El objetivo del cambio: con todo el dato dentro de la cabecera, solo
    hace falta UNA apertura del fichero de imagen (antes eran 4)."""
    ruta = _crear_jpeg(str(tmp_path / "completa.jpg"))

    aperturas = []
    open_real = builtins.open

    def open_contador(archivo, *args, **kwargs):
        if isinstance(archivo, (str, os.PathLike)) and os.fspath(archivo) == ruta:
            aperturas.append(archivo)
        return open_real(archivo, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_contador)

    resultado = _leer_metadatos(ruta, exif_obj, _Callback())

    assert len(aperturas) == 1, f"se abrió {len(aperturas)} veces: {aperturas}"
    assert resultado.modelo == "M3T"
    assert resultado.timestamp is not None
    assert resultado.yaw == pytest.approx(-45.5)
    # `gps` no pasa por el atajo (ver docstring del módulo): sigue `None`.
    assert resultado.gps is None


def test_exif_entero_en_buffer_solo_si_app1_cabe():
    """Si el recorte deja el APP1 Exif a medias no se usa el buffer
    (un valor truncado no lanzaría excepción y no dispararía el fallback)."""
    from atom_core.indice import _BYTES_CABECERA, _exif_entero_en_buffer

    def jpeg(largo_app1):
        seg = b'Exif\x00\x00' + b'\x00' * (largo_app1 - 8)
        cab = b'\xff\xd8' + b'\xff\xe0\x00\x10' + b'\x00' * 14 + b'\xff\xe1' + largo_app1.to_bytes(2, 'big') + seg
        return (cab + b'\xff\xda' + b'\x00' * _BYTES_CABECERA)[:_BYTES_CABECERA]

    assert _exif_entero_en_buffer(jpeg(60000)) is True
    # fichero corto (leído entero): siempre vale
    assert _exif_entero_en_buffer(b'\xff\xd8corto') is True
    # TIFF recortado: no se fía
    assert _exif_entero_en_buffer((b'II*\x00' + b'\x00' * _BYTES_CABECERA)[:_BYTES_CABECERA]) is False
    # APP1 que acaba más allá del buffer (segmentos APP2 grandes delante)
    grande = b'\xff\xd8' + (b'\xff\xe2\xff\xff' + b'\x00' * 65533) * 4 + b'\xff\xe1\xff\xff' + b'Exif\x00\x00'
    assert _exif_entero_en_buffer((grande + b'\x00' * _BYTES_CABECERA)[:_BYTES_CABECERA]) is False
