"""Fase Rust ``termica`` (T1): TIFF float32 + escala de grises + colormap en Rust.

Dos bloques:

1. Tests LOCALES (sin Rust): sujetan las ramas Python de la integración con un
   ``atom_rs`` FALSO en ``sys.modules`` (mismo patrón que ``test_rust_bridge``):
   - flag on  -> ``convert_dji_image_to_tif`` llama a ``termica_procesar`` con los
     argumentos correctos (raw, resolución, umbrales, grados, rutas, LUT en bytes,
     calidades JPEG) y NO ejecuta el camino Python;
   - flag off -> el camino Python de siempre, byte a byte (``test_fase_off_usa_python_sin_cambios``);
   - flag on + módulo ausente / sin ``termica_procesar`` -> ``RustNoDisponible``,
     nunca retroceso silencioso a Python;
   - ``apply._convertir_una_termica``: con la fase activa el TIFF sale ya girado del
     conversor y ``_publicar_tiff_girado`` no lo reabre.

2. Tests A/B ``rust_ab`` (banco): Python vs ``atom_rs`` REAL, sha256 de las salidas.
   Se saltan limpiamente si ``atom_rs`` no expone ``termica_procesar``; los que usan
   corpus real, además, si ``ATOM_RUST_CORPUS`` no está definido. Corpus: pares
   ``<X>_T.JPG`` + ``<X>_T.JPG.raw`` (el ``.raw`` lo produjo el SDK de DJI una vez y se
   guarda con el corpus: aquí NO se ejecuta el SDK).

     ATOM_RUST_CORPUS=<dir> python -m pytest tests/test_rust_termica_ab.py -m rust_ab -q
"""
from __future__ import annotations

import hashlib
import importlib
import io
import os
import shutil
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
import PIL.features

import pipeline
from atom_core import apply, rust_bridge
from atom_core.rust_bridge import RustNoDisponible

from tests import rust_corpus

_IMG = "DJI_0001_T.JPG"
_W, _H = 64, 48
#: Calidad JPEG que usa PIL sin `quality=` (`save(..., format='JPEG')`): la por
#: defecto de libjpeg. La rama Rust de pipeline.py pasa este mismo valor.
_Q_DEFECTO_PIL = 75


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    monkeypatch.delenv("ATOM_RUST", raising=False)
    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)


# ---------------------------------------------------------------- utilidades

def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


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


def _convertir(logger, monkeypatch, raiz: Path, raw: bytes, *, image_name: str = _IMG,
               grados: int = 0, auto_temp: bool = False, low=20, up=40,
               gris: bool = True, color: bool = True, defer_exif: bool = True):
    """`convert_dji_image_to_tif` real con el SDK de DJI sustituido por un doble que
    escribe `raw` tal cual (mismo patrón que test_post_dji_tif_almacen)."""
    def _sdk_falso(self, image_path, raw_path, humidity, emissivity, lib_dir):
        with open(raw_path, "wb") as fh:
            fh.write(raw)

    monkeypatch.setattr(pipeline.SplitImages, "_dji_measure_to_raw_linux", _sdk_falso)
    monkeypatch.setattr(pipeline, "_is_windows", lambda: False)
    obj = pipeline.SplitImages(logger)
    prog = types.SimpleNamespace(emit=lambda *a, **k: None)
    return obj.convert_dji_image_to_tif(
        str(raiz), str(raiz), image_name, "exiftool", "/no/existe/dji_irp", prog, prog,
        emissivity=0.9, humidity=50.0, auto_temp=auto_temp,
        up_threshold_temperature=up, low_threshold_temperature=low,
        rotate_90=(grados == 90), rotate_minus_90=(grados == 270), auto_rotate=False,
        just_atom_selection=False,
        generate_gray_scale_images=gris, generate_colormap_images=color,
        defer_exif=defer_exif,
    )


# ------------------------------------------------- doble de atom_rs (local)

