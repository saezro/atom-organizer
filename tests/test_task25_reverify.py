"""
Reverificación de 5 mejoras que YA deberían estar implementadas en el código real
(no se reimplementa nada aquí; si algún assert falla, documenta un gap real).
"""
import os
import struct
import subprocess
import types

import numpy as np
import numpy.testing  # noqa: F401  (import eager para evitar lazy-load bajo monkeypatch de subprocess.run)
import pandas as pd
import pytest
from PIL import Image as PILImage


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


# --- 1. TIFF en paralelo a JPG, sin subcarpeta "TIF" ------------------------
# gen_struct/split_images.py:407-421 (convert_dji_images_to_tif): el mismo
# input_folder se pasa como output_folder a convert_dji_image_to_tif.
def test_tiff_stays_parallel_to_jpg_no_tif_subfolder(tmp_path, logger, make_dji_jpeg):
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    make_dji_jpeg(str(input_folder / "DJI_0001_T.JPG"))

    obj = split_images.SplitImages(logger)
    obj.total_images_number = 1
    obj.current_image_number = 0

    calls = []

    def fake_convert(input_folder_arg, output_folder_arg, image_name, *args, **kwargs):
        calls.append((input_folder_arg, output_folder_arg, image_name))

    obj.convert_dji_image_to_tif = fake_convert
    progress = _noop_progress()

    obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

    assert len(calls) == 1
    called_input, called_output, called_image = calls[0]
    assert called_image == "DJI_0001_T.JPG"
    assert called_input == str(input_folder)
    assert called_output == str(input_folder), (
        "convert_dji_image_to_tif debe recibir el MISMO output_folder que input_folder "
        "(TIFF en paralelo, sin subcarpeta TIF) — gen_struct/split_images.py:407-421"
    )
    assert "TIF" not in os.listdir(input_folder)


# --- 2 y 5. Rotación de TIFF preserva radiometría + copia EXIF con exiftool,
#            y RJPG->TIFF gira 90/-90 con np.rot90 sobre el array crudo -------
# gen_struct/split_images.py:516-521 (rotación del array antes de guardar) y
# :563-573 (guardado + tagsfromfile con exiftool).
@pytest.mark.parametrize(
    "rotate_kwargs,rot90_k,rot90_axes",
    [
        ({"rotate_90": True}, 1, (1, 0)),      # Clockwise
        ({"rotate_minus_90": True}, 1, (0, 1)),  # Counterclockwise
    ],
)
def test_tiff_rotation_preserves_radiometry_and_copies_exif(
    tmp_path, logger, make_dji_jpeg, monkeypatch, rotate_kwargs, rot90_k, rot90_axes
):
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    image_name = "DJI_0001_T.JPG"
    image_path = make_dji_jpeg(str(input_folder / image_name))

    img_size = PILImage.open(image_path).size  # (64, 48) ancho x alto
    h, w = img_size[1], img_size[0]  # reshape(size[1], size[0]) = (48, 64)
    values = [float(i) for i in range(h * w)]
    raw_bytes = struct.pack(f"{h * w}f", *values)
    expected_arr = np.array(values, dtype=np.float64).reshape(h, w)
    expected_rotated = np.rot90(expected_arr, rot90_k, rot90_axes)

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            # Simula la utilidad DJI generando el .raw con datos radiométricos conocidos.
            with open(os.path.join(str(input_folder), image_name + ".raw"), "wb") as f:
                f.write(raw_bytes)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(split_images.subprocess, "run", fake_run)

    obj = split_images.SplitImages(logger)
    progress = _noop_progress()

    obj.convert_dji_image_to_tif(
        str(input_folder), str(input_folder), image_name,
        "exiftool", "dji_utility", progress, progress,
        **rotate_kwargs,
    )

    tiff_path = os.path.join(str(input_folder), "DJI_0001_T.tiff")
    assert os.path.exists(tiff_path), "No se generó el TIFF esperado."

    saved_arr = np.array(PILImage.open(tiff_path))
    np.testing.assert_array_equal(
        saved_arr, expected_rotated,
        err_msg=(
            "El TIFF guardado no coincide con el array crudo rotado ANTES de guardar "
            "(radiometría no preservada) — gen_struct/split_images.py:516-521,563-565"
        ),
    )

    assert len(calls) == 2, "Se esperaban 2 llamadas a subprocess.run: utilidad DJI + exiftool."
    exiftool_cmd = calls[1]
    assert "tagsfromfile" in exiftool_cmd, "No se copiaron metadatos EXIF con exiftool -tagsfromfile."
    assert "-overwrite_original_in_place" in exiftool_cmd
    assert image_name in exiftool_cmd and "DJI_0001_T.tiff" in exiftool_cmd


