"""Guarda bloqueante: el giro final de un vuelo no puede contradecir el
consenso de FlightYaw de sus imágenes (caso Willka, 6 vuelos 90 vs 270)."""
import types

import pytest

from atom_core import indice as indice_mod
from atom_core.indice import (ErrorGiroContradictorio, _MetadatosImagen,
                              _consenso_de_angulo_por_vuelo)


def _cfg(orientacion="Vertical", auto=True, rotate_90=False):
    return types.SimpleNamespace(
        orientacion=orientacion, choose_mode_auto=auto,
        gen_thumbnails_add_to_angle=0, gen_thumbnails_subs_to_angle=45,
        gen_thumbnails_max_error=20, include_v=True, gen_thumbnails_rotate_90=rotate_90)


def _asig(vuelos):
    """vuelos: {vuelo: [(n, gimbal_yaw, pitch, flight_yaw), ...]}"""
    filas = []
    for vuelo, grupos in vuelos.items():
        ventana = {"pb": "1", "vuelo": vuelo}
        for n, yaw, pitch, fy in grupos:
            for i in range(n):
                dato = _MetadatosImagen(
                    ruta=f"/x/{vuelo}_{yaw}_{fy}_{i}.JPG", nombre=f"{vuelo}_{i}.JPG",
                    timestamp=None, modelo=None, yaw=yaw, gps=None, pitch=pitch,
                    flight_yaw=fy)
                filas.append((dato, ventana))
    return filas


def _previo(vuelo, angulo):
    return {("1", vuelo, None): {
        "angulo": angulo, "version_algoritmo": indice_mod.VERSION_ALGORITMO_GIRO,
        "estado": "ok", "n_90": 0, "n_270": 0, "n_0": 0, "n_total": 0}}


def _run(vuelos, cfg=None, previos=None, tmp="/tmp"):
    return _consenso_de_angulo_por_vuelo(
        _asig(vuelos), None, cfg or _cfg(), tmp,
        types.SimpleNamespace(emit=lambda *a, **k: None), vuelos_previos=previos)


def test_angulo_previo_cacheado_contradice_flightyaw(tmp_path):
    with pytest.raises(ErrorGiroContradictorio) as exc:
        _run({"2": [(20, 88.0, -90.0, -90.0)]}, previos=_previo("2", 90), tmp=str(tmp_path))
    msg = str(exc.value)
    assert "90º" in msg and "270º" in msg and "20 imágenes" in msg and "100.0%" in msg


def test_consenso_coherente_no_lanza(tmp_path):
    res = _run({"2": [(20, 88.0, -90.0, -90.0)]}, tmp=str(tmp_path))
    assert res[("1", "2", None)] == 270
    res = _run({"2": [(20, 88.0, -90.0, -90.0)]}, previos=_previo("2", 270), tmp=str(tmp_path))
    assert res[("1", "2", None)] == 270


def test_sin_flight_yaw_no_lanza(tmp_path):
    res = _run({"2": [(20, 89.0, -66.0, None)]}, previos=_previo("2", 270), tmp=str(tmp_path))
    assert res[("1", "2", None)] == 270


def test_sin_consenso_flightyaw_no_lanza(tmp_path):
    res = _run({"2": [(10, 0.0, -90.0, 90.0), (10, 0.0, -90.0, -90.0)]},
               previos=_previo("2", 90), tmp=str(tmp_path))
    assert res[("1", "2", None)] == 90


def test_horizontal_ya_no_exime_y_manual_no_lanza(tmp_path):
    v = {"2": [(20, 88.0, -90.0, -90.0)]}
    with pytest.raises(ErrorGiroContradictorio):
        _run(v, cfg=_cfg(orientacion="Horizontal"), previos=_previo("2", 90),
             tmp=str(tmp_path))
    assert _run(v, cfg=_cfg(auto=False, rotate_90=True),
                tmp=str(tmp_path))[("1", "2", None)] == 90


def test_dos_vuelos_contradictorios_se_listan_ambos(tmp_path):
    v = {"2": [(20, 88.0, -90.0, -90.0)], "3": [(20, 88.0, -90.0, -90.0)],
         "4": [(20, 88.0, -90.0, -90.0)]}
    previos = {**_previo("2", 90), **_previo("3", 90), **_previo("4", 270)}
    with pytest.raises(ErrorGiroContradictorio) as exc:
        _run(v, previos=previos, tmp=str(tmp_path))
    msg = str(exc.value)
    assert "V2" in msg or "_2" in msg
    assert msg.count("ángulo asignado") == 2 and "2 vuelo(s)" in msg
