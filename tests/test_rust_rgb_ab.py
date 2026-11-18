"""Fase Rust ``rgb`` (T3): recorte + giro + encode en ``atom_rs.rgb_procesar`` (modo a).

Dos bloques:

1. Tests LOCALES (sin Rust): un ``atom_rs`` FALSO en ``sys.modules`` (que implementa
   ``rgb_procesar`` con Pillow) sujeta la integración de ``apply._escribir_salidas_de_fila``:
   flag off no toca ``atom_rs``; flag on delega, inserta XMP, verifica y publica igual que
   el camino Python; un error no deja ``.parcial``; XMP > 64 KiB y JPEG truncado lanzan
   lo mismo que sin flag.

2. Tests con ``atom_rs`` REAL (CI ``rust-wheels.yml`` job ``paridad-jpeg``, con
   ``ATOM_RS_REQUIRED=1``: sin wheel FALLAN en vez de saltar): modo a bit-idéntico a
   Pillow (misma libjpeg-turbo: sonda P), EXIF/XMP finales, dimensiones no múltiplo de
   MCU, orientación EXIF, cancelación sin huérfanos y pool roto. Con ``-m rust_ab`` +
   ``ATOM_RUST_CORPUS`` también sobre RGB reales (solo lectura, en secuencia, sin descargas).

El modo b (sin pérdida) está aplazado: solo su guardarraíl vive en ``test_rust_bridge.py``.

     python -m pytest tests/test_rust_rgb_ab.py -q
     ATOM_RUST_CORPUS=<dir> python -m pytest tests/test_rust_rgb_ab.py -m rust_ab -q
"""
from __future__ import annotations

import hashlib
import importlib
import io
import os
import sys
import threading
import types
from pathlib import Path

import numpy as np
import piexif
import pytest
from PIL import Image, features

import pipeline
from atom_core import apply, rust_bridge
from atom_core.rust_bridge import RustNoDisponible
from tests import rust_corpus
from tests.test_apply_rgb import _cfg, _fila_dict

_REQUERIDO = os.environ.get("ATOM_RS_REQUIRED") == "1"

_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/'
        b'22-rdf-syntax-ns#"><rdf:Description xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"'
        b' drone-dji:RelativeAltitude="+48.30" drone-dji:GimbalYawDegree="-87.40"'
        b' drone-dji:GimbalPitchDegree="-90.00"/></rdf:RDF></x:xmpmeta>')


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    for v in ("ATOM_RUST", "ATOM_RUST_FASES", "ATOM_RUST_RGB_MODO", "ATOM_RUST_RGB_B_APROBADO"):
        monkeypatch.delenv(v, raising=False)


# ---------------------------------------------------------------- utilidades

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


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


def _exif_bytes(datos: bytes) -> bytes:
    with Image.open(io.BytesIO(datos)) as im:
        return im.getexif().tobytes()


def _ref_pil(datos: bytes, angulo: int, caja, q_orig: int, q_crop: int, dir_: Path):
    """Lo que hace hoy `_escribir_salidas_de_fila` hasta antes del XMP:
    `_procesar_y_guardar_imagen` para el `_CROP` y para el original."""
    transpose = apply._transpose_para_angulo(angulo, pipeline)
    img = Image.open(io.BytesIO(datos))
    img.load()
    salidas = {}
    if caja is not False:  # False = sin _CROP; None = _CROP sin recorte
        ruta = str(dir_ / "ref_crop.jpg")
        pipeline._procesar_y_guardar_imagen(
            img.copy() if caja is None else img,
            pipeline.ImageProcessConfig(output_path=ruta, quality=q_crop,
                                        crop_box=caja, rotate_degrees=transpose))
        salidas["crop"] = Path(ruta).read_bytes()
    base = img.transpose(transpose) if transpose is not None else img
    ruta = str(dir_ / "ref_orig.jpg")
    pipeline._procesar_y_guardar_imagen(
        base, pipeline.ImageProcessConfig(output_path=ruta, quality=q_orig))
    salidas["orig"] = Path(ruta).read_bytes()
    img.close()
    return salidas


def _rust_modo_a(rs, datos, angulo, caja, q_orig, q_crop, dir_: Path):
    crop = None if caja is False else str(dir_ / "rs_crop.parcial.jpg")
    orig = str(dir_ / "rs_orig.parcial.jpg")
    with Image.open(io.BytesIO(datos)) as im:
        comentario = im.info.get("comment")
    stats = rs.rgb_procesar(datos, _exif_bytes(datos), None if caja is False else caja,
                            angulo, q_orig, q_crop, orig, crop, "a", comentario)
    out = {"orig": Path(orig).read_bytes()}
    if crop:
        out["crop"] = Path(crop).read_bytes()
    return out, stats


