"""Coordinador `revision_indice`: flag, modo, aplicación de lo seguro y auditoría."""
import os

import pytest

from atom_core import revision_indice as ri
from atom_core import sin_colocar
from atom_core.manifiesto import Manifiesto
from tests.test_giro_manifiesto_gcs import CLAVE as CLAVE_GCS
from tests.test_giro_manifiesto_gcs import RAIZ as RAIZ_GCS
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)
from tests.test_recomendaciones_sin_colocar import _indexar, _ventanas
from tests.test_sin_colocar import ESTADILLO, _t, tanda  # noqa: F401 (fixture)

QUIEN = "auto:recomendacion-alta (aplicada al momento, sin canal de respuesta)@equipo"
# Alta (45 s de la fila 0, otro vecino a 49 min) y media (200 s de la fila 0 / otro grupo).
FOTOS_ALTA_Y_MEDIA = [("DJI_0001_D.JPG", _t(10, 10, 45)), ("DJI_0002_D.JPG", _t(10, 40, 0))]


def _emisor():
    lineas = []
    return lineas, lineas.append


def _ejecucion(man):
    return man.grupos_sin_colocar_raw()[0]["ejecucion"]


def test_flag_apagado_por_defecto():
    assert ri.activa({}) is False
    assert ri.activa({"ATOM_REVISION_INDICE": "1"}) is True
    assert ri.activa({"ATOM_REVISION_INDICE": "0"}) is False
    assert ri.activa({}, {"revision_indice": {"activa": True}}) is True
    assert ri.activa({"ATOM_REVISION_INDICE": "1"}, {"revision_indice": {"activa": False}}) is False
    assert ri.modo({}) == "apagado"
    assert ri.modo({"ATOM_REVISION_INDICE": "1"}) == "inmediato"


def test_modo_pausa_si_armada():
    assert ri.armada() is False
    ri.armar()
    try:
        assert ri.armada() is True
        assert ri.modo({}) == "pausa"
    finally:
        ri.desarmar()
    assert ri.armada() is False and ri.modo({}) == "apagado"


def test_aplicar_seguras_solo_alta_y_avisa_el_resto(tanda):
    _indexar(tanda, FOTOS_ALTA_Y_MEDIA)
    man = tanda.manifiesto()
    lineas, emit = _emisor()
    try:
        res = ri.aplicar_seguras(man, tanda.destino, _ejecucion(man), QUIEN, emit)
        assert len(res["aplicadas"]) == 1 and len(res["sin_aplicar"]) == 1
        assert all("claves" not in r and r["n_fotos"] == 1
                   for r in res["aplicadas"] + res["sin_aplicar"])
        assert res["aplicadas"][0]["confianza"] == "alta"
        manuales = man.asignaciones_manuales()
        assert len(manuales) == 1
        [(clave, a)] = manuales.items()
        assert a["quien"].startswith("auto:recomendacion-alta")
        assert man.filas_por_claves([clave])[clave]["unassigned"] == 0
    finally:
        man.cerrar()
    assert any(l.startswith("Revisión del índice · aplicada recomendación: 1 fotos → PB1/V1 fila 0")
               and l.endswith("[aplicada al momento, sin canal de respuesta]") for l in lineas)
    avisos = [l for l in lineas if l.startswith("AVISO:")]
    assert len(avisos) == 1 and "confianza media" in avisos[0]
    assert res["avisos"] == avisos


def test_sin_pendientes_no_toca_nada(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 5, 0))])   # dentro de ventana: nada pendiente
    man = tanda.manifiesto()
    lineas, emit = _emisor()
    try:
        res = ri.aplicar_seguras(man, tanda.destino, 1, QUIEN, emit)
        assert man.asignaciones_manuales() == {}
    finally:
        man.cerrar()
    assert res == {"aplicadas": [], "sin_aplicar": [], "avisos": []} and lineas == []


def test_grupo_pendiente_sin_recomendacion_avisa_y_no_muta(tanda, monkeypatch):
    _indexar(tanda, FOTOS_ALTA_Y_MEDIA)
    man = tanda.manifiesto()
    lineas, emit = _emisor()
    monkeypatch.setattr(ri, "recomendar", lambda *a, **k: [])
    try:
        res = ri.aplicar_seguras(man, tanda.destino, _ejecucion(man), QUIEN, emit)
        assert man.asignaciones_manuales() == {}
    finally:
        man.cerrar()
    avisos = [l for l in lineas if l.startswith("AVISO:")]
    assert avisos and all("sin recomendación" in a and "grupo " in a for a in avisos)
    assert res["aplicadas"] == [] and res["avisos"] == avisos
    assert sum(r["n_fotos"] for r in res["sin_aplicar"]) == 2


def test_origen_ilegible_en_quien_es_error(tanda):
    _indexar(tanda, FOTOS_ALTA_Y_MEDIA)
    man = tanda.manifiesto()
    try:
        with pytest.raises(ValueError):
            ri.aplicar_seguras(man, tanda.destino, _ejecucion(man), "rodrigo@pc", lambda _t: None)
        assert man.asignaciones_manuales() == {}
    finally:
        man.cerrar()


def test_aplicar_seguras_gcs_usa_el_manifiesto_del_run(tanda, bucket, monkeypatch):  # noqa: F811
    from atom_core import almacen as almacen_mod
    _indexar(tanda, FOTOS_ALTA_Y_MEDIA)
    almacen_mod.publicar_en(os.path.join(tanda.destino, ".organizado", "manifiesto.db"),
                            RAIZ_GCS + "/.organizado/manifiesto.db", if_generation_match=0)

    def _no(*_a, **_k):
        raise AssertionError("aplicar_seguras no debe abrir un segundo manifiesto")
    monkeypatch.setattr(sin_colocar, "_abrir", _no)
    man = tanda.manifiesto()    # el manifiesto del run (local)
    lineas, emit = _emisor()
    try:
        res = ri.aplicar_seguras(man, RAIZ_GCS, _ejecucion(man), QUIEN, emit)
        assert len(res["aplicadas"]) == 1
        [fila] = [f for f in man.todas() if not f["unassigned"]]
        assert fila["ruta_salida_original"].startswith(RAIZ_GCS + "/RGB/PB1/")
    finally:
        man.cerrar()
    assert bucket.generaciones[CLAVE_GCS] == 1   # no se subió nada por su cuenta


def test_auditoria_ida_y_vuelta(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 5, 0))])
    man = tanda.manifiesto()
    try:
        assert ri.leer_auditoria(man, 7) is None
        datos = {"inicio": "2026-11-13T10:00:00+01:00", "resultado": "inmediata", "modo": "inmediato",
                 "espera_s": 0, "aplicadas": [], "dudosas": []}
        ri.guardar_auditoria(man, 7, datos)
        assert man.leer_ajuste("revision_indice:7") is not None
        assert ri.leer_auditoria(man, 7) == datos
        assert ri.leer_auditoria(man, 8) is None
    finally:
        man.cerrar()
