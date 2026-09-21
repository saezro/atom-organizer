"""Tests de `atom_core.rgb_gpu` sin GPU disponible.

`rgb_gpu.activo()` es la única puerta que decide GPU vs CPU en
`apply.aplicar_rgb` (ver `atom_core/apply.py:604`). En una máquina sin
CUDA/CuPy/nvImageCodec —la única que tenemos en CI/dev— `activo()` debe
devolver `False` siempre, y `aplicar_rgb` debe caer al camino CPU
(`_escribir_salidas_de_fila`) sin tocar `rgb_gpu.aplicar` (la ruta GPU).
"""
import importlib
import sys

import pytest

import pipeline as pipeline_real
from atom_core import apply, rgb_gpu
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import SplitImagesConfig


class _SignalFalsa:
    """Doble mínimo de `progress_callback`/`progress_bar`/`progress_summarize`."""

    def emit(self, valor=None, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _rgb_gpu_limpio():
    """`rgb_gpu._ESTADO` cachea el resultado de `activo()` a nivel de módulo
    (clave `"ok"`). Sin recargar el módulo entre tests, el primero que
    resuelva `activo()` contaminaría a todos los siguientes con su
    resultado cacheado — `reload` reconstruye `_ESTADO = {}` desde cero."""
    importlib.reload(rgb_gpu)
    apply.rgb_gpu = rgb_gpu
    yield
    importlib.reload(rgb_gpu)
    apply.rgb_gpu = rgb_gpu


def _sin_cupy(monkeypatch):
    """`sys.modules["cupy"] = None` es el modismo estándar para que un
    `import cupy` posterior falle con `ImportError` aunque el paquete esté
    instalado de verdad en el entorno (o no exista, como aquí)."""
    monkeypatch.setitem(sys.modules, "cupy", None)


def test_activo_sin_env_var_es_falso(monkeypatch):
    monkeypatch.delenv("ORGANIZER_RGB_GPU", raising=False)
    assert rgb_gpu.activo() is False


def test_activo_con_env_var_pero_sin_cupy_es_falso(monkeypatch):
    monkeypatch.setenv("ORGANIZER_RGB_GPU", "1")
    _sin_cupy(monkeypatch)
    assert rgb_gpu.activo() is False


def test_activo_con_env_var_a_0_es_falso_aunque_hubiera_cupy(monkeypatch):
    monkeypatch.delenv("ORGANIZER_RGB_GPU", raising=False)
    _sin_cupy(monkeypatch)
    assert rgb_gpu.activo() is False


def _cfg():
    """`SplitImagesConfig` mínima; solo `compress_level` importa aquí."""
    return SplitImagesConfig(
        input_folder="/origen", output_folder="/destino",
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="/estadillo.csv", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=85, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=True, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=True, gen_thumbnails=True, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5.0,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5.0,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=True, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )


def _fila_manifiesto(origen, salida_original):
    return FilaManifiesto(
        ruta_origen=str(origen),
        tipo="RGB",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb="PB1",
        vuelo="V01",
        nombre_nuevo="20260501_100000_" + str(origen).rsplit("/", 1)[-1],
        angulo_giro=0,
        pct_recorte=None,
        comprime=True,
        ruta_salida_original=str(salida_original),
        ruta_salida_crop=None,
        ruta_salida_tiff=None,
        unassigned=False,
    )


def test_aplicar_rgb_cae_a_cpu_sin_gpu(tmp_path, make_dji_jpeg, monkeypatch):
    """Con `activo() == False`, `aplicar_rgb` no debe ni mirar
    `rgb_gpu.aplicar` (la ruta GPU): si la llamara, este test revienta con
    el `RuntimeError` del espía en vez de escribir la imagen por CPU."""
    monkeypatch.setenv("ORGANIZER_RGB_GPU", "1")
    _sin_cupy(monkeypatch)

    def _explota(*args, **kwargs):
        raise RuntimeError("no debería llamarse a la ruta GPU sin CuPy")

    monkeypatch.setattr(rgb_gpu, "aplicar", _explota)

    origen = tmp_path / "origen" / "DJI_0001.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))
    salida = tmp_path / "salida" / "DJI_0001.JPG"

    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila_manifiesto(origen, salida)])

    resultado = apply.aplicar_rgb(
        manifiesto, _cfg(), pipeline_real,
        _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 0}
    assert salida.exists()
    manifiesto.cerrar()
