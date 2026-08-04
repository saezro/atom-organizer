"""Rotación AUTO del TIFF: el paso 6 consume el criterio que decidió el paso 5.

La decisión de giro NO se recalcula en la conversión a TIFF: el paso de miniaturas
la escribe en `MINIATURAS/<PBX_VXX>_miniaturas/<PBX_VXX>_Videofiles.csv` (columna
`Degree`) y `convert_dji_image_to_tif(auto_rotate=True)` la relee de ahí. Ese
traspaso entre pasos, que es por FICHERO, no tenía ni un solo test: se probaba que
el paso 5 escribe el CSV y que el paso 6 rota con los flags manuales, pero nunca
que el paso 6 lea bien lo que el paso 5 escribió.

Casos cubiertos:
  - Degree=270 y Degree=90 -> el TIFF sale rotado en el sentido correcto.
  - Degree=0 -> sin rotar.
  - CSV con solo la cabecera (el paso 5 no decidió ángulo) -> sin rotar y SIN
    reventar: antes `df["Degree"][0]` lanzaba KeyError, que el `except
    FileNotFoundError` no captura, y tumbaba la conversión de todo el vuelo.
  - CSV ausente -> sin rotar, avisando.
"""
import os
import struct
import subprocess
import types

import numpy as np
import pytest
from PIL import Image as PILImage


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def _monta_vuelo(tmp_path, make_dji_jpeg, contenido_csv):
    """Crea <destino>/TERMICA/PB24/PB24_V1/DJI_0001_T.JPG y, si procede, el CSV de
    criterio en <destino>/MINIATURAS/PB24_V1_miniaturas/. Devuelve (carpeta, jpg)."""
    vuelo = tmp_path / "TERMICA" / "PB24" / "PB24_V1"
    vuelo.mkdir(parents=True)
    image_path = make_dji_jpeg(str(vuelo / "DJI_0001_T.JPG"))

    if contenido_csv is not None:
        miniaturas = tmp_path / "MINIATURAS" / "PB24_V1_miniaturas"
        miniaturas.mkdir(parents=True)
        (miniaturas / "PB24_V1_Videofiles.csv").write_text(contenido_csv, encoding="utf-8")

    return vuelo, image_path


def _convierte(monkeypatch, logger, vuelo, image_path):
    """Corre la conversión con auto_rotate y devuelve (array_esperado_sin_rotar, tiff)."""
    import pipeline as split_images

    w, h = PILImage.open(image_path).size
    valores = [float(i) for i in range(h * w)]
    esperado = np.array(valores, dtype=np.float64).reshape(h, w)

    llamadas = []

    def fake_run(cmd, *args, **kwargs):
        llamadas.append(cmd)
        if len(llamadas) == 1:  # la utilidad DJI: deja el .raw radiométrico
            with open(os.path.join(str(vuelo), "DJI_0001_T.JPG.raw"), "wb") as f:
                f.write(struct.pack(f"{h * w}f", *valores))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(split_images.subprocess, "run", fake_run)

    obj = split_images.SplitImages(logger)
    progress = _noop_progress()
    obj.convert_dji_image_to_tif(
        str(vuelo), str(vuelo), "DJI_0001_T.JPG",
        "exiftool", "dji_utility", progress, progress,
        auto_rotate=True,
    )

    tiff = os.path.join(str(vuelo), "DJI_0001_T.tiff")
    assert os.path.exists(tiff), "No se generó el TIFF."
    return esperado, np.array(PILImage.open(tiff))


_CABECERA = "New Name,Original Name,Degree\n"


@pytest.mark.parametrize("degree,k,axes", [
    (270, 1, (0, 1)),   # antihorario
    (90, 1, (1, 0)),    # horario
])
def test_auto_rotate_aplica_el_degree_del_csv(tmp_path, logger, make_dji_jpeg, monkeypatch, degree, k, axes):
    csv = _CABECERA + f"PB24_V1_0001.JPG,DJI_0001_T.JPG,{degree}\n"
    vuelo, image_path = _monta_vuelo(tmp_path, make_dji_jpeg, csv)
    esperado, obtenido = _convierte(monkeypatch, logger, vuelo, image_path)
    np.testing.assert_array_equal(
        obtenido, np.rot90(esperado, k, axes),
        err_msg=f"Con Degree={degree} en el CSV el TIFF debería salir rotado en ese sentido.",
    )


def test_auto_rotate_degree_cero_no_rota(tmp_path, logger, make_dji_jpeg, monkeypatch):
    csv = _CABECERA + "PB24_V1_0001.JPG,DJI_0001_T.JPG,0\n"
    vuelo, image_path = _monta_vuelo(tmp_path, make_dji_jpeg, csv)
    esperado, obtenido = _convierte(monkeypatch, logger, vuelo, image_path)
    np.testing.assert_array_equal(obtenido, esperado)


def test_auto_rotate_csv_sin_filas_no_revienta(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """El paso 5 pudo no decidir ángulo ('demasiadas imágenes que no rotan igual'):
    deja el CSV con la cabecera y nada más. Eso no puede tumbar la conversión."""
    vuelo, image_path = _monta_vuelo(tmp_path, make_dji_jpeg, _CABECERA)
    esperado, obtenido = _convierte(monkeypatch, logger, vuelo, image_path)
    np.testing.assert_array_equal(obtenido, esperado)


def test_auto_rotate_sin_csv_no_rota_y_no_revienta(tmp_path, logger, make_dji_jpeg, monkeypatch):
    vuelo, image_path = _monta_vuelo(tmp_path, make_dji_jpeg, None)
    esperado, obtenido = _convierte(monkeypatch, logger, vuelo, image_path)
    np.testing.assert_array_equal(obtenido, esperado)
