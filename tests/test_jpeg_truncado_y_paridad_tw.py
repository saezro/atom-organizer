"""Falso «JPEG truncado» (DJI deja ~4097 B de relleno tras EOI) y paridad T<->W."""
import io

from PIL import Image

from atom_core import apply as apply_mod
from atom_core import pares
from atom_core import lectura_segura


def _jpeg(tmp_path, nombre, relleno=b""):
    import random
    rnd = random.Random(1)
    img = Image.new("RGB", (256, 256))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                 for _ in range(256 * 256)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    ruta = tmp_path / nombre
    ruta.write_bytes(buf.getvalue() + relleno)
    return ruta


def test_jpeg_valido_con_relleno_4097_no_es_truncado(tmp_path):
    ruta = _jpeg(tmp_path, "ok.JPG", b"\x00" * 4097)
    assert apply_mod._motivo_jpeg_truncado(str(ruta)) is None
    assert lectura_segura.jpeg_valido(ruta.read_bytes()) is True


def test_jpeg_cortado_a_la_mitad_es_truncado(tmp_path):
    ruta = _jpeg(tmp_path, "mitad.JPG")
    datos = ruta.read_bytes()
    ruta.write_bytes(datos[: len(datos) // 2])
    assert apply_mod._motivo_jpeg_truncado(str(ruta)) is not None
    assert lectura_segura.jpeg_valido(ruta.read_bytes()) is False


def test_jpeg_con_cola_de_ceros_es_truncado(tmp_path):
    ruta = _jpeg(tmp_path, "ceros.JPG")
    datos = ruta.read_bytes()
    ruta.write_bytes(datos[: len(datos) // 2] + b"\x00" * (len(datos) // 2))
    assert apply_mod._motivo_jpeg_truncado(str(ruta)) is not None
    assert lectura_segura.jpeg_valido(ruta.read_bytes()) is False


def test_paridad_tw_detecta_huerfanas():
    nombres = [
        "/o/DJI_20260901120000_0001_T.JPG", "/o/DJI_20260901120000_0001_W.JPG",
        "/o/DJI_20260901120000_0002_T.JPG",
        "/o/DJI_20260901120000_0003_W.JPG",
        "/o/DJI_20260901120000_0004_Z.JPG",
    ]
    t_sin_w, w_sin_t = pares.paridad_tw(nombres)
    assert t_sin_w == ["0002"]
    assert w_sin_t == ["0003"]


def test_paridad_tw_completa_no_avisa():
    nombres = ["DJI_20260901120000_0001_T.JPG", "DJI_20260901120000_0001_W.JPG"]
    assert pares.paridad_tw(nombres) == ([], [])


def test_jpeg_con_payload_mayor_de_64k_tras_eoi_es_valido(tmp_path):
    # R-JPEG DJI: datos tras el EOI > ventana de cola; el archivo está bien.
    ruta = _jpeg(tmp_path, "rjpeg.JPG", b"\x01\x02\x03\x04" * (40 * 1024))
    datos = ruta.read_bytes()
    assert b"\xff\xd9" not in datos[-lectura_segura.VENTANA_COLA_JPEG:]
    assert lectura_segura.jpeg_valido(datos) is True
    assert apply_mod._motivo_jpeg_truncado(str(ruta)) is None


def test_jpeg_con_payload_grande_truncado_a_mitad_del_scan_es_invalido(tmp_path):
    ruta = _jpeg(tmp_path, "rj_trunc.JPG", b"\x01\x02\x03\x04" * (40 * 1024))
    datos = ruta.read_bytes()
    corte = datos[: len(datos) // 8]
    assert lectura_segura.jpeg_valido(corte) is False
    assert lectura_segura.jpeg_valido(corte + b"\x00" * (200 * 1024)) is False
    ruta.write_bytes(corte)
    assert apply_mod._motivo_jpeg_truncado(str(ruta)) is not None


def test_eoi_de_miniatura_en_app_no_cuenta_como_eoi_del_scan():
    # FFD9 dentro de un segmento APP1 (miniatura EXIF) no debe validar un scan cortado.
    app1 = b"\xff\xe1" + (2 + 6).to_bytes(2, "big") + b"\xff\xd8\xff\xd9ab"[:6]
    cortado = b"\xff\xd8" + app1 + b"\xff\xda\x00\x04\x00\x00" + b"\x12\x34" * 100
    assert lectura_segura.jpeg_eoi_por_segmentos(cortado) is False
    assert lectura_segura.jpeg_eoi_por_segmentos(cortado + b"\xff\xd9") is True