def _caja(w, h, pct=0.7):
    return apply._caja_recorte_termico(w, h, pct)


# ---------------------------------------------------- módulo falso (con Pillow)

def _modulo_falso(llamadas: list, fallar: bool = False):
    """`atom_rs` falso: `rgb_procesar` con Pillow, mismas rutas y semántica que Rust."""
    m = types.ModuleType("atom_rs")
    m.version_abi = lambda: rust_bridge.ABI_ESPERADA
    m.version = lambda: "9.9.9"

    def rgb_procesar(datos, exif, caja, angulo, q_orig, q_crop, salida_orig, salida_crop, modo,
                    comentario=None, xmp=None):
        llamadas.append(dict(xmp=xmp, caja=caja, angulo=angulo, q_orig=q_orig, q_crop=q_crop,
                             salida_orig=salida_orig, salida_crop=salida_crop, modo=modo, comentario=comentario))
        transpose = apply._transpose_para_angulo(angulo, pipeline)
        img = Image.open(io.BytesIO(datos))
        img.load()
        if salida_crop:
            c = img.crop(caja) if caja else img.copy()
            if transpose is not None:
                c = c.transpose(transpose)
            c.save(salida_crop, quality=q_crop, exif=exif, comment=comentario)
        if fallar:
            raise OSError("fallo simulado a mitad de rgb_procesar")
        o = img.transpose(transpose) if transpose is not None else img
        o.save(salida_orig, quality=q_orig, exif=exif, comment=comentario)
        if xmp:  # Rust inserta el APP1 XMP dentro de la propia llamada
            for ruta in (salida_crop, salida_orig):
                if ruta:
                    apply.insertar_xmp_app1(ruta, ruta + ".x", xmp)
                    os.replace(ruta + ".x", ruta)
        return types.SimpleNamespace(
            ms_decode=1.0, ms_encode_orig=2.0, ms_encode_crop=3.0, motivo=None,
            ancho_orig=o.size[0], alto_orig=o.size[1],
            ancho_crop=c.size[0] if salida_crop else 0, alto_crop=c.size[1] if salida_crop else 0)

    for n in rust_bridge.SIMBOLOS_POR_FASE.values():
        for s in n:
            setattr(m, s, lambda *a, **k: None)
    m.rgb_procesar = rgb_procesar
    return m


def _fila(tmp: Path, origen: Path, nombre: str, angulo: int, con_crop: bool):
    salida = tmp / nombre
    return _fila_dict(str(origen), str(salida / "orig.JPG"),
                      str(salida / "crop_CROP.JPG") if con_crop else None,
                      angulo_giro=angulo, pct_recorte=0.7 if con_crop else None)


def _ficheros(carpeta: Path) -> dict[str, str]:
    return {p.name: _sha(p.read_bytes()) for p in sorted(carpeta.rglob("*")) if p.is_file()}


def test_flag_off_no_toca_atom_rs(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    origen = tmp_path / "o.JPG"
    _origen(origen)
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "s", 90, True), _cfg(), pipeline)
    assert sorted(_ficheros(tmp_path / "s")) == ["crop_CROP.JPG", "orig.JPG"]


@pytest.mark.parametrize("angulo", [0, 90, 270])
@pytest.mark.parametrize("con_crop", [True, False])
def test_integracion_igual_a_python(tmp_path, monkeypatch, angulo, con_crop):
    """Con el módulo falso (Pillow dentro) el camino de integración produce los MISMOS
    bytes finales (XMP incluido) que el camino Python: lo que cambia es solo quién codifica."""
    origen = tmp_path / "o.JPG"
    _origen(origen)
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "py", angulo, con_crop), _cfg(), pipeline)
    llamadas: list = []
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso(llamadas))
    cadena = apply._escribir_salidas_de_fila(
        _fila(tmp_path, origen, "rs", angulo, con_crop), _cfg(), pipeline)
    assert len(llamadas) == 1 and llamadas[0]["modo"] == "a" and llamadas[0]["angulo"] == angulo
    assert (llamadas[0]["caja"] is not None) is con_crop
    assert llamadas[0]["q_orig"] == (40 if angulo else 85)
    assert llamadas[0]["q_crop"] == (40 if angulo else 75)
    assert _ficheros(tmp_path / "rs") == _ficheros(tmp_path / "py")
    assert all(not apply.es_nombre_parcial(n) for n in _ficheros(tmp_path / "rs"))
    # formato "ruta:bytes; ruta:bytes": las rutas (p. ej. `C:\\` en Windows) pueden llevar ":".
    partes = cadena.split("; ")
    assert len(partes) == (2 if con_crop else 1)
    assert all(p.rsplit(":", 1)[1].isdigit() for p in partes)
    for p in (tmp_path / "rs").iterdir():  # XMP presente en la salida
        assert apply._bloque_xmp_de_bytes(p.read_bytes()) == _XMP