class _RustFalso:
    """`atom_rs` de mentira: apunta las llamadas a `termica_procesar` y deja un
    fichero en cada salida pedida para que el resto del flujo siga."""

    def __init__(self, error: Exception | None = None, con_termica: bool = True):
        self.llamadas: list[tuple] = []
        self.modulo = types.ModuleType("atom_rs")
        self.modulo.version_abi = lambda: rust_bridge.ABI_ESPERADA
        self.modulo.version = lambda: "9.9.9"
        if con_termica:
            self.modulo.termica_procesar = self._termica_procesar
        self._error = error

    def _termica_procesar(self, *args):
        self.llamadas.append(args)
        if self._error is not None:
            raise self._error
        (_raw, ancho, alto, _auto, _tmin, _tmax, grados, tiff_out, gris_out, color_out,
         _lut, _qg, _qc) = args
        for p in (tiff_out, gris_out, color_out):
            if p is not None:
                Path(p).write_bytes(b"RUST")
        girado = grados in (90, 270)
        return types.SimpleNamespace(min=0.0, max=1.0,
                                     ancho_final=alto if girado else ancho,
                                     alto_final=ancho if girado else alto)


@pytest.fixture
def rust_falso(monkeypatch):
    doble = _RustFalso()
    monkeypatch.setitem(sys.modules, "atom_rs", doble.modulo)
    return doble


# ============================================================ tests LOCALES

@pytest.mark.parametrize("grados", [0, 90, 270])
@pytest.mark.parametrize("color", [True, False])
def test_flag_on_llama_termica_procesar_con_sus_argumentos(tmp_path, logger, monkeypatch,
                                                         rust_falso, grados, color):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    raw = _raw_sintetico()
    resultado = _convertir(logger, monkeypatch, raiz, raw, grados=grados, color=color,
                           auto_temp=False, low=20, up=40)

    assert len(rust_falso.llamadas) == 1
    (r, ancho, alto, auto_temp, t_min, t_max, g, tiff_out, gris_out, color_out,
     lut, qg, qc) = rust_falso.llamadas[0]
    sal = _salidas(raiz)
    assert r == raw and isinstance(r, bytes)
    assert (ancho, alto) == (_W, _H)
    assert auto_temp is False
    assert (t_min, t_max) == (20.0, 40.0)
    assert isinstance(t_min, float) and isinstance(t_max, float)
    assert g == grados
    assert tiff_out == str(sal["tiff"])
    assert gris_out == str(sal["gris"])
    if color:
        assert color_out == str(sal["color"])
        assert lut == pipeline._get_thermal_lut("inferno").tobytes()
        assert len(lut) == pipeline._LUT_SIZE * 3
    else:
        assert color_out is None and lut is None
    assert (qg, qc) == (_Q_DEFECTO_PIL, _Q_DEFECTO_PIL)
    # El TIFF es el que escribió "Rust": el camino Python no se ejecutó.
    assert sal["tiff"].read_bytes() == b"RUST"
    assert resultado == (os.path.join(str(raiz), _IMG), str(sal["tiff"]))


def test_flag_on_sin_gris_pasa_none(tmp_path, logger, monkeypatch, rust_falso):
    monkeypatch.setenv("ATOM_RUST", "1")
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    _convertir(logger, monkeypatch, raiz, _raw_sintetico(), gris=False, color=False)
    args = rust_falso.llamadas[0]
    assert args[8] is None and args[9] is None and args[10] is None


def test_flag_off_no_toca_rust(tmp_path, logger, monkeypatch, rust_falso):
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    _convertir(logger, monkeypatch, raiz, _raw_sintetico())
    assert rust_falso.llamadas == []
    assert _salidas(raiz)["tiff"].read_bytes()[:4] == b"II*\x00"


