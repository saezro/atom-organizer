"""Sonda de paridad libjpeg-turbo por plataforma (Task P).

Compara encode/decode JPEG de `atom_rs` (libjpeg-turbo vendorizada en el crate) con
Pillow (libjpeg-turbo propia de su wheel). Es el gate antes de T3/T4: debe pasar en
linux x86_64, linux aarch64 y windows x86_64. Se ejecuta con una wheel real de
`atom_rs` (CI `rust-wheels.yml` job `paridad-jpeg`, o Cloud Build); sin ella se salta,
salvo con `ATOM_RUST_AB_ESTRICTO=1`, donde FALLA.

Variables: `ATOM_RUST_CORPUS` (carpeta con JPG reales opcionales, max 5),
`ATOM_HUELLAS_DIR` (donde se escribe `huellas-<plataforma>.json`; por defecto `huellas-out`).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image, features

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "jpeg_plataforma"))
import generar  # noqa: E402

_ESTRICTO = os.environ.get("ATOM_RUST_AB_ESTRICTO") == "1"
CALIDADES = (40, 75)  # 40 = compress_level por defecto (organize.py)
_HUELLAS_ENTRADA = json.loads(
    (Path(__file__).parent / "fixtures" / "jpeg_plataforma" / "huellas.json").read_text("utf-8")
)

try:
    import atom_rs as _rs
    _FALTA = None if hasattr(_rs, "jpeg_sonda_codificar") else "atom_rs sin jpeg_sonda_codificar"
except Exception as e:  # noqa: BLE001
    _rs, _FALTA = None, f"atom_rs no importable: {e}"

if _FALTA and _ESTRICTO:
    pytest.fail(f"ESTRICTO: {_FALTA}", pytrace=False)
pytestmark = pytest.mark.skipif(_FALTA is not None, reason=_FALTA or "")


def _contexto() -> str:
    return (
        f"machine={platform.machine()} sys.platform={sys.platform} "
        f"Pillow={Image.__version__} PIL.libjpeg_turbo={features.version('libjpeg_turbo')} "
        f"atom_rs.libjpeg_turbo={_rs.libjpeg_turbo_version()}"
    )


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _corpus() -> dict[str, tuple[int, int, bytes]]:
    imgs = {n: f() for n, f in generar.IMAGENES.items()}
    for n, huella in _HUELLAS_ENTRADA.items():
        assert _sha(imgs[n][2]) == huella, (
            f"la imagen sintetica '{n}' no es la esperada (numpy/generar.py distintos): {_contexto()}"
        )
    carpeta = os.environ.get("ATOM_RUST_CORPUS")
    if carpeta:
        reales = sorted(p for p in Path(carpeta).iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))[:5]
        for p in reales:
            with Image.open(p) as im:
                rgb = im.convert("RGB")
                imgs[f"corpus_{p.stem}"] = (rgb.width, rgb.height, rgb.tobytes())
    return imgs


@pytest.fixture(scope="module")
def imagenes():
    return _corpus()


def _pil_jpeg(w: int, h: int, rgb: bytes, q: int, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kw = {"exif": exif} if exif is not None else {}
    Image.frombytes("RGB", (w, h), rgb).save(buf, format="JPEG", quality=q, **kw)
    return buf.getvalue()


def _nombres() -> list[str]:
    n = list(generar.IMAGENES)
    if os.environ.get("ATOM_RUST_CORPUS"):
        n += [f"corpus_{p.stem}" for p in sorted(Path(os.environ["ATOM_RUST_CORPUS"]).iterdir())
              if p.suffix.lower() in (".jpg", ".jpeg")][:5]
    return n


@pytest.mark.parametrize("q", CALIDADES)
@pytest.mark.parametrize("nombre", _nombres())
def test_encode_rust_igual_a_pil(imagenes, nombre, q):
    w, h, rgb = imagenes[nombre]
    pil = _pil_jpeg(w, h, rgb, q)
    rust = _rs.jpeg_sonda_codificar(rgb, w, h, q, None)
    assert _sha(rust) == _sha(pil), (
        f"ENCODE distinto en {nombre} q={q}: sha rust={_sha(rust)[:16]} pil={_sha(pil)[:16]} "
        f"len rust={len(rust)} pil={len(pil)}. {_contexto()}"
    )


def test_encode_con_exif_rust_igual_a_pil(imagenes):
    w, h, rgb = imagenes["tablero"]
    exif = b"Exif\x00\x00" + struct.pack("<2sHIH", b"II", 42, 8, 0) + b"\x00\x00\x00\x00"
    pil = _pil_jpeg(w, h, rgb, 75, exif)
    rust = _rs.jpeg_sonda_codificar(rgb, w, h, 75, exif)
    assert _sha(rust) == _sha(pil), (
        f"ENCODE+EXIF distinto: len rust={len(rust)} pil={len(pil)}. {_contexto()}"
    )


@pytest.mark.parametrize("nombre", _nombres())
def test_decode_rust_igual_a_pil(imagenes, nombre):
    w, h, rgb = imagenes[nombre]
    jpg = _pil_jpeg(w, h, rgb, 75)  # JPEG hecho por Pillow: aisla el decode del encode
    with Image.open(io.BytesIO(jpg)) as im:
        ref = im.convert("RGB").tobytes()
    rw, rh, px = _rs.jpeg_sonda_decodificar(jpg)
    assert (rw, rh) == (w, h), f"dimensiones {rw}x{rh} != {w}x{h}. {_contexto()}"
    if px != ref:
        dif = sum(1 for a, b in zip(px, ref) if a != b)
        pytest.fail(f"DECODE distinto en {nombre}: {dif} bytes difieren de {len(ref)}. {_contexto()}")


@pytest.mark.parametrize("grados,metodo", [(90, Image.Transpose.ROTATE_270), (270, Image.Transpose.ROTATE_90)])
def test_transpose_mismo_resultado(imagenes, grados, metodo):
    """termica::rotar 90 = horario (PIL ROTATE_270); 270 = antihorario (PIL ROTATE_90)."""
    w, h, rgb = imagenes["tablero"]
    ref = Image.frombytes("RGB", (w, h), rgb).transpose(metodo).tobytes()
    assert _rs.jpeg_sonda_rotar(rgb, w, h, grados) == ref, _contexto()


def test_huellas_pil_por_plataforma(imagenes):
    """Informativo (no falla): sha256 de Pillow y de atom_rs por imagen y calidad."""
    salida = {"contexto": _contexto(), "pil": {}, "rust": {}}
    for nombre, (w, h, rgb) in imagenes.items():
        for q in CALIDADES:
            salida["pil"][f"{nombre}_q{q}"] = _sha(_pil_jpeg(w, h, rgb, q))
            salida["rust"][f"{nombre}_q{q}"] = _sha(_rs.jpeg_sonda_codificar(rgb, w, h, q, None))
    destino = Path(os.environ.get("ATOM_HUELLAS_DIR", "huellas-out"))
    destino.mkdir(parents=True, exist_ok=True)
    nombre_plat = f"{sys.platform}-{platform.machine()}".lower()
    (destino / f"huellas-{nombre_plat}.json").write_text(json.dumps(salida, indent=2), encoding="utf-8")