def test_error_en_rust_no_deja_parciales(tmp_path, monkeypatch):
    origen = tmp_path / "o.JPG"
    _origen(origen)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso([], fallar=True))
    with pytest.raises(OSError, match="fallo simulado"):
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "s", 90, True), _cfg(), pipeline)
    assert _ficheros(tmp_path / "s") == {}  # ni parciales ni destinos


def test_error_al_publicar_no_deja_parciales(tmp_path, monkeypatch):
    origen = tmp_path / "o.JPG"
    _origen(origen)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso([]))
    reales = apply._publicar_parcial
    n = {"v": 0}

    def _publica_y_cancela(parcial, destino):
        n["v"] += 1
        if n["v"] == 2:  # el original falla tras publicar el _CROP
            raise KeyboardInterrupt("cancelado")
        reales(parcial, destino)

    monkeypatch.setattr(apply, "_publicar_parcial", _publica_y_cancela)
    with pytest.raises(KeyboardInterrupt):
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "s", 0, True), _cfg(), pipeline)
    assert sorted(_ficheros(tmp_path / "s")) == ["crop_CROP.JPG"]
    assert apply.limpiar_parciales_huerfanos(str(tmp_path / "s")) == 0


def test_xmp_mayor_64k_lanza_igual(tmp_path, monkeypatch):
    origen = tmp_path / "o.JPG"
    grande = b"<x:xmpmeta>" + b"a" * 70000 + b"</x:xmpmeta>"
    _origen(origen, xmp=None)
    origen.write_bytes(origen.read_bytes() + grande)  # tras EOI: lo que ve `_bloque_xmp_de_bytes`
    with pytest.raises(apply.XmpDemasiadoGrandeError) as py:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "py", 0, True), _cfg(), pipeline)
    llamadas: list = []
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso(llamadas))
    with pytest.raises(apply.XmpDemasiadoGrandeError) as rs:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", 0, True), _cfg(), pipeline)
    assert str(rs.value) == str(py.value)
    assert llamadas == []  # falla antes de decodificar nada
    assert _ficheros(tmp_path / "rs") == {}


def test_jpeg_truncado_en_origen_lanza(tmp_path, monkeypatch):
    origen = tmp_path / "o.JPG"
    datos = _origen(origen, w=320, h=240)
    origen.write_bytes(datos[:len(datos) * 6 // 10] + b"\x00" * 8192)
    with pytest.raises(OSError, match="truncado") as py:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "py", 90, True), _cfg(), pipeline)
    llamadas: list = []
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso(llamadas))
    with pytest.raises(OSError, match="truncado") as rs:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", 90, True), _cfg(), pipeline)
    assert str(origen) in str(rs.value) and str(origen) in str(py.value)
    assert llamadas == []
    assert _ficheros(tmp_path / "rs") == {}


class PanicException(BaseException):
    """Mismo nombre que `pyo3_runtime.PanicException` (hereda de BaseException)."""


def _modulo_con(comportamiento):
    m = _modulo_falso([])
    llam: list = []

    def rgb_procesar(*a, **k):
        llam.append(a)
        return comportamiento()

    m.rgb_procesar = rgb_procesar
    m.llamadas = llam
    return m


def _lote_con_fallback(tmp_path, monkeypatch, modulo, origen: Path, angulo=90, con_crop=True):
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", modulo)
    return apply._escribir_salidas_de_fila(
        _fila(tmp_path, origen, "rs", angulo, con_crop), _cfg(), pipeline)


def test_panic_de_rust_es_error_de_fila_visible(tmp_path, monkeypatch):
    """Sin retroceso a Pillow: un panic de Rust es `PanicRust` de ESA fila y no deja parciales."""
    origen = tmp_path / "o.JPG"
    _origen(origen)

    def _panico():
        raise PanicException("boom")

    with pytest.raises(rust_bridge.PanicRust, match="panic de Rust: boom"):
        _lote_con_fallback(tmp_path, monkeypatch, _modulo_con(_panico), origen)
    assert not list((tmp_path / "rs").rglob("*.parcial*"))


