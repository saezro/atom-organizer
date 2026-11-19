"""Recomendaciones de colocación con confianza para las fotos sin colocar (lógica pura).

Criterio literal del spec §7 (alta confianza): vecino a <= `alta_s`, el otro a >= `otro_min_s`
o >= `factor_otro` veces la distancia, fila no de generales, giro sin contradecir FlightYaw y
foto sin otra recomendación a otra fila. Par partido: pareja de la misma toma colocada por el
índice (no a mano). `recomendar` solo lee: nunca escribe en el manifiesto.
"""
import datetime as dt
import os

import pytest

from atom_core import indice as ind
from atom_core.recomendaciones_sin_colocar import (
    Umbrales, recomendar, umbrales_desde_entorno)
from tests.test_sin_colocar import ESTADILLO, VENTANAS, _t, tanda  # noqa: F401 (fixture)


def _ventanas(estadillo):
    resultado = {}
    for _pb, _vuelo, fecha, ini, fin in estadillo:
        h = lambda s: dt.datetime(2024, 6, 1, *[int(x) for x in s.split(":")])  # noqa: E731
        resultado[(fecha, ini, fin)] = (h(ini), h(fin))
    return resultado


def _recomendaciones(tanda, umbrales=None):
    man = tanda.manifiesto()
    try:
        ejecucion = man.grupos_sin_colocar_raw()[0]["ejecucion"]
        return recomendar(man, tanda.destino, ejecucion, umbrales or Umbrales())
    finally:
        man.cerrar()


def _indexar(tanda, fotos, estadillo=ESTADILLO):
    tanda.indexar(fotos, estadillo=estadillo, ventanas=_ventanas(estadillo))


def test_vecino_a_45s_y_otro_a_20min_es_alta(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 45))])
    [r] = _recomendaciones(tanda)
    assert (r["motivo"], r["confianza"], r["fila"], r["pb"], r["vuelo"]) == ("vecino", "alta", 0, "1", "1")
    assert r["distancia_s"] == 45.0
    assert r["id"] == f"{r['grupo_id']}:0:vecino" and len(r["claves"]) == 1
    assert r["explicacion"] == "vecino PB1/V1 a 45 s; el siguiente a 49 min"


def test_vecino_a_200s_es_media(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 13, 20))])
    [r] = _recomendaciones(tanda)
    assert (r["confianza"], r["distancia_s"]) == ("media", 200.0)


def test_ambos_vecinos_dentro_del_umbral_es_baja(tanda):
    estadillo = [ESTADILLO[0], ("1", "2", "2024:06:01", "10:12:00", "10:20:00")]
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 11, 0))], estadillo)   # 60 s y 60 s
    [r] = _recomendaciones(tanda)
    assert r["confianza"] == "baja"


def test_otro_vecino_a_5x_la_distancia_basta_para_alta(tanda):
    estadillo = [ESTADILLO[0], ("1", "2", "2024:06:01", "10:13:01", "10:20:00")]
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 30))], estadillo)   # 30 s y 151 s
    [r] = _recomendaciones(tanda)
    assert (r["confianza"], r["distancia_s"]) == ("alta", 30.0)
    # 150 s = 5 x 30 s tambien basta (>=), 149 s no.
    estadillo = [ESTADILLO[0], ("1", "2", "2024:06:01", "10:12:59", "10:20:00")]
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 30))], estadillo)   # 149 s
    [r] = _recomendaciones(tanda)
    assert r["confianza"] == "media"


def test_fila_de_generales_nunca_alta(tanda):
    estadillo = ESTADILLO + [("GENERALES", "1", "2024:06:01", "13:00:00", "13:10:00")]
    _indexar(tanda, [("DJI_0001_D.JPG", _t(12, 59, 30))], estadillo)   # 30 s de GENERALES
    [r] = _recomendaciones(tanda)
    assert r["pb"] == "GENERALES" and r["confianza"] != "alta"


@pytest.mark.parametrize("motivo", ["sin-fecha-exif", "dia-sin-estadillo",
                                    "estadillo-sin-fecha-legible"])
def test_motivos_sin_fecha_y_dia_sin_estadillo_no_recomiendan_alta(tanda, motivo):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 45))])
    man = tanda.manifiesto()
    try:   # el motivo manda aunque el grupo conservara vecinos
        grupo = man.grupos_sin_colocar_raw()[0]
        grupo["motivo"] = motivo
        man._conexion().execute("UPDATE grupos_sin_colocar SET datos = ? WHERE grupo_id = ?",
                                (__import__("json").dumps(grupo), grupo["grupo_id"]))
        man._conexion().commit()
    finally:
        man.cerrar()
    assert all(r["confianza"] != "alta" for r in _recomendaciones(tanda))


def test_motivos_reales_sin_vecinos_no_recomiendan_alta(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 20, 0, dia=2)), ("DJI_0002_D.JPG", None)])
    assert all(r["confianza"] != "alta" for r in _recomendaciones(tanda))


def _indexar_par(tanda, estadillo=ESTADILLO, z=_t(10, 10, 3), t=_t(10, 9, 59), extra=()):
    """La T se indexa en una tanda y la Z (misma toma, <= 5 s, misma carpeta) en otra
    posterior: el índice no las junta (cada tanda hereda solo dentro de sí misma), así que
    la Z queda fuera de ventana con su pareja ya colocada por el índice. El índice lee ahora
    los metadatos reales del fichero (Rust): la T de la tanda anterior ya no está en el origen
    (como en una tanda real, ya procesada), si no la Z la heredaría dentro de la misma tanda.
    La fila de la T persiste en el manifiesto."""
    _indexar(tanda, [("DJI_20240601100959_0001_T.JPG", t)], estadillo)
    os.remove(tanda.rutas["DJI_20240601100959_0001_T.JPG"])
    _indexar(tanda, [("DJI_20240601101003_0001_Z.JPG", z), *extra], estadillo)


