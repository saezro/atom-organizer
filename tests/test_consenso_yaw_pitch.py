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


# --- FlightYawDegree manda sobre el GimbalYaw (decisión Rodrigo 2026-10-08) ---
def _asig_fy(grupos):
    """grupos: (n, gimbal_yaw, pitch, flight_yaw)."""
    ventana = {"pb": "1", "vuelo": "2"}
    filas = []
    for n, yaw, pitch, fy in grupos:
        for i in range(n):
            dato = _MetadatosImagen(ruta=f"/x/{yaw}_{fy}_{i}.JPG", nombre=f"{yaw}_{i}.JPG",
                                    timestamp=None, modelo=None, yaw=yaw, gps=None,
                                    pitch=pitch, flight_yaw=fy)
            filas.append((dato, ventana))
    return filas


def _angulo_fy(tmp_path, grupos):
    res = _consenso_de_angulo_por_vuelo(
        _asig_fy(grupos), None, _cfg(), str(tmp_path),
        types.SimpleNamespace(emit=lambda *a, **k: None))
    return res[("1", "2", None)]


def test_flight_yaw_menos_90_gana_a_gimbal_rgb_mas_90_y_t_menos_90(tmp_path):
    assert _angulo_fy(tmp_path, [(947, 90.0, -87.0, -90.0), (946, -90.0, -93.0, -90.0)]) == 270


def test_flight_yaw_mas_90(tmp_path):
    assert _angulo_fy(tmp_path, [(10, -90.0, -87.0, 90.0), (10, 90.0, -93.0, 90.0)]) == 90


def test_flight_yaw_89_pitch_normal_sin_cambio(tmp_path):
    assert _angulo_fy(tmp_path, [(10, -89.0, -66.0, -89.0)]) == 270
    assert _angulo_fy(tmp_path, [(10, 89.0, -66.0, 89.0)]) == 90


def test_sin_flight_yaw_comportamiento_actual(tmp_path):
    assert _angulo_fy(tmp_path, [(372, 89.0, -85.0, None), (373, -91.0, -95.0, None)]) == 90


def test_flight_yaw_horizontal_es_cero(tmp_path):
    assert _angulo_fy(tmp_path, [(10, 90.0, -66.0, -18.0)]) == 0
    assert _angulo_fy(tmp_path, [(10, 90.0, -66.0, 180.0)]) == 0


# --- Confianza del consenso, caché versionada y auditoría (2026-10-09) ---
import json
import os

from atom_core import indice as indice_mod


def _emisor():
    msgs = []
    return msgs, types.SimpleNamespace(emit=lambda m, *a, **k: msgs.append(m))


def _consenso(tmp_path, grupos, pipeline=None, reparto=None):
    msgs, cb = _emisor()
    kw = {} if reparto is None else {"reparto_out": reparto}
    res = _consenso_de_angulo_por_vuelo(
        _asignaciones(grupos), pipeline, _cfg(), str(tmp_path), cb, **kw)
    return res[("1", "2", None)], msgs


def test_reparto_60_40_avisa_con_bandas(tmp_path):
    reparto = {}
    angulo, msgs = _consenso(tmp_path, [(60, 89.0, -66.0), (40, -89.0, -66.0)], reparto=reparto)
    assert any("AVISO" in m and "consenso" in m.lower() for m in msgs)
    r = reparto[("1", "2", None)]
    assert r["angulo"] == angulo
    assert r["n_90"] == 60 and r["n_270"] == 40 and r["total"] == 100
    assert r["version_algoritmo"] == indice_mod.VERSION_ALGORITMO_GIRO


def test_reparto_95_5_sin_aviso(tmp_path):
    reparto = {}
    angulo, msgs = _consenso(tmp_path, [(95, 89.0, -66.0), (5, -89.0, -66.0)], reparto=reparto)
    assert angulo == 90
    assert not any("consenso" in m.lower() and "AVISO" in m for m in msgs)
    assert reparto[("1", "2", None)]["pct_ganadora"] == 95.0


def test_empate_avisa(tmp_path):
    _, msgs = _consenso(tmp_path, [(50, 89.0, -66.0), (50, -89.0, -66.0)])
    assert any("AVISO" in m and "consenso" in m.lower() for m in msgs)


class _PipeCriterio:
    def __init__(self):
        self.llamadas = 0

    def read_auto_rotate_degree(self, carpeta, cb):
        self.llamadas += 1
        return 0


def _crear_criterio(tmp_path, version):
    d = tmp_path / "CSVs" / "_criterio"
    d.mkdir(parents=True)
    (d / "PB1_V2_Videofiles.csv").write_text("New Name,Original Name,Degree\n")
    if version is not None:
        (d / "PB1_V2_Videofiles.giro.json").write_text(json.dumps({"version_algoritmo": version}))


def test_criterio_version_vieja_se_recalcula(tmp_path):
    _crear_criterio(tmp_path, "0-vieja")
    pipe = _PipeCriterio()
    angulo, _ = _consenso(tmp_path, [(10, 89.0, -66.0)], pipeline=pipe)
    assert angulo == 90 and pipe.llamadas == 0


def test_criterio_sin_version_se_recalcula(tmp_path):
    _crear_criterio(tmp_path, None)
    pipe = _PipeCriterio()
    angulo, _ = _consenso(tmp_path, [(10, 89.0, -66.0)], pipeline=pipe)
    assert angulo == 90 and pipe.llamadas == 0


def test_criterio_version_actual_se_reutiliza(tmp_path):
    _crear_criterio(tmp_path, indice_mod.VERSION_ALGORITMO_GIRO)
    pipe = _PipeCriterio()
    angulo, _ = _consenso(tmp_path, [(10, 89.0, -66.0)], pipeline=pipe)
    assert angulo == 0 and pipe.llamadas == 1


def test_el_indice_no_escribe_sidecar_en_el_destino(tmp_path):
    # El índice solo decide: el sidecar `.giro.json` lo escribe el cierre junto al CSV.
    _consenso(tmp_path, [(95, 89.0, -66.0), (5, -89.0, -66.0)])
    assert not (tmp_path / "CSVs").exists()