def test_flag_on_sin_modulo_lanza_sin_retroceso(tmp_path, logger, monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    with pytest.raises(RustNoDisponible):
        _convertir(logger, monkeypatch, raiz, _raw_sintetico())
    assert not _salidas(raiz)["tiff"].exists()
    assert not any(_salidas(raiz)["gris"].parent.iterdir())


def test_flag_on_modulo_sin_termica_procesar_lanza(tmp_path, logger, monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    monkeypatch.setitem(sys.modules, "atom_rs", _RustFalso(con_termica=False).modulo)
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    with pytest.raises(RustNoDisponible, match="termica_procesar"):
        _convertir(logger, monkeypatch, raiz, _raw_sintetico())
    assert not _salidas(raiz)["tiff"].exists()


def _convert_one_safe_con(logger, monkeypatch, tmp_path, error):
    def _convert_que_lanza(self, *a, **k):
        raise error

    monkeypatch.setattr(pipeline.SplitImages, "convert_dji_image_to_tif", _convert_que_lanza)
    obj = pipeline.SplitImages(logger)
    obj.total_images_number = 1
    prog = types.SimpleNamespace(emit=lambda *a, **k: None)
    return obj, lambda: obj._convert_one_safe(str(tmp_path), _IMG, "exiftool", "dji",
                                              prog, prog, {})


def test_convert_one_safe_relanza_rust_no_disponible(tmp_path, logger, monkeypatch):
    """`RustNoDisponible` es de la instalación, no de la imagen: no se traga con el
    «se omite y se continúa» del pool, aborta el vuelo."""
    obj, llamar = _convert_one_safe_con(logger, monkeypatch, tmp_path,
                                        RustNoDisponible("wheel sin termica_procesar"))
    with pytest.raises(RustNoDisponible):
        llamar()
    assert obj.error_splitting_images == 0


def test_convert_one_safe_sigue_aislando_otros_errores(tmp_path, logger, monkeypatch):
    obj, llamar = _convert_one_safe_con(logger, monkeypatch, tmp_path, ValueError("raw roto"))
    assert llamar() is None
    assert obj.error_splitting_images == 1


def test_valueerror_de_rust_lleva_el_nombre_de_la_imagen(tmp_path, logger, monkeypatch):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    doble = _RustFalso(error=ValueError("entrada inválida: raw con NaN en el píxel 3"))
    monkeypatch.setitem(sys.modules, "atom_rs", doble.modulo)
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    with pytest.raises(ValueError, match=_IMG) as ei:
        _convertir(logger, monkeypatch, raiz, _raw_sintetico())
    assert "NaN" in str(ei.value)
    assert not _salidas(raiz)["tiff"].exists()


def test_flag_on_raw_no_multiplo_de_4_lanza_valueerror(tmp_path, logger, monkeypatch, rust_falso):
    """Mismo tipo de error que el `np.frombuffer` de la rama Python."""
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    with pytest.raises(ValueError, match=_IMG):
        _convertir(logger, monkeypatch, raiz, _raw_sintetico() + b"\x00")
    assert rust_falso.llamadas == []


# Huellas del camino Python ANTES de T1 (Pillow 12.3.0 / libjpeg-turbo 3.1.4.1,
# numpy 2.4.6, matplotlib 3.11.1), raw `_raw_sintetico()` 64x48, umbrales 20/40.
_HUELLAS_PYTHON = {
    0: {
        "tiff": "a0d59d4de2aa1210fde549467dee988c9d32244d5bb77fd97ab8ca73e3c03033",
        "gris": "816a4fd0c8e7e8e04bab009976756a36ab2603e342207b8d8ff8503539a5f8fe",
        "color": "d9163bdfba2ff56851f8e1bd570259a425c51b25b9346f6f62f82165b79ff9b4",
    },
    90: {
        "tiff": "dba98edf4203e763c19603765f80ba23f1db1f23a9d37e1023227ab4c4aaef66",
        "gris": "3139699ab1dab12fb69cca66f58b2a89fb7eb1da4a21e89fed62c6897a54d4ec",
        "color": "aec225f4c2ab256e6a899796fa545801671bfac00c0906e0826f4efa6e79b2f9",
    },
    270: {
        "tiff": "72ec4659df00127523590eda9493a01c99b1fc7f4d580cfeceadf0ece172a712",
        "gris": "109a8ce8dcb6e207d6fcac1312c666fe73501a6e1d2dc84370b3b43bdabb22a7",
        "color": "295aeca72e600b67f9acf2fec1cc7d14be484138d88664100af40231102d94df",
    },
}
_LIBJPEG_HUELLAS = "3.1.4.1"


@pytest.mark.parametrize("grados", [0, 90, 270])
def test_fase_off_usa_python_sin_cambios(tmp_path, logger, monkeypatch, grados):
    """Sin flag, `convert_dji_image_to_tif` produce los mismos bytes que antes de T1."""
    raiz = _carpeta(tmp_path, "r", _jpg_bytes(_W, _H))
    _convertir(logger, monkeypatch, raiz, _raw_sintetico(), grados=grados)
    sal = _salidas(raiz)
    huellas = _HUELLAS_PYTHON[grados]
    assert _sha(sal["tiff"]) == huellas["tiff"]
    if PIL.features.version("libjpeg_turbo") != _LIBJPEG_HUELLAS:
        pytest.skip("huellas JPEG tomadas con libjpeg-turbo " + _LIBJPEG_HUELLAS)
    assert _sha(sal["gris"]) == huellas["gris"]
    assert _sha(sal["color"]) == huellas["color"]


# ------------------------------------------------------------ apply (local)

class _PipelineDoble:
    """Doble de `SplitImages`: escribe un TIFF F 4x2 real (no cuadrado, para ver
    si alguien lo gira) y apunta los kwargs."""

    def __init__(self):
        self.kwargs = None

    def convert_dji_image_to_tif(self, input_folder, output_folder, image_name,
                                 exiftool_exe, dji_utility, pc, pb, **kwargs):
        self.kwargs = kwargs
        os.makedirs(output_folder, exist_ok=True)
        tiff = os.path.join(output_folder, os.path.splitext(image_name)[0] + ".tiff")
        Image.new("F", (4, 2)).save(tiff, format="TIFF")
        return (os.path.join(input_folder, image_name), tiff)


def _cfg_apply():
    return types.SimpleNamespace(
        convert_to_tif_emissivity=0.9, convert_to_tif_humidity=50.0,
        convert_to_tif_temp_auto=False, convert_to_tif_up_temperature=70.0,
        convert_to_tif_low_temperature=0.0, convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )


def _apply_una(tmp_path, angulo):
    origen = tmp_path / "origen" / _IMG
    origen.parent.mkdir()
    origen.write_bytes(_jpg_bytes(8, 4))
    staging = tmp_path / "staging"
    staging.mkdir()
    fila = {
        "ruta_origen": str(origen),
        "ruta_salida_tiff": str(tmp_path / "out" / "DJI_0001_T.tiff"),
        "ruta_salida_original": str(tmp_path / "out" / _IMG),
        "angulo_giro": angulo,
    }
    doble = _PipelineDoble()
    prog = types.SimpleNamespace(emit=lambda *a, **k: None)
    apply._convertir_una_termica(fila, _cfg_apply(), doble, "exiftool", "dji", prog, prog,
                                 str(staging))
    with Image.open(fila["ruta_salida_tiff"]) as im:
        return doble.kwargs, im.size, Path(fila["ruta_salida_tiff"]).read_bytes()


@pytest.mark.parametrize("angulo, r90, r270", [(0, False, False), (90, True, False),
                                                (270, False, True)])
def test_apply_con_rust_el_tiff_sale_girado_del_conversor(tmp_path, monkeypatch,
                                                          angulo, r90, r270):
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    kwargs, tam, datos = _apply_una(tmp_path, angulo)
    assert kwargs["rotate_90"] is r90
    assert kwargs["rotate_minus_90"] is r270
    assert kwargs["auto_rotate"] is False
    # `_publicar_tiff_girado` no reabre ni gira: se mueve tal cual lo dejó el conversor.
    assert tam == (4, 2)
    buf = io.BytesIO()
    Image.new("F", (4, 2)).save(buf, format="TIFF")
    assert datos == buf.getvalue()


@pytest.mark.parametrize("angulo, tam_esperado", [(0, (4, 2)), (90, (2, 4)), (270, (2, 4))])
def test_apply_sin_rust_sigue_girando_con_pil(tmp_path, angulo, tam_esperado):
    kwargs, tam, _ = _apply_una(tmp_path, angulo)
    assert kwargs["rotate_90"] is False and kwargs["rotate_minus_90"] is False
    assert tam == tam_esperado


@pytest.mark.parametrize("angulo", [0, 90, 270])
def test_publicar_tiff_ya_girado_copia_byte_a_byte(tmp_path, angulo):
    origen = tmp_path / "a.tiff"
    Image.new("F", (4, 2)).save(origen, format="TIFF")
    destino = tmp_path / "sub" / "b.tiff"
    apply._publicar_tiff_girado(str(origen), str(destino), angulo, ya_girado=True)
    assert destino.read_bytes() == origen.read_bytes()
    assert [p.name for p in destino.parent.iterdir()] == ["b.tiff"]


# ======================================================== tests A/B rust_ab

def _atom_rs_real():
    try:
        m = importlib.import_module("atom_rs")
    except ImportError as e:
        pytest.skip(f"atom_rs no instalado ({e})")
    if m is None or not hasattr(m, "termica_procesar"):
        pytest.skip("atom_rs sin termica_procesar")
    return m


@pytest.fixture
def atom_rs():
    return _atom_rs_real()


def _pares_corpus() -> list[tuple[Path, Path]]:
    raiz = rust_corpus.corpus()
    if raiz is None:
        pytest.skip(f"{rust_corpus.VARIABLE} sin definir")
    pares = []
    for raw in sorted(raiz.rglob("*.raw")):
        jpg = raw.with_suffix("")
        if jpg.is_file():
            pares.append((jpg, raw))
    if len(pares) < 60:
        pytest.fail(f"corpus {raiz}: {len(pares)} pares <X>_T.JPG + <X>_T.JPG.raw; se exigen >= 60")
    return pares


def _ab(tmp_path, logger, monkeypatch, jpg: bytes, raw: bytes, etiqueta: str, *,
        image_name: str = _IMG, **kw) -> tuple[dict, dict]:
    """Ejecuta la MISMA conversión con la fase off (Python) y on (Rust)."""
    monkeypatch.delenv("ATOM_RUST", raising=False)
    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)
    py = _carpeta(tmp_path, f"py_{etiqueta}", jpg, image_name)
    _convertir(logger, monkeypatch, py, raw, image_name=image_name, **kw)
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    rs = _carpeta(tmp_path, f"rs_{etiqueta}", jpg, image_name)
    try:
        _convertir(logger, monkeypatch, rs, raw, image_name=image_name, **kw)
    finally:
        monkeypatch.delenv("ATOM_RUST_FASES", raising=False)
    return _salidas(py, image_name), _salidas(rs, image_name)


def _comparar_jpeg(a: Path, b: Path, rs_mod) -> None:
    """sha256 idéntico obligatorio con la MISMA libjpeg-turbo; con otra versión, se
    comparan píxeles decodificados y se FALLA igualmente pidiendo decisión."""
    if _sha(a) == _sha(b):
        return
    v_pil = PIL.features.version("libjpeg_turbo")
    v_rs = rs_mod.libjpeg_turbo_version() if hasattr(rs_mod, "libjpeg_turbo_version") else None
    if v_pil is not None and v_pil == v_rs:
        pytest.fail(f"{b.name}: misma libjpeg-turbo ({v_pil}) y bytes JPEG distintos: bug en Rust")
    with Image.open(a) as ia, Image.open(b) as ib:
        mismos_pixeles = ia.size == ib.size and np.array_equal(np.asarray(ia), np.asarray(ib))
    if mismos_pixeles:
        pytest.fail(f"{b.name}: bytes distintos por libjpeg: requiere decisión de Rodrigo "
                    f"(Pillow libjpeg-turbo {v_pil}, atom_rs {v_rs}; píxeles decodificados idénticos)")
    pytest.fail(f"{b.name}: JPEG distinto también en píxeles decodificados "
                f"(Pillow libjpeg-turbo {v_pil}, atom_rs {v_rs})")


_RES_TIFF = [
    pytest.param(640, 512, 0, id="640x512"),
    pytest.param(1280, 1024, 0, id="1280x1024"),
    pytest.param(640, 512, 90, id="512x640_g90"),
    pytest.param(640, 512, 270, id="512x640_g270"),
]


@pytest.mark.rust_ab
@pytest.mark.parametrize("w, h, grados", _RES_TIFF)
def test_tiff_bit_identico_a_pil(tmp_path, logger, monkeypatch, atom_rs, w, h, grados):
    fuentes = [(_jpg_bytes(w, h), _raw_sintetico(w, h, semilla=7), _IMG)]
    if rust_corpus.corpus() is not None:
        for jpg, raw in _pares_corpus():
            datos = raw.read_bytes()
            if len(datos) == w * h * 4:
                fuentes.append((jpg.read_bytes(), datos, jpg.name))
    fallos = []
    for n, (jpg, raw, nombre) in enumerate(fuentes):
        py, rs = _ab(tmp_path, logger, monkeypatch, jpg, raw, str(n), image_name=nombre,
                     grados=grados, auto_temp=False, low=0, up=70, gris=False, color=False)
        if _sha(py["tiff"]) != _sha(rs["tiff"]):
            fallos.append(nombre)
    assert not fallos, f"TIFF distinto en {len(fallos)}/{len(fuentes)}: {fallos[:10]}"


def _fuentes_corpus_o_sinteticas():
    fuentes = [(_jpg_bytes(640, 512), _raw_sintetico(640, 512, semilla=11), _IMG)]
    if rust_corpus.corpus() is not None:
        fuentes += [(j.read_bytes(), r.read_bytes(), j.name) for j, r in _pares_corpus()]
    return fuentes


@pytest.mark.rust_ab
@pytest.mark.parametrize("grados", [0, 90, 270])
def test_gris_bit_identico(tmp_path, logger, monkeypatch, atom_rs, grados):
    for n, (jpg, raw, nombre) in enumerate(_fuentes_corpus_o_sinteticas()):
        py, rs = _ab(tmp_path, logger, monkeypatch, jpg, raw, f"{grados}_{n}", image_name=nombre,
                     grados=grados, auto_temp=False, low=0, up=70, gris=True, color=False)
        assert _sha(py["tiff"]) == _sha(rs["tiff"]), nombre
        _comparar_jpeg(py["gris"], rs["gris"], atom_rs)


@pytest.mark.rust_ab
@pytest.mark.parametrize("auto_temp", [False, True])
def test_gris_bit_identico_auto_temp(tmp_path, logger, monkeypatch, atom_rs, auto_temp):
    jpg, raw, nombre = _fuentes_corpus_o_sinteticas()[0]
    py, rs = _ab(tmp_path, logger, monkeypatch, jpg, raw, f"auto{auto_temp}",
                 auto_temp=auto_temp, low=20, up=40, gris=True, color=False)
    _comparar_jpeg(py["gris"], rs["gris"], atom_rs)


@pytest.mark.rust_ab
def test_colormap_bit_identico(tmp_path, logger, monkeypatch, atom_rs):
    """LUT inferno, umbrales fijos 0/70."""
    for n, (jpg, raw, nombre) in enumerate(_fuentes_corpus_o_sinteticas()):
        py, rs = _ab(tmp_path, logger, monkeypatch, jpg, raw, str(n), image_name=nombre,
                     auto_temp=False, low=0, up=70, gris=False, color=True)
        _comparar_jpeg(py["color"], rs["color"], atom_rs)


@pytest.mark.rust_ab
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_frame_constante_no_panic(tmp_path, logger, monkeypatch, atom_rs):
    """max == min: Python divide 0/0 -> NaN -> `astype(np.uint8)` (0 en x86_64 con
    numpy 2.4.6). Rust debe dar el mismo uint8 y no lanzar."""
    raw = np.full(640 * 512, 25.0, dtype="<f4").tobytes()
    esperado = (np.array([np.nan]).astype(np.uint8))[0]
    py, rs = _ab(tmp_path, logger, monkeypatch, _jpg_bytes(640, 512), raw, "cte",
                 auto_temp=True, gris=True, color=True)
    assert _sha(py["tiff"]) == _sha(rs["tiff"])
    with Image.open(rs["gris"]) as g:
        assert np.all(np.asarray(g) == esperado)
    _comparar_jpeg(py["gris"], rs["gris"], atom_rs)
    _comparar_jpeg(py["color"], rs["color"], atom_rs)


@pytest.mark.rust_ab
def test_raw_con_nan_lanza_valueerror(tmp_path, logger, monkeypatch, atom_rs):
    v = np.full(640 * 512, 30.0, dtype="<f4")
    v[1234] = np.nan
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    raiz = _carpeta(tmp_path, "nan", _jpg_bytes(640, 512))
    with pytest.raises(ValueError, match=_IMG):
        _convertir(logger, monkeypatch, raiz, v.tobytes(), gris=True, color=True)
    sal = _salidas(raiz)
    assert not sal["tiff"].exists() and not sal["gris"].exists() and not sal["color"].exists()
    assert not [p for p in raiz.rglob("*") if ".parcial" in p.name]


@pytest.mark.rust_ab
@pytest.mark.parametrize("malo", [np.inf, -np.inf], ids=["+inf", "-inf"])
def test_raw_con_inf_lanza_valueerror(tmp_path, logger, monkeypatch, atom_rs, malo):
    """Rust rechaza también ±inf (no finito = error visible), aunque Python no lo haga."""
    v = np.full(640 * 512, 30.0, dtype="<f4")
    v[4321] = malo
    monkeypatch.setenv("ATOM_RUST_FASES", "termica")
    raiz = _carpeta(tmp_path, "inf", _jpg_bytes(640, 512))
    with pytest.raises(ValueError, match=_IMG) as ei:
        _convertir(logger, monkeypatch, raiz, v.tobytes(), gris=True, color=True)
    assert "inf" in str(ei.value)
    sal = _salidas(raiz)
    assert not sal["tiff"].exists() and not sal["gris"].exists() and not sal["color"].exists()
    assert not [p for p in raiz.rglob("*") if ".parcial" in p.name]


@pytest.mark.rust_ab
def test_raw_tamano_incoherente(tmp_path, atom_rs):
    raw = _raw_sintetico(640, 512)[:-4]
    tiff = tmp_path / "x.tiff"
    with pytest.raises(ValueError):
        atom_rs.termica_procesar(raw, 640, 512, True, 0.0, 70.0, 0, str(tiff),
                                 None, None, None, _Q_DEFECTO_PIL, _Q_DEFECTO_PIL)
    assert not tiff.exists()


@pytest.mark.rust_ab
def test_exif_final_identico(tmp_path, logger, monkeypatch, atom_rs):
    """Tras `exiftool -tagsfromfile` (defer_exif=False) el TIFF final es el mismo."""
    import external_tools
    try:
        exiftool = external_tools.resolve_tool("exiftool")
    except Exception as e:  # noqa: BLE001 - herramienta externa ausente en este equipo
        pytest.skip(f"exiftool no disponible: {e}")
    if shutil.which(exiftool) is None and not os.path.isfile(exiftool):
        pytest.skip("exiftool no disponible")
    fuentes = _fuentes_corpus_o_sinteticas()
    for n, (jpg, raw, nombre) in enumerate(fuentes):
        py, rs = _ab(tmp_path, logger, monkeypatch, jpg, raw, str(n), image_name=nombre,
                     auto_temp=False, low=0, up=70, gris=False, color=False, defer_exif=False)
        assert _sha(py["tiff"]) == _sha(rs["tiff"]), nombre