def test_par_partido_recomienda_el_vuelo_de_la_pareja(tanda):
    _indexar_par(tanda)
    [r] = _recomendaciones(tanda)
    assert (r["motivo"], r["confianza"], r["fila"]) == ("par-partido", "alta", 0)
    assert r["claves"][0].startswith("DJI_20240601101003_0001_Z.JPG|")
    assert r["explicacion"] == "par partido: su pareja de la misma toma está en PB1/V1"
    assert r["id"] == f"{r['grupo_id']}:0:par-partido"


def test_par_partido_con_pareja_a_mano_no_es_alta(tanda):
    _indexar_par(tanda)
    man = tanda.manifiesto()
    try:
        grupo = man.grupos_sin_colocar_raw()[0]
        clave_t = next(c for c, f in man.filas_por_claves(
            [f["clave"] for f in man.todas()]).items() if f["nombre_original"].endswith("_T.JPG"))
        man.registrar_asignaciones([clave_t], grupo["grupo_id"], 0, None, "x", "2024-06-01T12:00:00+02:00")
    finally:
        man.cerrar()
    recs = _recomendaciones(tanda)
    assert recs and all(r["confianza"] != "alta" for r in recs)


def test_giro_contradictorio_baja_a_baja(tanda, monkeypatch):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 45))])

    def contradice(*_a, **_k):
        raise ind.ErrorGiroContradictorio("el consenso difiere de FlightYaw")

    monkeypatch.setattr(ind, "_validar_giro_contra_flight_yaw", contradice)
    [r] = _recomendaciones(tanda)
    assert r["confianza"] == "baja" and "FlightYaw" in r["explicacion"]


def test_conflicto_dos_filas_misma_foto_nunca_alta(tanda):
    # La pareja T esta en la fila 1 (10:10:13) pero el vecino mas cercano de la Z es la fila 0.
    estadillo = [("1", "1", "2024:06:01", "10:00:00", "10:10:09"),
                 ("1", "2", "2024:06:01", "10:10:12", "10:20:00")]
    _indexar_par(tanda, estadillo, z=_t(10, 10, 10), t=_t(10, 10, 13))
    recs = _recomendaciones(tanda)
    assert {r["fila"] for r in recs} == {0, 1} and {r["motivo"] for r in recs} == {"vecino", "par-partido"}
    assert all(r["confianza"] == "baja" for r in recs)
    assert all("otra fila" in r["explicacion"] for r in recs)


def test_orden_estable_por_grupo_y_fila(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 45)), ("DJI_0002_D.JPG", _t(10, 59, 0)),
                     ("DJI_0003_D.JPG", _t(12, 0, 0))])
    recs = _recomendaciones(tanda)
    assert [(r["grupo_id"], r["fila"]) for r in recs] == sorted((r["grupo_id"], r["fila"]) for r in recs)
    assert recs == _recomendaciones(tanda)


def test_recomendar_no_escribe(tanda):
    _indexar_par(tanda, extra=[("DJI_0009_D.JPG", _t(10, 10, 45))])

    def contar():
        man = tanda.manifiesto()
        try:
            c = man._conexion()
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("vuelos", "asignaciones_manuales", "imagenes", "grupos_sin_colocar")}
        finally:
            man.cerrar()

    antes = contar()
    assert _recomendaciones(tanda)
    assert contar() == antes


def test_umbrales_por_defecto_y_entorno():
    assert Umbrales() == Umbrales(alta_s=120.0, otro_min_s=600.0, factor_otro=5.0)
    assert umbrales_desde_entorno({}) == Umbrales()
    assert umbrales_desde_entorno({"ATOM_REVISION_ALTA_S": "60"}).alta_s == 60.0
    for malo in ("abc", "0", "-5"):
        with pytest.raises(ValueError):
            umbrales_desde_entorno({"ATOM_REVISION_ALTA_S": malo})


def _par(pb="1", vuelo="1", ini="10:00:00", fin="10:20:00"):
    return {"pb": pb, "vuelo": vuelo, "inicio": f"2024-06-01T{ini}", "fin": f"2024-06-01T{fin}"}


def test_pareja_ambigua_no_adivina_fila():
    from atom_core.recomendaciones_sin_colocar import _ficha_de_pareja
    fichas = [{"fila": 0, **_par(ini="10:00:00", fin="10:05:00")},
              {"fila": 1, **_par(ini="10:30:00", fin="10:40:00")}]
    pareja = {"pb": "1", "vuelo": "1", "timestamp_exif": "2024-06-01T10:20:00"}
    assert _ficha_de_pareja(fichas, pareja) is None
    pareja["timestamp_exif"] = "2024-06-01T10:31:00"
    assert _ficha_de_pareja(fichas, pareja)["fila"] == 1


def test_ficha_con_inicio_vacio_no_revienta():
    from atom_core.recomendaciones_sin_colocar import _ficha_de_pareja
    fichas = [{"fila": 0, **_par(), "inicio": ""},
              {"fila": 1, **_par(ini="10:30:00", fin="10:40:00"), "fin": "basura"}]
    pareja = {"pb": "1", "vuelo": "1", "timestamp_exif": "2024-06-01T10:31:00"}
    assert _ficha_de_pareja(fichas, pareja) is None


def test_sin_otro_vecino_es_alta(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 10, 45))], [ESTADILLO[0]])
    [r] = _recomendaciones(tanda)
    assert r["confianza"] == "alta" and r["explicacion"].endswith("no hay otro vecino")
