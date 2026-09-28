"""Validación temprana del modelo de dron para el % de recorte automático.

Antes de este fix, un modelo EXIF sin entrada en `percentage_by_models`
reventaba con `KeyError` crudo DENTRO de `_construir_fila`
(`Pipeline.get_percentage_by_model`, pipeline.py:4328) — es decir, DESPUÉS
de que el pool de hilos ya hubiese leído el EXIF completo de todas las
imágenes (`construir_indice`, atom_core/indice.py). Estos tests sujetan que
ahora `construir_indice` valida los modelos justo tras leer los metadatos y
aborta con `ErrorModeloSinRecorte`, un mensaje claro, ANTES de montar
ninguna fila — nunca con un `KeyError`.

Mismos dobles y helpers que `tests/test_indice_organizado.py` (estilo de la
casa: dobles a mano, nunca `unittest.mock`).
"""
import datetime as dt

import pytest

from atom_core.indice import ErrorModeloSinRecorte, construir_indice
from tests.test_indice_organizado import (
    _PipelineDePrueba,
    _ExifDePrueba,
    _Signal,
    _cfg,
    _crear_imagen,
    _escribir_estadillo,
    _manifiesto,
)


def _preparar_estadillo_y_ventana(tmp_path):
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    ts = dt.datetime(2024, 6, 1, 10, 5, 0)
    return ventanas, ts


def test_modelo_conocido_no_revienta(tmp_path):
    """Modelo presente en `percentage_by_models`: el índice se construye sin
    excepción y la fila lleva el % esperado."""
    ventanas, ts = _preparar_estadillo_y_ventana(tmp_path)
    cfg = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=True)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    pipeline = _PipelineDePrueba(ventanas, pct_por_modelo={"M3T": 80})
    exif = _ExifDePrueba(timestamps={ruta: ts}, modelos={ruta: "M3T"})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["pct_recorte"] == 0.80
    manifiesto.cerrar()


def test_modelo_desconocido_falla_al_inicio_con_mensaje_claro(tmp_path):
    """Modelo EXIF ('H30T') ausente de `percentage_by_models`: debe abortar
    con `ErrorModeloSinRecorte` (nunca `KeyError`), con el nombre del modelo
    y un fichero de ejemplo en el mensaje, y SIN escribir ninguna fila en el
    manifiesto (falla antes de `insertar_o_reabrir`, igual que
    `ErrorColisionEstadillo`)."""
    ventanas, ts = _preparar_estadillo_y_ventana(tmp_path)
    cfg = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=True)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    # El diccionario de recortes solo conoce M3T, no H30T.
    pipeline = _PipelineDePrueba(ventanas, pct_por_modelo={"M3T": 80})
    exif = _ExifDePrueba(timestamps={ruta: ts}, modelos={ruta: "H30T"})
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorModeloSinRecorte) as excinfo:
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    mensaje = str(excinfo.value)
    assert "H30T" in mensaje
    assert "DJI_0001_D.JPG" in mensaje
    assert manifiesto.todas() == []
    manifiesto.cerrar()


def test_mezcla_conocido_y_desconocido_falla_sin_escribir_nada(tmp_path):
    """Una imagen con modelo conocido y otra con modelo desconocido en el
    mismo run: debe abortar entero (nada a medias) señalando el modelo que
    falta, no colar la conocida y reventar luego con la otra."""
    ventanas, ts = _preparar_estadillo_y_ventana(tmp_path)
    cfg = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=True)
    ruta_ok = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    ruta_mala = _crear_imagen(cfg.input_folder, "DJI_0002_D.JPG")
    pipeline = _PipelineDePrueba(ventanas, pct_por_modelo={"M3T": 80})
    exif = _ExifDePrueba(
        timestamps={ruta_ok: ts, ruta_mala: ts},
        modelos={ruta_ok: "M3T", ruta_mala: "H20T"},
    )
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorModeloSinRecorte) as excinfo:
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    assert "H20T" in str(excinfo.value)
    assert manifiesto.todas() == []
    manifiesto.cerrar()


def test_recorte_desactivado_no_valida_modelo(tmp_path):
    """Con `cropping_rgb=False` (recorte apagado) un modelo desconocido no
    debe importar: no hay recorte que configurar."""
    ventanas, ts = _preparar_estadillo_y_ventana(tmp_path)
    cfg = _cfg(tmp_path, cropping_rgb=False)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    pipeline = _PipelineDePrueba(ventanas, pct_por_modelo={"M3T": 80})
    exif = _ExifDePrueba(timestamps={ruta: ts}, modelos={ruta: "H30T"})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    assert len(manifiesto.todas()) == 1
    manifiesto.cerrar()