def test_decode_con_warnings_lo_procesa_rust_tolerante(tmp_path, monkeypatch):
    """Antes un decode no limpio iba a Pillow; ahora Rust es tolerante y procesa (motivo None)."""
    origen = tmp_path / "o.JPG"
    _origen(origen)
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "py", 270, True), _cfg(), pipeline)
    llam: list = []
    r = _lote_con_fallback(tmp_path, monkeypatch, _modulo_falso(llam), origen, angulo=270)
    assert len(llam) == 1
    assert _ficheros(tmp_path / "rs") == _ficheros(tmp_path / "py")


def test_caja_fuera_de_la_imagen_llama_a_rust(tmp_path, monkeypatch):
    """pct 0.95 en 4:3 da top negativo: Rust rellena como `Image.crop` (ya no decide Pillow)."""
    origen = tmp_path / "o.JPG"
    _origen(origen, 640, 480)
    assert apply._caja_recorte_termico(640, 480, 0.95)[1] < 0
    fila = _fila_dict(str(origen), str(tmp_path / "rs" / "o.JPG"), str(tmp_path / "rs" / "c.JPG"),
                      angulo_giro=0, pct_recorte=0.95)
    pyfila = dict(fila, ruta_salida_original=str(tmp_path / "py" / "o.JPG"),
                  ruta_salida_crop=str(tmp_path / "py" / "c.JPG"))
    apply._escribir_salidas_de_fila(pyfila, _cfg(), pipeline)
    llam: list = []
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso(llam))
    apply._escribir_salidas_de_fila(fila, _cfg(), pipeline)
    assert len(llam) == 1 and llam[0]["caja"][1] < 0
    assert _ficheros(tmp_path / "rs") == _ficheros(tmp_path / "py")


@pytest.mark.parametrize("modo", ["L", "CMYK"])
def test_origen_no_rgb_lo_procesa_rust(tmp_path, monkeypatch, modo):
    origen = tmp_path / "o.JPG"
    Image.new(modo, (64, 48), 90).save(origen, quality=90)
    llam: list = []
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso(llam))
    # El falso (Pillow) conserva L/CMYK; la verificación de modo de salida no es lo que se prueba aquí.
    monkeypatch.setattr(apply, "verificar_salida_rust", lambda *a, **k: None)
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", 90, False), _cfg(), pipeline)
    assert len(llam) == 1
    assert (tmp_path / "rs" / "orig.JPG").exists()


@pytest.mark.parametrize("modo", ["RGBA", "P"])
def test_origen_rgba_o_paleta_es_error_de_fila_con_texto_pillow(tmp_path, monkeypatch, modo):
    """Modo que Pillow tampoco escribe como JPEG: error de fila (texto Pillow), sin parciales."""
    origen = tmp_path / "o.png"
    Image.new(modo, (64, 48)).save(origen, format="PNG")
    llam: list = []
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso(llam))
    with pytest.raises(OSError, match="cannot write mode"):
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", 90, False), _cfg(), pipeline)
    assert not list((tmp_path / "rs").rglob("*.parcial*"))


def test_resumen_rgb_sin_fallbacks(tmp_path, monkeypatch, make_dji_jpeg):
    """Sin retroceso a Pillow no hay contador de fallbacks ni AVISO en el log."""
    from tests.test_apply_rgb import _ControladorFalso, _SignalFalsa, _fila_manifiesto
    from atom_core.manifiesto import Manifiesto
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    filas = []
    for i in range(3):
        o = tmp_path / "origen" / f"DJI_{i:04d}.JPG"
        o.parent.mkdir(exist_ok=True)
        make_dji_jpeg(str(o))
        filas.append(_fila_manifiesto(o, tmp_path / "salida" / o.name,
                                      tmp_path / "salida" / f"c{i}.JPG", pct_recorte=1.0))
    m.insertar_muchas(filas)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    monkeypatch.setitem(sys.modules, "atom_rs", _modulo_falso([]))
    msgs = _SignalFalsa()
    res = apply.aplicar_rgb(m, _cfg(), pipeline, msgs, _SignalFalsa(), _SignalFalsa())
    assert res == {"hecho": 3, "fallido": 0}
    texto = "".join(str(x) for x in msgs.mensajes)
    assert "rust_rgb_fallbacks" not in texto and "AVISO RGB · Rust" not in texto


