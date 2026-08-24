"""Reejecutar "Convertir a TIF" sobre un lote ya procesado NO puede volver a
invocar al SDK de DJI para las térmicas cuyo `.tiff` de destino ya existe.

La pasada anterior gira `*_T.JPG` IN-PLACE (`rotate_thermal_jpgs_in_place`,
pipeline.py:2861): tras eso el JPG ya no es un R-JPEG válido para el SDK. Si se
relanza la fase de conversión (caso normal de recuperación: el Job de Cloud Run
no reanuda por fases), el SDK rechaza la imagen (`dji_rc != 0`, pipeline.py
~3116) y el `FileNotFoundError` posterior del `.raw` que nunca se escribió
infla `error_splitting_images` sin que haya nada roto: el `.tiff` bueno de la
primera pasada sigue ahí intacto.

`convert_dji_image_to_tif` debe, ANTES de invocar el SDK, comprobar si el
`.tiff` de destino ya existe con tamaño > 0 y, si es así, saltarse la
conversión de ese fichero sin contarlo como error.
"""
import os
import struct
import subprocess
import types

from PIL import Image as PILImage


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def _cmd_text(cmd):
    return " ".join(str(a) for a in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)


def _es_conversor_dji(cmd):
    texto = _cmd_text(cmd)
    return "dji_irp" in texto or "dji_utility" in texto


def _make_raw(image_path, input_folder, image_name):
    w, h = PILImage.open(image_path).size
    values = [float(i) for i in range(h * w)]
    with open(os.path.join(str(input_folder), image_name + ".raw"), "wb") as f:
        f.write(struct.pack(f"{h * w}f", *values))


def test_segunda_pasada_no_reinvoca_sdk_si_el_tiff_ya_existe(tmp_path, logger, make_dji_jpeg, monkeypatch):
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    name = "DJI_0001_T.JPG"
    path = make_dji_jpeg(str(input_folder / name))

    # --- Primera pasada: el SDK "funciona" y deja el .tiff bueno. ---
    def fake_run_ok(cmd, *args, **kwargs):
        if _es_conversor_dji(cmd) and name in _cmd_text(cmd):
            _make_raw(path, input_folder, name)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(split_images.subprocess, "run", fake_run_ok)

    obj = split_images.SplitImages(logger)
    obj.total_images_number = 1
    obj.current_image_number = 0
    progress = _noop_progress()
    obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

    tiff_path = os.path.join(str(input_folder), "DJI_0001_T.tiff")
    assert os.path.exists(tiff_path), "La primera pasada no generó el TIFF: el test no prueba nada."
    assert obj.error_splitting_images == 0

    # --- Segunda pasada: el JPG térmico ya fue girado in-place por una fase
    # anterior (simulado no regenerando el .raw), así que el SDK real lo
    # rechazaría (dji_rc != 0). El fix debe evitar que se le llegue a invocar.
    llamadas_sdk = []

    def fake_run_reject(cmd, *args, **kwargs):
        if _es_conversor_dji(cmd) and name in _cmd_text(cmd):
            llamadas_sdk.append(cmd)
            # SDK rechaza la imagen: no escribe .raw y devuelve rc != 0.
            return subprocess.CompletedProcess(args=cmd, returncode=(256 - 16))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(split_images.subprocess, "run", fake_run_reject)

    obj2 = split_images.SplitImages(logger)
    obj2.total_images_number = 1
    obj2.current_image_number = 0
    obj2.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

    assert not llamadas_sdk, (
        "La segunda pasada invocó al SDK de DJI para una imagen cuyo .tiff de "
        "destino ya existía: debía saltarse la conversión."
    )
    assert obj2.error_splitting_images == 0, (
        "El TIFF ya existente se contó como error en vez de como 'ya hecho'."
    )
    assert not obj2.images_error_splitting_images
