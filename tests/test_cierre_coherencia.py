"""Coherencia de cierre: imágenes == tiff == filas meta/location por vuelo."""
import os

from atom_core.cierre import (PREFIJO_COHERENCIA, PREFIJO_ERROR, calcular_recuentos,
                              recuentos_por_vuelo, verificar)
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from tests.test_cierre_organizado import _cfg, _crear_fichero, _fila_rgb, _fila_termica


def _f(tipo, pb="1", vuelo="1", *, tiff=False, crop=False, estado="hecho", unassigned=False):
    return {"tipo": tipo, "pb": pb, "vuelo": vuelo, "estado": estado, "unassigned": unassigned,
            "ruta_salida_tiff": "x.tiff" if tiff else None,
            "ruta_salida_crop": "x_CROP.JPG" if crop else None}


def _vuelo(n_t, n_w, **kw):
    return ([_f("TERMICA", tiff=kw.get("tiff", True)) for _ in range(n_t)]
            + [_f("RGB", crop=kw.get("crop", True)) for _ in range(n_w)])


def test_ok_cuando_todo_cuadra():
    r = recuentos_por_vuelo(_vuelo(81, 81), {("1", "1"): {"meta": 81, "location": 81}},
                            convert_to_tif=True, cropping_rgb=True)[("1", "1")]
    assert r["estado"] == "OK" and r["motivo"] == ""
    assert (r["n_T"], r["n_tiff"], r["n_W"], r["n_crop"], r["filas_meta"], r["filas_location"]) == (81, 81, 81, 81, 81, 81)


def test_filas_meta_menos_que_termicas_es_error_con_numeros():
    r = recuentos_por_vuelo(_vuelo(81, 81), {("1", "1"): {"meta": 79, "location": 79}},
                            convert_to_tif=True, cropping_rgb=True)[("1", "1")]
    assert r["estado"] == "ERROR"
    assert "T 81" in r["motivo"] and "meta 79" in r["motivo"] and "location 79" in r["motivo"]


def test_falta_csv_con_termicas_es_error():
    r = recuentos_por_vuelo(_vuelo(5, 5), {}, convert_to_tif=True, cropping_rgb=True)[("1", "1")]
    assert r["estado"] == "ERROR" and "meta 0" in r["motivo"]


def test_tiff_faltante_es_error_solo_si_convert_to_tif():
    filas = _vuelo(3, 0, tiff=False)
    csv = {("1", "1"): {"meta": 3, "location": 0}}
    assert recuentos_por_vuelo(filas, csv, convert_to_tif=True)[("1", "1")]["estado"] == "ERROR"
    assert recuentos_por_vuelo(filas, csv, convert_to_tif=False)[("1", "1")]["estado"] == "OK"


def test_crop_faltante_es_error_solo_si_cropping_rgb():
    filas = _vuelo(0, 3, crop=False)
    csv = {("1", "1"): {"meta": 0, "location": 3}}
    assert recuentos_por_vuelo(filas, csv, cropping_rgb=True)[("1", "1")]["estado"] == "ERROR"
    assert recuentos_por_vuelo(filas, csv, cropping_rgb=False)[("1", "1")]["estado"] == "OK"


def test_vuelo_solo_rgb_compara_location_con_w_y_no_exige_meta():
    r = recuentos_por_vuelo(_vuelo(0, 4), {("1", "1"): {"meta": 0, "location": 4}},
                            cropping_rgb=True)[("1", "1")]
    assert r["estado"] == "OK"
    r = recuentos_por_vuelo(_vuelo(0, 4), {("1", "1"): {"meta": 0, "location": 3}},
                            cropping_rgb=True)[("1", "1")]
    assert r["estado"] == "ERROR"


def test_vuelo_solo_termico_no_exige_location():
    r = recuentos_por_vuelo(_vuelo(4, 0), {("1", "1"): {"meta": 4, "location": 0}},
                            convert_to_tif=True)[("1", "1")]
    assert r["estado"] == "OK"


def test_meta_distinta_de_location_en_vuelo_mixto_es_error():
    r = recuentos_por_vuelo(_vuelo(4, 4), {("1", "1"): {"meta": 4, "location": 3}})[("1", "1")]
    assert r["estado"] == "ERROR" and "location" in r["motivo"]


def test_sin_filas_csv_none_no_comprueba_csv():
    r = recuentos_por_vuelo(_vuelo(4, 4), None)[("1", "1")]
    assert r["estado"] == "OK" and r["filas_meta"] is None and r["filas_location"] is None


