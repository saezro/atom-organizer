"""Golden de paridad del Organizer: congela la salida del camino Python (corpus sintético).

Uso (desde la raíz del repo, con el venv del proyecto):

    .venv/bin/python tests/golden/generar.py          # regraba indice/rgb/termica.json
    .venv/bin/python tests/golden/generar.py --check  # solo compara, no escribe

El corpus es DETERMINISTA (semillas fijas, sin reloj ni azar); cada caso lleva el sha256
de su ENTRADA para detectar deriva del generador (o de Pillow/piexif al crear la imagen)
antes de culpar al código bajo prueba. `tests/test_golden_paridad.py` regenera el corpus
y compara la salida del camino ACTIVO contra estos JSON.

Salidas JPEG dependen de la libjpeg-turbo de Pillow: se guarda su versión en ``_meta``.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import numpy as np

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
for _p in (str(RAIZ), str(RAIZ / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tests.conftest  # noqa: E402,F401  (stub de winreg, antes de importar utils/pipeline)

import piexif  # noqa: E402
from PIL import Image, PngImagePlugin, features  # noqa: E402

import exif as exif_mod  # noqa: E402
import pipeline  # noqa: E402
from atom_core import apply, indice  # noqa: E402
from tests.test_apply_rgb import _cfg, _fila_dict  # noqa: E402
from tests import test_rust_indice_equiv as _ie  # noqa: E402
from tests import test_rust_rgb_ab as _rgb  # noqa: E402
from tests import test_rust_termica_ab as _te  # noqa: E402

_VAR_RUST = ("ATOM_RUST", "ATOM_RUST_FASES", "ATOM_RUST_RGB_MODO", "ATOM_RUST_RGB_B_APROBADO")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ corpus

def _png_exif_itxt(w: int = 64, h: int = 48) -> bytes:
    """PNG con chunk eXIf (Make/Model/fecha/GPS) e iTXt `XML:com.adobe.xmp` con XMP DJI."""
    info = PngImagePlugin.PngInfo()
    info.add_text("XML:com.adobe.xmp", _ie._xmp_atributos(_ie._XMP_DJI).decode("latin-1"),
                  zip=False)
    info.add_itxt("Comment", "golden ñ", lang="es", tkey="Comment")
    b = io.BytesIO()
    Image.new("RGB", (w, h), (40, 90, 140)).save(b, format="PNG", pnginfo=info,
                                                 exif=_ie._exif_bytes())
    return b.getvalue()


def _tiff_exif_gps_xmp(w: int = 64, h: int = 48) -> bytes:
    """TIFF con EXIF (Make/Model/fecha/GPS) y XMP en el tag 700."""
    ex = Image.Exif()  # `tiffinfo=` pisaría `exif=`: el tag 700 va dentro del propio Exif
    ex.load(_ie._exif_bytes())
    ex[700] = _ie._xmp_atributos(_ie._XMP_DJI)
    b = io.BytesIO()
    Image.new("RGB", (w, h), (60, 70, 80)).save(b, format="TIFF", exif=ex)
    return b.getvalue()


def _ruido(w: int, h: int, c: int, semilla: int) -> np.ndarray:
    a = np.random.RandomState(semilla).randint(0, 256, size=(h, w, c), dtype=np.uint8)
    a[..., 0] = (a[..., 0] // 4 + (np.arange(w, dtype=np.uint32)[None, :] * 192 // max(w - 1, 1)
                                   )).astype(np.uint8)
    return a


def _guardar(img: Image.Image, formato: str, **kw) -> bytes:
    b = io.BytesIO()
    img.save(b, format=formato, **kw)
    return b.getvalue()


def corpus() -> dict[str, tuple[str, bytes]]:
    """nombre de caso -> (extensión, bytes). Orden y contenido deterministas."""
    with tempfile.TemporaryDirectory() as t:
        jpg_rgb = _rgb._origen(Path(t) / "o.JPG", 320, 240, semilla=1)
    c: dict[str, tuple[str, bytes]] = {}
    c["jpeg_rgb"] = ("JPG", jpg_rgb)
    c["jpeg_l"] = ("JPG", _guardar(Image.fromarray(_ruido(320, 240, 1, 2)[..., 0], "L"),
                                   "JPEG", quality=90))
    c["jpeg_cmyk"] = ("JPG", _guardar(Image.fromarray(_ruido(320, 240, 4, 3), "CMYK"),
                                      "JPEG", quality=90))
    c["tiff_rgb"] = ("TIF", _guardar(Image.fromarray(_ruido(320, 240, 3, 4), "RGB"), "TIFF"))
    c["png_rgb"] = ("PNG", _guardar(Image.fromarray(_ruido(320, 240, 3, 5), "RGB"), "PNG"))
    c["png_rgba"] = ("PNG", _guardar(Image.fromarray(_ruido(320, 240, 4, 6), "RGBA"), "PNG"))
    c["png_p"] = ("PNG", _guardar(Image.fromarray(_ruido(320, 240, 3, 7), "RGB")
                                  .convert("P", palette=Image.ADAPTIVE, colors=64), "PNG"))
    c["jpeg_truncado_sin_eoi"] = ("JPG", jpg_rgb[:len(jpg_rgb) * 6 // 10])
    c["jpeg_truncado_mitad"] = ("JPG", jpg_rgb[:len(jpg_rgb) // 2])
    for nombre, muta in (("bytes_extra", _rgb._muta_bytes_extra),
                         ("hueco_de_ceros", _rgb._muta_hueco_de_ceros),
                         ("scan_corrupto", _rgb._muta_scan_corrupto),
                         ("truncado_con_eoi", _rgb._muta_truncado_con_eoi),
                         ("marcador_invalido", _rgb._muta_marcador_invalido)):
        c[f"jpeg_mutado_{nombre}"] = ("JPG", muta(jpg_rgb))
    c["jpeg_dji_full"] = ("JPG", _ie._jpeg_dji(_ie._exif_bytes(), _ie._xmp_atributos(_ie._XMP_DJI),
                                               ancho=320, alto=240))
    c["jpeg_dji_app3_64k"] = ("JPG", _ie._jpeg_dji(_ie._exif_bytes(),
                                                   _ie._xmp_atributos(_ie._XMP_DJI),
                                                   relleno_app3=65_000))
    c["jpeg_dji_app3_roto_256k"] = ("JPG", _ie._jpeg_roto_tras_256k())  # pil_estado "corto"
    c["tiff_exif_gps_xmp700"] = ("TIF", _tiff_exif_gps_xmp())
    c["png_exif_itxt"] = ("PNG", _png_exif_itxt())
    return c


# ------------------------------------------------------------------ serialización

def _ser(v):
    """Valor -> JSON estable (NaN/inf como texto, datetime ISO, tuplas como listas)."""
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        return v
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _ser(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_ser(x) for x in v]
    if v is None or isinstance(v, (bool, int, str)):
        return v
    return repr(v)


def _exc(e: BaseException, tmp: str) -> str:
    texto = f"{type(e).__name__}: {e}".replace(tmp, "<TMP>")
    return re.sub(r"0x[0-9a-fA-F]+", "0x?", texto)  # direcciones de objeto: no deterministas


class _Cb:
    def emit(self, *a, **k):
        pass


def _sin_rust() -> None:
    for v in _VAR_RUST:
        os.environ.pop(v, None)


# ------------------------------------------------------------------ índice

def resultado_indice(nombre: str, ext: str, datos: bytes, tmp: Path, logger) -> dict:
    """`indice._leer_metadatos` sobre el fichero -> dict serializable (sin `ruta`)."""
    ruta = tmp / f"{nombre}.{ext}"
    ruta.write_bytes(datos)
    gi = exif_mod.GeneralInformationFromImage(logger)
    try:
        m = indice._leer_metadatos(str(ruta), gi, _Cb())
    except Exception as e:  # noqa: BLE001
        return {"entrada_sha256": sha(datos), "excepcion": _exc(e, str(tmp))}
    import dataclasses
    d = dataclasses.asdict(m)
    d.pop("ruta")
    d["nombre"] = os.path.basename(d["nombre"])
    return {"entrada_sha256": sha(datos), "metadatos": _ser(d)}


# ------------------------------------------------------------------ rgb

#: (id, ángulo, pct_recorte; None = sin _CROP). pct 0.97 -> caja parcialmente fuera.
VARIANTES_RGB = (("a0_crop70", 0, 0.7), ("a90_sin_crop", 90, None), ("a270_crop_fuera", 270, 0.97))


def resultado_rgb(nombre: str, ext: str, datos: bytes, tmp: Path, variante, python_puro: bool) -> dict:
    vid, angulo, pct = variante
    origen = tmp / f"{nombre}.{ext}"
    origen.write_bytes(datos)
    sal = tmp / f"s_{nombre}_{vid}"
    fila = _fila_dict(str(origen), str(sal / "orig.JPG"),
                      str(sal / "crop_CROP.JPG") if pct is not None else None,
                      angulo_giro=angulo, pct_recorte=pct)
    fn = apply._escribir_salidas_de_fila_python if python_puro else apply._escribir_salidas_de_fila
    try:
        fn(fila, _cfg(), pipeline)
    except Exception as e:  # noqa: BLE001
        # Un fallo a mitad puede dejar el _CROP escrito: solo cuenta la excepción.
        return {"excepcion": _exc(e, str(tmp))}
    ficheros = {p.name: sha(p.read_bytes()) for p in sorted(sal.rglob("*")) if p.is_file()}
    return {"salidas_sha256": ficheros}


# ------------------------------------------------------------------ térmica

#: (id, ancho, alto, semilla, grados, auto_temp, low, up)
CASOS_TERMICA = (
    ("640x512_g0", 640, 512, 7, 0, False, 20, 40),
    ("640x512_g90", 640, 512, 7, 90, False, 20, 40),
    ("640x512_g270_auto", 640, 512, 9, 270, True, 20, 40),
    ("1280x1024_g0", 1280, 1024, 11, 0, False, 0, 70),
    ("64x48_fijo_g0", 64, 48, None, 0, False, 20, 40),
)


def resultado_termica(caso, tmp: Path, logger) -> dict:
    """`SplitImages.convert_dji_image_to_tif` real (rama `else` de pipeline.py, flag off) con
    el SDK de DJI sustituido por un doble que escribe el .raw sintético. Es invocable aislada:
    mismo patrón que tests/test_rust_termica_ab.py."""
    cid, w, h, semilla, grados, auto, low, up = caso
    jpg = _te._jpg_bytes(w, h)
    raw = _te._raw_sintetico(w, h, semilla=semilla)
    raiz = _te._carpeta(tmp, f"t_{cid}", jpg)

    def _sdk_falso(self, image_path, raw_path, humidity, emissivity, lib_dir):
        with open(raw_path, "wb") as fh:
            fh.write(raw)

    prog = types.SimpleNamespace(emit=lambda *a, **k: None)
    out = {"jpg_sha256": sha(jpg), "raw_sha256": sha(raw)}
    with mock.patch.object(pipeline.SplitImages, "_dji_measure_to_raw_linux", _sdk_falso), \
            mock.patch.object(pipeline, "_is_windows", lambda: False):
        obj = pipeline.SplitImages(logger)
        try:
            obj.convert_dji_image_to_tif(
                str(raiz), str(raiz), _te._IMG, "exiftool", "/no/existe/dji_irp", prog, prog,
                emissivity=0.9, humidity=50.0, auto_temp=auto,
                up_threshold_temperature=up, low_threshold_temperature=low,
                rotate_90=(grados == 90), rotate_minus_90=(grados == 270), auto_rotate=False,
                just_atom_selection=False, generate_gray_scale_images=True,
                generate_colormap_images=True, defer_exif=True)
        except Exception as e:  # noqa: BLE001
            out["excepcion"] = _exc(e, str(tmp))
            return out
    out["salidas_sha256"] = {k: sha(p.read_bytes()) for k, p in _te._salidas(raiz).items()}
    return out


# ------------------------------------------------------------------ orquestación

def _logger(tmp: Path):
    from utils import OrganizerLogger  # noqa: PLC0415
    return OrganizerLogger(name=f"golden_{tmp.name}", log_dir=str(tmp / "Logs"),
                           create_file_handler=False)


def meta() -> dict:
    return {"libjpeg_turbo": features.version("libjpeg_turbo"),
            "pillow": __import__("PIL").__version__, "numpy": np.__version__}


def generar_todo() -> dict[str, dict]:
    _sin_rust()
    casos = corpus()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        logger = _logger(tmp)
        indice_j = {"_meta": meta(), "casos": {}}
        rgb_j = {"_meta": meta(), "casos": {}}
        ter_j = {"_meta": meta(), "casos": {}}
        for nombre, (ext, datos) in casos.items():
            indice_j["casos"][nombre] = resultado_indice(nombre, ext, datos, tmp, logger)
            rgb_j["casos"][nombre] = {"entrada_sha256": sha(datos), "variantes": {
                v[0]: resultado_rgb(nombre, ext, datos, tmp, v, True) for v in VARIANTES_RGB}}
        for caso in CASOS_TERMICA:
            ter_j["casos"][caso[0]] = resultado_termica(caso, tmp, logger)
    return {"indice": indice_j, "rgb": rgb_j, "termica": ter_j}


def main() -> int:
    solo_check = "--check" in sys.argv
    res = generar_todo()
    rc = 0
    for nombre, valor in res.items():
        ruta = AQUI / f"{nombre}.json"
        texto = json.dumps(valor, indent=2, sort_keys=True) + "\n"
        if solo_check:
            if not ruta.exists() or ruta.read_text(encoding="utf-8") != texto:
                print(f"DIFIERE {ruta.name}")
                rc = 1
        else:
            ruta.write_text(texto, encoding="utf-8")
            print(f"escrito {ruta} ({len(valor['casos'])} casos)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