def test_flag_rgb_sin_simbolo_lanza_al_arrancar(monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    m = _modulo_falso([])
    del m.rgb_procesar
    monkeypatch.setitem(sys.modules, "atom_rs", m)
    with pytest.raises(RustNoDisponible, match="rgb_procesar"):
        rust_bridge.modulo()


# ------------------------------------------------------------- atom_rs REAL

def _rs_real():
    """``atom_rs`` real con ``rgb_procesar``. Sin él, skip en local; con
    ``ATOM_RS_REQUIRED=1`` (CI) es un FALLO."""
    def _no(motivo: str):
        if _REQUERIDO:
            pytest.fail(f"ATOM_RS_REQUIRED=1 y {motivo}")
        pytest.skip(motivo)

    try:
        m = importlib.import_module("atom_rs")
    except Exception as e:  # noqa: BLE001
        _no(f"atom_rs no disponible ({type(e).__name__}): la verificación queda para CI")
    if not hasattr(m, "rgb_procesar"):
        _no("atom_rs cargado no expone rgb_procesar (wheel anterior a T3)")
    if m.version_abi() != rust_bridge.ABI_ESPERADA:
        _no(f"atom_rs ABI {m.version_abi()} != {rust_bridge.ABI_ESPERADA}")
    return m


def _comparar(rs, a: dict, b: dict, que: str):
    for k in a:
        if a[k] != b[k]:
            ctx = (f"{que} [{k}]: bytes distintos. Pillow libjpeg_turbo="
                   f"{features.version('libjpeg_turbo')} atom_rs={rs.libjpeg_turbo_version()}")
            if features.version("libjpeg_turbo") != rs.libjpeg_turbo_version():
                ctx += " -> versión libjpeg distinta: decisión de Rodrigo"
            pytest.fail(ctx)


@pytest.mark.parametrize("angulo", [0, 90, 270])
@pytest.mark.parametrize("con_crop", [True, False])
def test_modo_a_sha256_igual_a_pil(tmp_path, angulo, con_crop):
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 640, 480)
    caja = _caja(640, 480) if con_crop else False
    q_orig = pipeline._ROTATION_JPEG_QUALITY if angulo else 85
    q_crop = pipeline._ROTATION_JPEG_QUALITY if angulo else apply._CROP_JPEG_QUALITY
    ref = _ref_pil(datos, angulo, caja, q_orig, q_crop, tmp_path)
    got, st = _rust_modo_a(rs, datos, angulo, caja, q_orig, q_crop, tmp_path)
    _comparar(rs, ref, got, f"angulo={angulo} crop={con_crop}")
    assert (st.ancho_orig, st.alto_orig) == ((480, 640) if angulo else (640, 480))
    assert st.ms_decode >= 0 and st.ms_encode_orig > 0


def test_crop_sin_recorte_copia_integra(tmp_path):
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 320, 240)
    ref = _ref_pil(datos, 90, None, 40, 40, tmp_path)
    got, _ = _rust_modo_a(rs, datos, 90, None, 40, 40, tmp_path)  # caja None = _CROP sin recorte
    _comparar(rs, ref, got, "crop sin recorte")


@pytest.mark.parametrize("angulo", [0, 90, 270])
@pytest.mark.parametrize("con_crop", [True, False])
def test_modo_a_xmp_y_exif_finales_identicos(tmp_path, monkeypatch, angulo, con_crop):
    """Tras XMP + `verificar_parcial` + publicación: sha256 final == camino Python."""
    _rs_real()
    origen = tmp_path / "o.JPG"
    _origen(origen)
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "py", angulo, con_crop), _cfg(), pipeline)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", angulo, con_crop), _cfg(), pipeline)
    assert _ficheros(tmp_path / "rs") == _ficheros(tmp_path / "py")
    for p in (tmp_path / "rs").iterdir():
        datos = p.read_bytes()
        assert apply._bloque_xmp_de_bytes(datos) == _XMP
        apply.verificar_parcial(str(p))


def test_xmp_mayor_64k_lanza_igual_real(tmp_path, monkeypatch):
    _rs_real()
    origen = tmp_path / "o.JPG"
    _origen(origen, xmp=None)
    origen.write_bytes(origen.read_bytes() + b"<x:xmpmeta>" + b"a" * 70000 + b"</x:xmpmeta>")
    with pytest.raises(apply.XmpDemasiadoGrandeError) as py:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "py", 90, True), _cfg(), pipeline)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    with pytest.raises(apply.XmpDemasiadoGrandeError) as rs:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", 90, True), _cfg(), pipeline)
    assert str(rs.value) == str(py.value)
    assert _ficheros(tmp_path / "rs") == {}


