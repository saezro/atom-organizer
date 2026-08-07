import os
from pipeline import SplitImages


def test_imagen_zoom_va_a_rgb_zoom(tmp_path, logger):
    obj = SplitImages(logger)
    output_folder = str(tmp_path)
    dest_zoom = obj._rgb_destination_folder(output_folder, "DJI_0001_Z.jpg")
    dest_normal = obj._rgb_destination_folder(output_folder, "DJI_0002.jpg")
    assert dest_zoom == os.path.join(output_folder, "RGB", "ZOOM")
    assert dest_normal == os.path.join(output_folder, "RGB")
    assert os.path.isdir(dest_zoom)  # se crea si no existía
