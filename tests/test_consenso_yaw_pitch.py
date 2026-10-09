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
    assert angulo == 0 and any("ERROR" in m and "sin sentido de giro" in m for m in msgs)
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
    angulo, msgs = _consenso(tmp_path, [(50, 89.0, -66.0), (50, -89.0, -66.0)])
    assert angulo == 0 and any("ERROR" in m and "sin sentido de giro" in m for m in msgs)


def _consenso_prev(tmp_path, grupos, previos):
    msgs, cb = _emisor()
    res = _consenso_de_angulo_por_vuelo(
        _asignaciones(grupos), None, _cfg(), str(tmp_path), cb, vuelos_previos=previos)
    return res[("1", "2", None)]


def test_vuelo_previo_version_vieja_o_ausente_se_recalcula(tmp_path):
    for version in ("0-vieja", None):
        previos = {("1", "2", None): {"angulo": 0, "version_algoritmo": version}}
        assert _consenso_prev(tmp_path, [(10, 89.0, -66.0)], previos) == 90


def test_vuelo_previo_version_actual_se_reutiliza_sin_csv(tmp_path):
    previos = {("1", "2", None): {"angulo": 270,
                                  "version_algoritmo": indice_mod.VERSION_ALGORITMO_GIRO}}
    assert _consenso_prev(tmp_path, [(10, 89.0, -66.0)], previos) == 270
    assert not (tmp_path / "CSVs").exists()


def test_vuelo_90_pct_rellena_votos_y_fila_vuelos(tmp_path):
    msgs, cb = _emisor()
    reparto, votos = {}, {}
    asig = _asignaciones([(90, 89.0, -66.0), (10, -89.0, -66.0)])
    res = _consenso_de_angulo_por_vuelo(asig, None, _cfg(), str(tmp_path), cb,
                                        reparto_out=reparto, votos_out=votos)
    assert res[("1", "2", None)] == 90
    r = reparto[("1", "2", None)]
    assert (r["estado"], r["n_total"], r["pct_90"], r["pct_270"], r["sufijo"]) == ("ok", 100, 90.0, 10.0, "")
    assert sorted(votos.values()).count(90) == 90 and sorted(votos.values()).count(270) == 10


def test_vuelo_60_40_sin_consenso_en_fila_vuelos(tmp_path):
    msgs, cb = _emisor()
    reparto = {}
    res = _consenso_de_angulo_por_vuelo(_asignaciones([(60, 89.0, -66.0), (40, -89.0, -66.0)]),
                                        None, _cfg(), str(tmp_path), cb, reparto_out=reparto)
    assert res[("1", "2", None)] == 0
    r = reparto[("1", "2", None)]
    assert r["estado"] == "sin_consenso" and r["angulo"] == 0 and r["motivo"]


def test_el_indice_no_escribe_sidecar_en_el_destino(tmp_path):
    # El índice solo decide: el sidecar `.giro.json` lo escribe el cierre junto al CSV.
    _consenso(tmp_path, [(95, 89.0, -66.0), (5, -89.0, -66.0)])
    assert not (tmp_path / "CSVs").exists()


# --- Regla por vuelo: >=80% un sentido manda, si no ERROR sin girar (2026-10-09) ---
class _PipeFake:
    def __init__(self):
        self.errores = []
        self.organizer_logger = types.SimpleNamespace(
            logger=types.SimpleNamespace(error=lambda m: self.errores.append(("log", m))))

    def _register_image_error(self, path):
        self.errores.append(("reg", path))


def test_vuelo_90_pct_mismo_sentido_gira_todo(tmp_path):
    pipe = _PipeFake()
    angulo, _ = _consenso(tmp_path, [(90, 89.0, -66.0), (10, -89.0, -66.0)], pipeline=pipe)
    assert angulo == 90 and pipe.errores == []


def test_vuelo_60_40_no_gira_y_registra_error(tmp_path):
    pipe = _PipeFake()
    reparto = {}
    angulo, msgs = _consenso(tmp_path, [(60, 89.0, -66.0), (40, -89.0, -66.0)],
                             pipeline=pipe, reparto=reparto)
    assert angulo == 0
    assert {t for t, _ in pipe.errores} == {"log", "reg"}
    assert any(m.startswith("\nERROR") and "60.0%" in m and "40.0%" in m for m in msgs)
    assert reparto[("1", "2", None)]["error_sin_consenso"] is True


def test_vuelo_100_pct_normal(tmp_path):
    pipe = _PipeFake()
    angulo, _ = _consenso(tmp_path, [(20, -89.0, -66.0)], pipeline=pipe)
    assert angulo == 270 and pipe.errores == []


def test_reproceso_con_vuelo_previo_sin_consenso_reemite_el_error(tmp_path):
    pipe = _PipeFake()
    msgs, cb = _emisor()
    previos = {("1", "2", None): {
        "angulo": 0, "version_algoritmo": indice_mod.VERSION_ALGORITMO_GIRO,
        "estado": "sin_consenso", "n_total": 100, "n_90": 60, "n_270": 40, "n_0": 0}}
    res = _consenso_de_angulo_por_vuelo(
        _asignaciones([(10, 89.0, -66.0)]), pipe, _cfg(), str(tmp_path), cb,
        vuelos_previos=previos)
    assert res[("1", "2", None)] == 0
    assert {t for t, _ in pipe.errores} == {"log", "reg"}
    assert any(m.startswith("\nERROR") and "60.0%" in m and "40.0%" in m for m in msgs)


def test_reproceso_con_vuelo_previo_ok_no_emite_error(tmp_path):
    pipe = _PipeFake()
    _msgs, cb = _emisor()
    previos = {("1", "2", None): {
        "angulo": 90, "version_algoritmo": indice_mod.VERSION_ALGORITMO_GIRO,
        "estado": "ok", "n_total": 100, "n_90": 95, "n_270": 5, "n_0": 0}}
    _consenso_de_angulo_por_vuelo(_asignaciones([(10, 89.0, -66.0)]), pipe, _cfg(),
                                  str(tmp_path), cb, vuelos_previos=previos)
    assert pipe.errores == []