def test_jpeg_truncado_en_origen_lanza_real(tmp_path, monkeypatch):
    _rs_real()
    origen = tmp_path / "o.JPG"
    datos = _origen(origen, w=320, h=240)
    origen.write_bytes(datos[:len(datos) * 6 // 10] + b"\x00" * 8192)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    with pytest.raises(OSError, match="truncado") as ei:
        apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "rs", 90, True), _cfg(), pipeline)
    assert str(origen) in str(ei.value)
    assert _ficheros(tmp_path / "rs") == {}  # sin .parcial


@pytest.mark.parametrize("w,h", [(4000, 3000), (4001, 3001)])
def test_dimensiones_no_multiplo_mcu(tmp_path, w, h):
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", w, h, semilla=7)
    caja = _caja(w, h)
    for angulo in (0, 90, 270):
        q = pipeline._ROTATION_JPEG_QUALITY if angulo else 85
        ref = _ref_pil(datos, angulo, caja, q, q, tmp_path)
        got, _ = _rust_modo_a(rs, datos, angulo, caja, q, q, tmp_path)
        _comparar(rs, ref, got, f"{w}x{h} angulo={angulo}")


@pytest.mark.parametrize("orientacion", [1, 6, 8])
def test_orientation_exif_conservada(tmp_path, orientacion):
    """Pillow no toca Orientation al girar: el EXIF de salida lleva la del origen."""
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 320, 240, orientacion=orientacion)
    got, _ = _rust_modo_a(rs, datos, 90, _caja(320, 240), 40, 40, tmp_path)
    for k, v in got.items():
        with Image.open(io.BytesIO(v)) as im:
            assert im.getexif().get(0x0112) == orientacion, k
            assert im.getexif().tobytes() == _exif_bytes(datos), k


def test_errores_de_entrada_son_visibles(tmp_path):
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 64, 48)
    o = str(tmp_path / "o.parcial.jpg")
    with pytest.raises(ValueError):  # ángulo inválido
        rs.rgb_procesar(datos, None, None, 45, 40, 40, o, None, "a")
    # Caja fuera de la imagen: ya NO es error (F2 rellena en negro como Pillow `crop`).
    o_fuera, c_fuera = str(tmp_path / "fuera.parcial.jpg"), str(tmp_path / "fuera_c.jpg")
    rs.rgb_procesar(datos, None, (0, 0, 100, 100), 0, 40, 40, o_fuera, c_fuera, "a")
    assert os.path.exists(o_fuera) and os.path.exists(c_fuera)
    with pytest.raises(ValueError):  # modo b aplazado
        rs.rgb_procesar(datos, None, None, 0, 40, 40, o, None, "b")
    with pytest.raises(ValueError, match="EXIF data is too long"):
        rs.rgb_procesar(datos, b"E" * 70000, None, 0, 40, 40, o, None, "a")
    st = rs.rgb_procesar(b"no soy un jpeg", None, None, 0, 40, 40, o, None, "a")
    assert st.motivo  # decode no limpio: no escribe, decide Pillow
    assert not os.path.exists(o) and not (tmp_path / "c.jpg").exists()


def test_cancelacion_sin_huerfanos(tmp_path, monkeypatch):
    """Cancelación a mitad de lote con la fase activa (la señal sube como excepción
    desde el publicado): ni `.parcial` huérfanos ni hilos de más."""
    _rs_real()
    origen = tmp_path / "o.JPG"
    _origen(origen, 320, 240)
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    reales = apply._publicar_parcial
    n = {"v": 0}

    def _cancela_en_la_5(parcial, destino):
        n["v"] += 1
        if n["v"] == 5:
            raise KeyboardInterrupt("cancelado")
        reales(parcial, destino)

    monkeypatch.setattr(apply, "_publicar_parcial", _cancela_en_la_5)
    apply._escribir_salidas_de_fila(_fila(tmp_path, origen, "f0", 90, True), _cfg(), pipeline)
    hilos_antes = threading.active_count()
    with pytest.raises(KeyboardInterrupt):
        for i in range(1, 6):
            apply._escribir_salidas_de_fila(_fila(tmp_path, origen, f"f{i}", 90, True), _cfg(), pipeline)
    assert apply.limpiar_parciales_huerfanos(str(tmp_path)) == 0
    # rayon mantiene su pool global (hilos del proceso, no de `threading`): estable.
    assert threading.active_count() <= hilos_antes + 1