# --- 3. CSV _Videofiles con columnas ['New Name','Original Name','Degree'] --
# gen_struct/gen_struct_folder.py:503-658 (gen_thumbnails_and_rotate).
def test_videofiles_csv_columns_and_degree(tmp_path, logger, make_dji_jpeg):
    import pipeline as gen_struct_folder

    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    # gimbal_yaw = 0.0 en ambas imágenes: con los límites de abajo caen en el
    # bucket "no rotar" (Degree = 0), evitando la rama de rotate_and_save.
    make_dji_jpeg(str(flight_folder / "DJI_0001_T.JPG"), gimbal_yaw=0.0)
    make_dji_jpeg(str(flight_folder / "DJI_0002_T.JPG"), gimbal_yaw=0.0)

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.miniaturas_root_folder = str(planta_folder / "MINIATURAS")
    os.makedirs(obj.miniaturas_root_folder)
    obj.total_images_number = 2
    obj.current_image_number = 0

    progress = _noop_progress()

    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False,
        max_error=50, lim_max_270=-10, lim_min_270=-170,
        lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    csv_path = os.path.join(obj.miniaturas_root_folder, "PB1_V01_miniaturas", "PB1_V01_Videofiles.csv")
    assert os.path.exists(csv_path), (
        "No se generó el CSV _Videofiles — gen_struct/gen_struct_folder.py:649-652"
    )
    df = pd.read_csv(csv_path)
    assert list(df.columns) == ["New Name", "Original Name", "Degree"], (
        "Columnas del CSV _Videofiles distintas de las esperadas."
    )
    assert len(df) == 2
    assert set(df["Degree"].unique()) == {0}


# --- 4. RGB gira con los mismos grados que las térmicas ---------------------
# image_processing/compress_image.py:253-313 (rotate_and_save), rama RGB :299-303.
def test_rgb_rotates_with_same_degrees_param_as_thermal(tmp_path, logger, make_dji_jpeg):
    import pipeline as compress_image

    obj = compress_image.CompressImage(logger)
    progress = _noop_progress()
    degrees = PILImage.ROTATE_90  # mismo valor que gen_struct_folder.py pasa para ambas ramas.

    # --- Rama térmica (rgb_processing=False): no necesita XMP, solo geometría. ---
    thermal_in = tmp_path / "thermal_in"
    thermal_in.mkdir()
    thermal_mini = tmp_path / "thermal_mini"
    thermal_mini.mkdir()
    base = PILImage.new("RGB", (10, 6), color=(10, 20, 30))
    base.save(thermal_in / "T.JPG")
    original_size = base.size

    obj.rotate_and_save(
        "T.JPG", str(thermal_in), str(thermal_mini), degrees, 90,
        "T_out.JPG", False, str(thermal_mini), progress,
    )
    thermal_result_size = PILImage.open(thermal_mini / "T_out.JPG").size
    assert thermal_result_size == (original_size[1], original_size[0]), (
        "ROTATE_90 debe intercambiar ancho/alto (transpose real, no crop) en la rama térmica."
    )

    # --- Rama RGB (rgb_processing=True): usa el MISMO `degrees`; requiere XMP DJI. ---
    rgb_dir = tmp_path / "rgb_in"
    rgb_dir.mkdir()
    rgb_img_path = make_dji_jpeg(str(rgb_dir / "R.JPG"))
    original_rgb_size = PILImage.open(rgb_img_path).size

    obj.rotate_and_save(
        "R.JPG", str(rgb_dir), str(rgb_dir), degrees, 90,
        "R.JPG", True, str(rgb_dir), progress,
    )
    rgb_result_size = PILImage.open(rgb_img_path).size

    assert rgb_result_size == (original_rgb_size[1], original_rgb_size[0]), (
        "La rama RGB de rotate_and_save (image_processing/compress_image.py:299-303) debe "
        "aplicar el MISMO transpose(degrees) que la rama térmica (:296) — confirma que RGB "
        "gira con los mismos grados que las térmicas."
    )