def test_filas_fallidas_pendientes_y_unassigned_no_cuentan_ni_crean_vuelo():
    filas = [_f("TERMICA", estado="fallido"), _f("TERMICA", estado="pendiente"),
             _f("TERMICA", unassigned=True), _f("TERMICA", pb=None, vuelo=None)]
    assert recuentos_por_vuelo(filas, {}) == {}


def test_rgb_extra_queda_fuera_de_la_regla():
    filas = _vuelo(0, 2) + [_f("RGB_Extra", crop=False)]
    r = recuentos_por_vuelo(filas, {("1", "1"): {"meta": 0, "location": 2}}, cropping_rgb=True)[("1", "1")]
    assert r["estado"] == "OK" and r["n_W"] == 2


def test_generales_calcula_recuentos_pero_queda_excluido():
    filas = [_f("TERMICA", pb="GENERALES", vuelo="AT141A", tiff=True),
             _f("RGB", pb="GENERALES", vuelo="AT141A", crop=True)]
    r = recuentos_por_vuelo(filas, {("GENERALES", "AT141A"): {"meta": 1, "location": 1}},
                            convert_to_tif=True, cropping_rgb=True)[("GENERALES", "AT141A")]
    assert r["estado"] == "EXCLUIDO" and r["motivo"] == "FOTOS_GENERALES fuera de la regla"
    assert (r["n_T"], r["n_W"], r["filas_meta"]) == (1, 1, 1)


def test_generales_incoherente_no_da_error_en_verificar(tmp_path):
    destino = tmp_path / "destino"
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_muchas([_fila_termica(destino, "/o/g.JPG", "generales", "AT141A", angulo_giro=0,
                                     ruta_salida_tiff=str(destino / "g.tiff"))])
    for fila in m.todas():
        _crear_fichero(fila["ruta_salida_original"])
        _crear_fichero(fila["ruta_salida_tiff"])
        m.marcar_hecha(fila["id"], verificacion="ok")
    cfg = _cfg(destino, convert_to_tif=True, gen_meta_location=True)
    rec = calcular_recuentos(m, cfg, {("generales", "AT141A"): {"meta": 0, "location": 0}})
    assert rec[("generales", "AT141A")]["estado"] == "EXCLUIDO"
    problemas = verificar(m, cfg, recuentos_csv={("generales", "AT141A"): {"meta": 0, "location": 0}})
    assert not [p for p in problemas if PREFIJO_COHERENCIA in p]
    m.cerrar()


def test_verificar_anade_linea_error_por_vuelo_incoherente(tmp_path):
    destino = tmp_path / "destino"
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_muchas([
        _fila_termica(destino, f"/o/{i}.JPG", "1", "1", angulo_giro=0,
                      ruta_salida_tiff=str(destino / f"{i}.tiff")) for i in range(3)])
    for fila in m.todas():
        _crear_fichero(fila["ruta_salida_original"])
        _crear_fichero(fila["ruta_salida_tiff"])
        m.marcar_hecha(fila["id"], verificacion="ok")
    cfg = _cfg(destino, convert_to_tif=True, gen_meta_location=True)

    problemas = verificar(m, cfg, recuentos_csv={("1", "1"): {"meta": 2, "location": 0}})

    lineas = [p for p in problemas if p.startswith(PREFIJO_ERROR + PREFIJO_COHERENCIA)]
    assert len(lineas) == 1 and "PB1_V1" in lineas[0] and "T 3" in lineas[0] and "meta 2" in lineas[0]
    m.cerrar()


def test_verificar_sin_recuentos_csv_no_anade_linea_de_coherencia(tmp_path):
    destino = tmp_path / "destino"
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_muchas([_fila_termica(destino, "/o/a.JPG", "1", "1", angulo_giro=0,
                                     ruta_salida_tiff=str(destino / "a.tiff"))])
    for fila in m.todas():
        _crear_fichero(fila["ruta_salida_original"])
        _crear_fichero(fila["ruta_salida_tiff"])
        m.marcar_hecha(fila["id"], verificacion="ok")
    problemas = verificar(m, _cfg(destino, convert_to_tif=True))
    assert not [p for p in problemas if PREFIJO_COHERENCIA in p]
    assert calcular_recuentos(m, _cfg(destino, convert_to_tif=True), None)[("1", "1")]["estado"] == "OK"
    m.cerrar()
