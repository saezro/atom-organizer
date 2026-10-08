"""Consenso de yaw del índice con térmica de DJI M30T pasada del nadir.

Caso real (Willka vuelo 1_2): RGB yaw +89 pitch -85 (372 imgs) y térmica yaw -91
pitch -95 (373 imgs). La térmica con GimbalPitch < -90 trae el yaw invertido ~180º
respecto al RGB con la misma orientación física; ganaba la térmica (por una
imagen) y el ángulo salía 270 en vez de 90. `_yaw_normalizado` suma 180 al yaw
cuando pitch < -90 antes de meterlo en el consenso. Sin pitch, yaw tal cual.
"""
import types

from atom_core.indice import _MetadatosImagen, _consenso_de_angulo_por_vuelo, _yaw_normalizado


def _cfg():
    return types.SimpleNamespace(
        orientacion="Vertical", choose_mode_auto=True,
        gen_thumbnails_add_to_angle=0, gen_thumbnails_subs_to_angle=45,
        gen_thumbnails_max_error=20, include_v=True, gen_thumbnails_rotate_90=False)


def _asignaciones(grupos):
    ventana = {"pb": "1", "vuelo": "2"}
    filas = []
    for n, yaw, pitch in grupos:
        for i in range(n):
            dato = _MetadatosImagen(ruta=f"/x/{yaw}_{i}.JPG", nombre=f"{yaw}_{i}.JPG",
                                    timestamp=None, modelo=None, yaw=yaw, gps=None,
                                    pitch=pitch)
            filas.append((dato, ventana))
    return filas


def _angulo(tmp_path, grupos):
    res = _consenso_de_angulo_por_vuelo(
        _asignaciones(grupos), None, _cfg(), str(tmp_path),
        types.SimpleNamespace(emit=lambda *a, **k: None))
    return res[("1", "2", None)]


def test_termica_pitch_menor_90_no_invierte_el_consenso(tmp_path):
    assert _angulo(tmp_path, [(372, 89.0, -85.0), (373, -91.0, -95.0)]) == 90


def test_sin_pitch_menor_90_todo_igual(tmp_path):
    assert _angulo(tmp_path, [(10, -89.0, -66.0)]) == 270


def test_sin_pitch_no_toca_el_yaw(tmp_path):
    assert _angulo(tmp_path, [(10, -89.0, None)]) == 270


def test_yaw_normalizado_rango():
    assert _yaw_normalizado(-91.0, -95.0) == 89.0
    assert _yaw_normalizado(100.0, -95.0) == -80.0
    assert _yaw_normalizado(-91.0, -90.0) == -91.0
    assert _yaw_normalizado(-91.0, None) == -91.0
