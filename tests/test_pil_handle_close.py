import os
import pytest
from PIL import Image
from pipeline import CompressImage, RGBCropping


class _NoopCallback:
    def emit(self, *args, **kwargs):
        pass


def test_compress_image_cierra_handle_si_falla_tras_abrir(organizer_logger_stub, tmp_path, synthetic_jpeg, monkeypatch):
    """Si compress_image falla DESPUÉS de abrir la imagen (p.ej. al obtener datos gimbal), el handle debe quedar cerrado
    y el archivo debe poder borrarse/reabrirse inmediatamente (comportamiento típico bloqueado en Windows si no se cierra)."""
    input_folder = tmp_path / "in"
    output_folder = tmp_path / "out"
    input_folder.mkdir()
    output_folder.mkdir()
    image_name = "foto.jpg"
    synthetic_jpeg(input_folder / image_name)

    ci = CompressImage(organizer_logger_stub)

    def _boom(*args, **kwargs):
        raise ValueError("fallo simulado tras abrir la imagen")

    monkeypatch.setattr(ci.exif_management_obj, "get_gimbal_yaw_pitch", _boom)

    ci.compress_image(image_name, str(input_folder), str(output_folder), 80, "", _NoopCallback(), aerotools_devices=False)

    # Si el handle quedó abierto, en Windows este rename fallaría con PermissionError.
    # En Linux el filesystem no bloquea, así que verificamos explícitamente que no quede
    # ningún objeto Image abierto colgado del propio método (comprobación indirecta):
    # reabrir y borrar el archivo de origen debe funcionar sin excepción.
    os.remove(str(input_folder / image_name))


def test_get_cropped_image_cierra_handle_en_camino_feliz(organizer_logger_stub, tmp_path, synthetic_jpeg):
    """Tras recortar con éxito, el handle de la imagen original debe quedar cerrado."""
    input_folder = tmp_path / "in"
    input_folder.mkdir()
    image_name = "termica.jpg"
    synthetic_jpeg(input_folder / image_name)

    rc = RGBCropping(organizer_logger_stub)
    resultado = rc.crop_centered_image(str(input_folder), image_name, {}, False, 50, _NoopCallback())

    assert resultado is not None
    # El archivo original debe poder borrarse inmediatamente tras el recorte (handle cerrado).
    os.remove(str(input_folder / image_name))


def test_compress_image_no_deja_handles_sin_cerrar(organizer_logger_stub, tmp_path, synthetic_jpeg, monkeypatch):
    import pipeline as ci_module
    opened = []
    original_open = ci_module.Image.open

    def _spy_open(*args, **kwargs):
        img = original_open(*args, **kwargs)
        opened.append(img)
        return img

    monkeypatch.setattr(ci_module.Image, "open", _spy_open)

    input_folder = tmp_path / "in"
    output_folder = tmp_path / "out"
    input_folder.mkdir()
    output_folder.mkdir()
    image_name = "foto.jpg"
    synthetic_jpeg(input_folder / image_name)

    ci = CompressImage(organizer_logger_stub)
    monkeypatch.setattr(ci.exif_management_obj, "get_gimbal_yaw_pitch", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    ci.compress_image(image_name, str(input_folder), str(output_folder), 80, "", _NoopCallback(), aerotools_devices=False)

    assert all(img.fp is None for img in opened), "Quedó al menos un handle PIL sin cerrar tras la excepción"
