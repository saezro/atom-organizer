import pickle

import pytest
from PIL import Image

from pipeline import process_one_image, ImageProcessConfig


def _make_synthetic_jpeg(path, size=(200, 100), color=(120, 130, 140)):
    img = Image.new("RGB", size, color=color)
    img.save(path, format="JPEG")
    img.close()
    return str(path)


def test_process_one_image_guarda_con_dimensiones_esperadas_sin_transformar(tmp_path):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    _make_synthetic_jpeg(src, size=(200, 100))

    cfg = ImageProcessConfig(output_path=str(dst), quality=80)
    result_path = process_one_image(str(src), cfg)

    assert result_path == str(dst)
    with Image.open(dst) as out_img:
        assert out_img.size == (200, 100)


def test_process_one_image_aplica_crop_y_rotate(tmp_path):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    _make_synthetic_jpeg(src, size=(200, 100))

    # Recorte a 100x50 centrado, luego rotación 90 grados -> dimensiones finales invertidas (50x100)
    cfg = ImageProcessConfig(
        output_path=str(dst),
        quality=80,
        crop_box=(50, 25, 150, 75),  # 100x50
        rotate_degrees=Image.Transpose.ROTATE_90,
    )
    process_one_image(str(src), cfg)

    with Image.open(dst) as out_img:
        assert out_img.size == (50, 100)


def test_process_one_image_abre_la_imagen_una_sola_vez(tmp_path, monkeypatch):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    _make_synthetic_jpeg(src, size=(200, 100))

    import pipeline as ci_module
    original_open = ci_module.Image.open
    call_count = {"n": 0}

    def _counting_open(*args, **kwargs):
        call_count["n"] += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr(ci_module.Image, "open", _counting_open)

    cfg = ImageProcessConfig(
        output_path=str(dst),
        quality=80,
        crop_box=(50, 25, 150, 75),
        rotate_degrees=Image.Transpose.ROTATE_90,
    )
    process_one_image(str(src), cfg)

    assert call_count["n"] == 1, "process_one_image ha abierto la imagen más de una vez"


def test_process_one_image_crop_centrado_por_porcentaje(tmp_path, monkeypatch):
    src = tmp_path / "in.jpg"
    dst = tmp_path / "out.jpg"
    _make_synthetic_jpeg(src, size=(200, 100))

    import pipeline as ci_module
    original_open = ci_module.Image.open
    call_count = {"n": 0}

    def _counting_open(*args, **kwargs):
        call_count["n"] += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr(ci_module.Image, "open", _counting_open)

    cfg = ImageProcessConfig(output_path=str(dst), quality=80, crop_centered_pct=0.5)
    process_one_image(str(src), cfg)

    assert call_count["n"] == 1, "process_one_image ha abierto la imagen más de una vez"

    with Image.open(dst) as out_img:
        assert out_img.size == (100, 50)


def test_process_one_image_y_cfg_son_picklables():
    """Prerrequisito de Task 17 (ProcessPoolExecutor, spawn en Windows): tanto la función
    como la config deben poder serializarse con pickle sin errores."""
    cfg = ImageProcessConfig(output_path="/tmp/out.jpg", quality=80, crop_box=(0, 0, 10, 10))
    pickled_cfg = pickle.dumps(cfg)
    assert pickle.loads(pickled_cfg) == cfg

    pickled_fn = pickle.dumps(process_one_image)
    assert pickle.loads(pickled_fn) is process_one_image
