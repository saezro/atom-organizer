"""
TDD del endurecimiento del flujo DJI->TIFF (convert_dji_images_to_tif):

  1. RESILIENCIA: una imagen que falla (excepción no capturada aguas abajo:
     dji_irp que no genera .raw, .raw truncado -> struct/reshape, error al
     guardar) NO debe tumbar el vuelo entero. El resto de imágenes se procesan
     y el fallo queda registrado en self.images_error_splitting_images.

  2. PARALELISMO: convert_dji_images_to_tif procesa las imágenes en paralelo
     (ThreadPoolExecutor, self.max_dji_workers) sin cambiar el resultado.

Estos tests deben FALLAR contra el código secuencial-sin-red actual (RED).
"""
import os
import struct
import subprocess
import threading
import types

import numpy as np
import numpy.testing  # noqa: F401  (eager para evitar lazy-load bajo monkeypatch)
import pytest
from PIL import Image as PILImage


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def _make_raw(image_path, input_folder, image_name):
    """Genera el .raw sintético (floats 0..N) del tamaño de la imagen dada."""
    w, h = PILImage.open(image_path).size  # (64, 48) -> w=64, h=48
    values = [float(i) for i in range(h * w)]
    with open(os.path.join(str(input_folder), image_name + ".raw"), "wb") as f:
        f.write(struct.pack(f"{h * w}f", *values))


# --- 1. RESILIENCIA: una imagen que lanza no aborta el resto del vuelo -------
def test_lote_continua_si_una_imagen_lanza(tmp_path, logger, make_dji_jpeg):
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    for n in ("DJI_0001_T.JPG", "DJI_0002_T.JPG", "DJI_0003_T.JPG"):
        make_dji_jpeg(str(input_folder / n))

    obj = split_images.SplitImages(logger)
    obj.total_images_number = 3
    obj.current_image_number = 0

    processed = []

    def fake_convert(inp, out, image_name, *args, **kwargs):
        if image_name == "DJI_0002_T.JPG":
            raise RuntimeError("boom simulado (p.ej. .raw truncado -> reshape)")
        processed.append(image_name)
        return (os.path.join(inp, image_name),
                os.path.join(out, image_name.removesuffix(".JPG") + ".tiff"))

    obj.convert_dji_image_to_tif = fake_convert
    obj._run_exif_batch = lambda *a, **k: None  # no exif real en este test
    progress = _noop_progress()

    # Contrato NUEVO: NO propaga la excepción; procesa las 2 sanas y registra la fallida.
    obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

    assert set(processed) == {"DJI_0001_T.JPG", "DJI_0003_T.JPG"}, (
        "Las imágenes sanas deben procesarse aunque otra falle (vuelo no abortado)."
    )
    fallidas = [os.path.basename(p) for p in obj.images_error_splitting_images]
    assert "DJI_0002_T.JPG" in fallidas, "La imagen fallida debe quedar registrada."
    assert obj.error_splitting_images >= 1


# --- 2. RESILIENCIA end-to-end: .raw truncado (fallo REAL aguas abajo) -------
def test_raw_truncado_no_tumba_el_lote(tmp_path, logger, make_dji_jpeg, monkeypatch):
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    names = ["DJI_0001_T.JPG", "DJI_0002_T.JPG", "DJI_0003_T.JPG"]
    paths = {n: make_dji_jpeg(str(input_folder / n)) for n in names}

    def fake_run(cmd, *args, **kwargs):
        # Identifica la imagen por el nombre presente en el comando (string).
        for n in names:
            if n in cmd:
                if n == "DJI_0002_T.JPG":
                    # dji_irp "corrupto": escribe un .raw truncado -> struct/reshape peta.
                    with open(os.path.join(str(input_folder), n + ".raw"), "wb") as f:
                        f.write(struct.pack("4f", 1.0, 2.0, 3.0, 4.0))
                else:
                    _make_raw(paths[n], input_folder, n)
                break
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(split_images.subprocess, "run", fake_run)

    obj = split_images.SplitImages(logger)
    obj.total_images_number = 3
    obj.current_image_number = 0
    progress = _noop_progress()

    obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

    # Las 2 sanas generan su TIFF; la corrupta no, y queda registrada.
    assert os.path.exists(os.path.join(str(input_folder), "DJI_0001_T.tiff"))
    assert os.path.exists(os.path.join(str(input_folder), "DJI_0003_T.tiff"))
    assert not os.path.exists(os.path.join(str(input_folder), "DJI_0002_T.tiff"))
    assert obj.error_splitting_images >= 1


# --- 3. PARALELISMO real: N imágenes se procesan concurrentemente -----------
def test_convierte_en_paralelo(tmp_path, logger, make_dji_jpeg):
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    n_imgs = 4
    for i in range(n_imgs):
        make_dji_jpeg(str(input_folder / f"DJI_{i:04d}_T.JPG"))

    obj = split_images.SplitImages(logger)
    obj.total_images_number = n_imgs
    obj.current_image_number = 0
    obj.max_dji_workers = n_imgs  # debe existir y habilitar concurrencia

    barrier = threading.Barrier(n_imgs, timeout=5)

    def fake_convert(inp, out, image_name, *args, **kwargs):
        # Solo desbloquea si las n_imgs conversiones corren A LA VEZ.
        barrier.wait()
        return (os.path.join(inp, image_name),
                os.path.join(out, image_name.removesuffix(".JPG") + ".tiff"))

    obj.convert_dji_image_to_tif = fake_convert
    obj._run_exif_batch = lambda *a, **k: None
    progress = _noop_progress()

    # Con ejecución secuencial la barrier nunca se completa -> BrokenBarrierError.
    obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)


# --- 4. EQUIVALENCIA: paralelo produce los MISMOS TIFF que secuencial -------
def test_paralelo_identico_a_secuencial(tmp_path, logger, make_dji_jpeg, monkeypatch):
    import pipeline as split_images

    def _run(folder_name, workers):
        input_folder = tmp_path / folder_name
        input_folder.mkdir()
        names = [f"DJI_{i:04d}_T.JPG" for i in range(5)]
        paths = {n: make_dji_jpeg(str(input_folder / n)) for n in names}

        def fake_run(cmd, *args, **kwargs):
            for n in names:
                if n in cmd and cmd.strip().startswith('"dji'):
                    _make_raw(paths[n], input_folder, n)
                    break
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(split_images.subprocess, "run", fake_run)
        obj = split_images.SplitImages(logger)
        obj.total_images_number = len(names)
        obj.current_image_number = 0
        obj.max_dji_workers = workers
        progress = _noop_progress()
        obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

        out = {}
        for n in names:
            tiff = os.path.join(str(input_folder), n.removesuffix(".JPG") + ".tiff")
            with open(tiff, "rb") as f:
                out[n] = f.read()
        return out

    seq = _run("SEQ", 1)
    par = _run("PAR", 4)
    assert seq.keys() == par.keys()
    for n in seq:
        assert seq[n] == par[n], f"El TIFF de {n} difiere entre secuencial y paralelo."
