"""Invariantes de la lectura del `.raw` en la conversión a TIFF (v3.4.14).

La lectura del `.raw` que escribe el SDK dejó de ser
`np.array(struct.unpack("<N>f", data))` y pasó a `np.frombuffer(...).astype(np.float64)`:
128x más rápido y, sobre todo, sin retener el GIL 20 ms por imagen dentro de un pool
de hilos.

Lo que estos tests protegen es lo que ese cambio puede romper en silencio:

  - El TIFF entregado sigue siendo **float32**. Ojo al matiz, que es contraintuitivo:
    la lectura produce float64, pero PIL guarda en modo 'F' y eso ES float32, así que
    el fichero que recibe el cliente siempre fue de 32 bits. El test fija el formato
    del ENTREGABLE, que es lo que no puede cambiar; comparar solo valores con
    `np.array_equal` no lo detectaría, porque ignora el dtype.
  - Los valores son los mismos que daba `struct.unpack`, incluidos negativos y
    decimales (temperaturas bajo cero).

Y el tercero cubre la memoización del criterio de giro, del mismo commit: el CSV se
lee UNA vez por carpeta de vuelo, no una vez por imagen.
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


def _convierte_con_raw(monkeypatch, logger, tmp_path, make_dji_jpeg, valores):
    """Monta un vuelo con un .raw de los `valores` dados y devuelve el array del TIFF."""
    import pipeline as split_images

    vuelo = tmp_path / "TERMICA" / "PB24" / "PB24_V1"
    vuelo.mkdir(parents=True)
    image_path = make_dji_jpeg(str(vuelo / "DJI_0001_T.JPG"))
    w, h = PILImage.open(image_path).size
    assert len(valores) == w * h, "el .raw sintético debe cubrir la imagen entera"

    def fake_run(cmd, *args, **kwargs):
        with open(os.path.join(str(vuelo), "DJI_0001_T.JPG.raw"), "wb") as f:
            f.write(struct.pack(f"{len(valores)}f", *valores))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(split_images.subprocess, "run", fake_run)

    obj = split_images.SplitImages(logger)
    progress = _noop_progress()
    obj.convert_dji_image_to_tif(
        str(vuelo), str(vuelo), "DJI_0001_T.JPG",
        "exiftool", "dji_utility", progress, progress,
    )
    tiff = os.path.join(str(vuelo), "DJI_0001_T.tiff")
    assert os.path.exists(tiff), "No se generó el TIFF."
    return np.array(PILImage.open(tiff)), (w, h)


def test_el_tiff_entregado_sigue_siendo_float32(monkeypatch, logger, tmp_path, make_dji_jpeg):
    """El formato del .tiff que recibe el cliente no puede cambiar: es float32.

    PIL guarda el array en modo 'F' (32 bits) por mucho que se le pase un float64, así
    que este es el dtype real del entregable. Si alguien cambia la lectura o el save de
    forma que el fichero pase a 64 bits, todos los .tiff dejarían de casar con los ya
    entregados y aquí saltaría.
    """
    w, h = PILImage.open(make_dji_jpeg(str(tmp_path / "sonda.JPG"))).size
    valores = [float(i) for i in range(w * h)]
    arr, _ = _convierte_con_raw(monkeypatch, logger, tmp_path, make_dji_jpeg, valores)
    assert arr.dtype == np.float32, (
        "El TIFF entregado ha pasado a {0}; era float32.".format(arr.dtype))


def test_los_valores_coinciden_con_struct_unpack(monkeypatch, logger, tmp_path, make_dji_jpeg):
    """np.frombuffer debe dar exactamente lo mismo que el struct.unpack anterior,
    incluidos negativos y decimales (temperaturas bajo cero)."""
    w, h = PILImage.open(make_dji_jpeg(str(tmp_path / "sonda.JPG"))).size
    n = w * h
    # Rango realista de temperaturas, con negativos y fracciones no exactas en binario.
    valores = [(-20.0 + (i % 700) * 0.1) for i in range(n)]

    arr, (w, h) = _convierte_con_raw(monkeypatch, logger, tmp_path, make_dji_jpeg, valores)

    # Referencia: exactamente el camino viejo, sobre los mismos bytes. Se compara ya
    # en float32 porque es lo que PIL escribe en el fichero (modo 'F'), que es el
    # entregable; el float64 solo existe en memoria durante la conversión.
    data = struct.pack(f"{n}f", *valores)
    esperado = np.array(struct.unpack(f"{n}f", data)).reshape(h, w).astype(np.float32)

    assert np.array_equal(arr, esperado), "Los valores del TIFF ya no son los del camino anterior."


def test_el_criterio_de_giro_se_lee_una_vez_por_carpeta(logger, tmp_path, monkeypatch):
    """`read_auto_rotate_degree` memoiza por carpeta: el CSV se lee una vez por vuelo,
    no una vez por imagen. Antes eran hasta 4 os.path.exists + un pd.read_csv por cada
    térmica del vuelo."""
    import pipeline as split_images

    vuelo = tmp_path / "TERMICA" / "PB24" / "PB24_V1"
    vuelo.mkdir(parents=True)
    csvs = tmp_path / "CSVs" / split_images.utils.CRITERIO_DIRNAME
    csvs.mkdir(parents=True)
    (csvs / "PB24_V1_Videofiles.csv").write_text("Degree\n90\n", encoding="utf-8")

    obj = split_images.SplitImages(logger)
    progress = _noop_progress()

    lecturas = []
    real = obj._leer_criterio_giro

    def contando(input_folder, progress_callback):
        lecturas.append(input_folder)
        return real(input_folder, progress_callback)

    monkeypatch.setattr(obj, "_leer_criterio_giro", contando)

    grados = [obj.read_auto_rotate_degree(str(vuelo), progress) for _ in range(25)]

    assert grados == [90] * 25, "El criterio memoizado debe devolver siempre el mismo ángulo."
    assert len(lecturas) == 1, (
        "El CSV de criterio se ha leído {0} veces para la misma carpeta; debe leerse una "
        "sola vez por vuelo.".format(len(lecturas)))

    # Tras reset_variables el caché se vacía: entre corridas el usuario puede haber
    # cambiado el criterio de rotación de esa misma carpeta.
    obj.reset_variables()
    obj.read_auto_rotate_degree(str(vuelo), progress)
    assert len(lecturas) == 2, "reset_variables debe invalidar el criterio memoizado."