@pytest.mark.skipif(sys.platform == "win32", reason="usa fork para que el worker sea importable")
@pytest.mark.parametrize("workers", [1, 3])
def test_pool_roto_con_rust(tmp_path, monkeypatch, make_dji_jpeg, workers):
    """Mismo patrón que `test_apply_rgb_pool_roto`, con el trabajo REAL (fase rgb)
    en vez del doble: mismo nº de filas hechas/falladas y mismas recreaciones."""
    _rs_real()
    from tests.test_apply_rgb import _ControladorFalso, _SignalFalsa
    from tests.test_apply_rgb_pool_roto import VENENO, _estados, _montar
    from atom_core.manifiesto import Manifiesto  # noqa: F401

    monkeypatch.setenv("ATOM_MP_START", "fork")
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")

    from tests._trabajo_veneno import trabajo  # top-level: picklable por referencia

    monkeypatch.setattr(apply, "_trabajo_fila", trabajo)
    nombres = [f"DJI_{i:04d}.JPG" for i in range(8)] + [f"DJI_{VENENO}.JPG"]
    manifiesto = _montar(tmp_path, nombres, make_dji_jpeg)
    rondas = []
    original = apply._kwargs_pool_rgb
    monkeypatch.setattr(apply, "_kwargs_pool_rgb", lambda w: rondas.append(w) or original(w))
    msgs = _SignalFalsa()
    salida: dict = {}

    def _correr():
        salida["res"] = apply.aplicar_rgb(
            manifiesto, _cfg(), None, msgs, _SignalFalsa(), _SignalFalsa(),
            controlador=_ControladorFalso(trabajadores=workers))

    # Watchdog (C1): si el pool/rayon vuelve a colgarse tras `fork`, FALLA en vez de bloquear el CI.
    hilo = threading.Thread(target=_correr, daemon=True)
    hilo.start()
    hilo.join(180)
    assert not hilo.is_alive(), "aplicar_rgb colgado con la fase rgb + fork (deadlock de rayon?)"
    res = salida["res"]
    assert res == {"hecho": 8, "fallido": 1}
    est = _estados(manifiesto)
    assert est[f"DJI_{VENENO}.JPG"] == "fallido"
    assert sum(1 for v in est.values() if v == "hecho") == 8
    assert len(rondas) <= 6
    if workers > 1:
        assert rondas[0] == workers and rondas[-1] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="os.fork no existe en Windows")
def test_fork_tras_usar_rust_no_cuelga(tmp_path):
    """C1: tras usar `rgb_procesar` en el padre, un hijo `fork` también debe poder
    llamarlo (sin pool global de rayon heredado vacío). Con timeout: falla, no cuelga."""
    import signal
    import time
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 320, 240)
    _rust_modo_a(rs, datos, 90, _caja(320, 240), 40, 40, tmp_path)  # inicializa lo que sea
    pid = os.fork()
    if pid == 0:
        try:
            rs.rgb_procesar(datos, _exif_bytes(datos), _caja(320, 240), 90, 40, 40,
                            str(tmp_path / "h_o.jpg"), str(tmp_path / "h_c.jpg"), "a")
            os._exit(0)
        except BaseException:  # noqa: BLE001
            os._exit(3)
    fin = time.time() + 60
    while time.time() < fin:
        r, estado = os.waitpid(pid, os.WNOHANG)
        if r:
            assert os.WIFEXITED(estado) and os.WEXITSTATUS(estado) == 0, estado
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    pytest.fail("hijo fork colgado en rgb_procesar (deadlock de rayon)")


def test_com_del_origen_se_conserva_como_pillow(tmp_path):
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 320, 240, comentario=b"DJI comentario \xc3\xb1")
    assert b"\xff\xfe" in datos
    for angulo in (0, 90, 270):
        ref = _ref_pil(datos, angulo, _caja(320, 240), 40, 40, tmp_path)
        got, _ = _rust_modo_a(rs, datos, angulo, _caja(320, 240), 40, 40, tmp_path)
        _comparar(rs, ref, got, f"COM angulo={angulo}")
        assert b"DJI comentario" in got["orig"] and b"DJI comentario" in got["crop"]


@pytest.mark.parametrize("calidad", [0, 1])
def test_calidad_cero_como_pillow(tmp_path, calidad):
    rs = _rs_real()
    datos = _origen(tmp_path / "o.JPG", 320, 240)
    ref = _ref_pil(datos, 0, _caja(320, 240), calidad, calidad, tmp_path)
    got, _ = _rust_modo_a(rs, datos, 0, _caja(320, 240), calidad, calidad, tmp_path)
    _comparar(rs, ref, got, f"calidad={calidad}")


