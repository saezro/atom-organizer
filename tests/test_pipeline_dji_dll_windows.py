"""
Tests de la rama Windows de `pipeline.py` que decide entre `dji_irp_windows`
(DLL en proceso) y `dji_irp.exe` (subprocess), ver `_dji_dll_ok` (~línea 3612).

Sigue el patrón de `test_dji_conversor_invocacion.py`: `pipeline._is_windows`
monkeypatcheado a True y `subprocess.run` sustituido por un doble que captura
la invocación, para comprobar sin un .exe real cuál de las dos vías se usó.
"""
import os
import struct
import subprocess
import types

import pytest

import dji_irp_windows


def _sink_progress(sink):
    def _emit(*a, **k):
        if a and isinstance(a[0], str):
            sink.append(a[0])
    return types.SimpleNamespace(emit=_emit)


def _preparar(tmp_path, make_dji_jpeg, monkeypatch, rc_exe=0, salida_exe=""):
    """Monta una térmica de prueba (64x48, igual que `make_dji_jpeg`) y un
    `dji_irp.exe` falso (vía `subprocess.run`) con el rc pedido."""
    import pipeline

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    image_name = "DJI_0001_T.JPG"
    make_dji_jpeg(str(input_folder / image_name))

    dron_dir = tmp_path / "programas_externos" / "DJI"
    dron_dir.mkdir(parents=True)
    dji_utility = str(dron_dir / "dji_irp.exe")

    monkeypatch.setattr(pipeline, "_is_windows", lambda: True)

    llamadas = []

    def fake_run(cmd, *args, **kwargs):
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, rc_exe, stdout="", stderr=salida_exe)

    monkeypatch.setattr(subprocess, "run", fake_run)

    return input_folder, image_name, dji_utility, llamadas, dron_dir


def _fue_invocado_el_exe(llamadas, dji_utility):
    """`dji_irp.exe` se invoca con una cmdline (string) que incluye su ruta
    entre comillas; el exiftool falso no lleva `dji_utility` en su cmd."""
    return any(dji_utility in (cmd if isinstance(cmd, str) else str(cmd)) for cmd in llamadas)


def test_dji_dll_ok_salta_dji_irp_exe(tmp_path, logger, make_dji_jpeg, monkeypatch):
    import pipeline

    input_folder, image_name, dji_utility, llamadas, _dir = _preparar(
        tmp_path, make_dji_jpeg, monkeypatch)

    # 64x48 == tamaño del JPG de `make_dji_jpeg`: así `resolucion_desde_raw`
    # deduce la resolución térmica sin tocar la lista de resoluciones DJI conocidas.
    ancho, alto = 64, 48
    n = ancho * alto

    def fake_measure(image_path, raw_out, humidity, emissivity, sdk_dir):
        with open(raw_out, "wb") as fh:
            fh.write(struct.pack("<{0}f".format(n), *([1.5] * n)))

    monkeypatch.setattr(dji_irp_windows, "enabled", lambda: True)
    monkeypatch.setattr(dji_irp_windows, "measure", fake_measure)

    obj = pipeline.SplitImages(logger)
    mensajes = []
    progress = _sink_progress(mensajes)
    obj.convert_dji_image_to_tif(
        str(input_folder), str(input_folder), image_name, "exiftool",
        dji_utility, progress, progress)

    assert not _fue_invocado_el_exe(llamadas, dji_utility), (
        "con la DLL en proceso funcionando no debe lanzarse dji_irp.exe")
    # El .raw se procesa y se borra al terminar; si algo hubiera fallado antes
    # de llegar ahí, seguiría presente.
    assert not os.path.exists(os.path.join(str(input_folder), image_name + ".raw"))


def test_dji_dll_falla_cae_a_dji_irp_exe_con_warning(tmp_path, logger, make_dji_jpeg, monkeypatch):
    import pipeline

    input_folder, image_name, dji_utility, llamadas, _dir = _preparar(
        tmp_path, make_dji_jpeg, monkeypatch, rc_exe=0)

    def fake_measure_rota(image_path, raw_out, humidity, emissivity, sdk_dir):
        raise RuntimeError("create rc=-16 [img=DJI_0001_T.JPG bytes=123]")

    monkeypatch.setattr(dji_irp_windows, "enabled", lambda: True)
    monkeypatch.setattr(dji_irp_windows, "measure", fake_measure_rota)

    avisos = []
    obj = pipeline.SplitImages(logger)
    monkeypatch.setattr(
        obj.organizer_logger.logger, "warning",
        lambda msg, *a, **k: avisos.append(msg % a if a else msg))

    mensajes = []
    progress = _sink_progress(mensajes)
    obj.convert_dji_image_to_tif(
        str(input_folder), str(input_folder), image_name, "exiftool",
        dji_utility, progress, progress)

    assert _fue_invocado_el_exe(llamadas, dji_utility), (
        "si la DLL en proceso falla, debe caer a dji_irp.exe")
    assert any("dji_irp_windows" in a and "ha fallado" in a for a in avisos), (
        "el fallo de la DLL debe quedar avisado (nivel warning) antes del fallback")
