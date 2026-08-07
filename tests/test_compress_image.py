from PIL import Image
from utils import OrganizerLogger as ol
from pipeline import CompressImage


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_compress_image(tmp_path):
    logger = ol("test_compress_image", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return CompressImage(logger)


def test_rotate_and_save_respeta_quality_param_rgb(tmp_path, synthetic_jpeg):
    input_folder = tmp_path / "RGB" / "PB1_V1"
    input_folder.mkdir(parents=True)
    image_name = "DJI_0001.JPG"
    synthetic_jpeg(input_folder / image_name)

    ci = _make_compress_image(tmp_path)
    cb = FakeSignal()

    calidades_usadas = []
    original_save = Image.Image.save

    def spy_save(self, fp, *args, **kwargs):
        if "quality" in kwargs:
            calidades_usadas.append(kwargs["quality"])
        return original_save(self, fp, *args, **kwargs)

    Image.Image.save = spy_save
    try:
        ci.rotate_and_save(
            image_name, str(input_folder), Image.ROTATE_90, 85, progress_callback=cb,
        )
    finally:
        Image.Image.save = original_save

    assert 40 not in calidades_usadas, f"Se ha recomprimido a quality=40 fijo pese a pedir 85: {calidades_usadas}"
    assert 85 in calidades_usadas, f"No se ha usado el quality=85 solicitado: {calidades_usadas}"