def test_caja_fuera_de_la_imagen_rust_igual_que_sin_flag(tmp_path, monkeypatch):
    """I2: pct >= 0.94 en 4:3 y 16:9 da caja fuera (Pillow rellena en negro). Con la
    fase activa Rust rellena igual: mismos bytes que sin flag."""
    _rs_real()
    for w, h in ((640, 480), (640, 360)):
        origen = tmp_path / f"o{w}.JPG"
        _origen(origen, w, h)
        caja = apply._caja_recorte_termico(w, h, 0.95 if h == 480 else 0.99)
        assert caja[0] < 0 or caja[1] < 0 or caja[2] > w or caja[3] > h
        pct = 0.95 if h == 480 else 0.99
        f = lambda d: _fila_dict(str(origen), str(tmp_path / d / "o.JPG"),  # noqa: E731
                                 str(tmp_path / d / "c.JPG"), angulo_giro=90, pct_recorte=pct)
        monkeypatch.delenv("ATOM_RUST_FASES", raising=False)
        apply._escribir_salidas_de_fila(f(f"py{w}"), _cfg(), pipeline)
        monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
        apply._escribir_salidas_de_fila(f(f"rs{w}"), _cfg(), pipeline)
        assert _ficheros(tmp_path / f"rs{w}") == _ficheros(tmp_path / f"py{w}")


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


@pytest.mark.parametrize("muta", [_muta_bytes_extra, _muta_hueco_de_ceros, _muta_scan_corrupto,
                                  _muta_truncado_con_eoi, _muta_marcador_invalido],
                         ids=lambda f: f.__name__[len("_muta_"):])
def test_jpeg_mutado_decide_pillow(tmp_path, monkeypatch, muta):
    """I3: ante datos corruptos (avisos de libjpeg) Rust no da la imagen por buena: el
    resultado con la fase activa es SIEMPRE el de Pillow (mismos bytes o mismo tipo de
    excepción), aunque libjpeg avise donde Pillow acepta."""
    _rs_real()
    origen = tmp_path / "o.JPG"
    base = _origen(origen, 320, 240)
    origen.write_bytes(muta(base))

    def _correr(d):
        try:
            apply._escribir_salidas_de_fila(_fila(tmp_path, origen, d, 90, True), _cfg(), pipeline)
        except Exception as e:  # noqa: BLE001
            return ("exc", type(e).__name__, {})
        return ("ok", "", _ficheros(tmp_path / d))

    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)
    py = _correr("py")
    monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
    rs = _correr("rs")
    assert rs == py, f"{muta.__name__}: Rust {rs[:2]} != Pillow {py[:2]}"
    if rs[0] == "exc":
        assert _ficheros(tmp_path / "rs") == {}  # sin .parcial


def test_flag_off_regresion():
    """Con el flag off `exigir('rgb')` es None: el camino Python no se toca
    (`tests/test_apply_rgb.py` y `tests/test_apply_rgb_pool_roto.py` lo cubren en CI)."""
    assert rust_bridge.exigir("rgb") is None


@pytest.mark.rust_ab
def test_corpus_real_bit_identico(tmp_path, monkeypatch):
    """A/B sobre RGB reales del corpus PRUEBA (solo lectura, en secuencia): sha256 final
    del camino Rust == camino Python. Con ATOM_RS_REQUIRED=1 y sin corpus, sintético."""
    _rs_real()
    base = rust_corpus.corpus()
    if base is None:
        if not _REQUERIDO:
            pytest.skip("sin ATOM_RUST_CORPUS")
        rutas = []
        for i in range(12):
            r = tmp_path / "sint" / f"DJI_{i:04d}_W.JPG"
            r.parent.mkdir(exist_ok=True)
            _origen(r, 640 + 8 * i, 480 + 5 * i, semilla=i)
            rutas.append(r)
    else:
        limite = int(os.environ.get("ATOM_RUST_CORPUS_N", "150"))
        rutas = [p for p in sorted(base.rglob("*.JPG")) if not p.name.upper().endswith("_T.JPG")
                 ][:limite]
        assert len(rutas) >= min(limite, 150), f"corpus con {len(rutas)} RGB (<150)"
    difs = []
    for i, ruta in enumerate(rutas):
        angulo = (0, 90, 270)[i % 3]
        py, rs_ = tmp_path / f"py{i}", tmp_path / f"rs{i}"
        monkeypatch.delenv("ATOM_RUST_FASES", raising=False)
        apply._escribir_salidas_de_fila(_fila(py, ruta, "x", angulo, True), _cfg(), pipeline)
        monkeypatch.setenv("ATOM_RUST_FASES", "rgb")
        apply._escribir_salidas_de_fila(_fila(rs_, ruta, "x", angulo, True), _cfg(), pipeline)
        if _ficheros(py) != _ficheros(rs_):
            difs.append(str(ruta))
    assert difs == [], f"{len(difs)}/{len(rutas)} salidas distintas: {difs[:5]}"
