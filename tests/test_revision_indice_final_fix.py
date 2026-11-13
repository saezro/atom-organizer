"""Arreglos de la revisión final (calendario de revisión del índice): fallos parciales,
errores de la pausa en la auditoría, `motivo_fuera`/`sufijo` en el bloque, espera persistida."""
import json
import time
from types import SimpleNamespace

import pytest

import app_webview as aw
from atom_core import cancelacion
from atom_core import indice as ind
from atom_core import revision_indice as ri
from atom_core.recomendaciones_sin_colocar import recomendar, umbrales_desde_entorno
from tests.test_recomendaciones_sin_colocar import _indexar
from tests.test_revision_indice_pausa import _esperar_estado, _hilo
from tests.test_sin_colocar import _t, tanda  # noqa: F401 (fixture)

FOTOS = [("DJI_0001_D.JPG", _t(10, 10, 45)), ("DJI_0002_D.JPG", _t(10, 40, 0))]


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.delenv("ATOM_REVISION_INDICE", raising=False)
    monkeypatch.delenv("ATOM_REVISION_ESPERA_MIN", raising=False)
    cancelacion.limpiar()
    ri.desarmar()
    yield
    cancelacion.limpiar()
    ri.desarmar()


def _preparar(tanda):
    _indexar(tanda, FOTOS)
    man = tanda.manifiesto()
    return man, man.grupos_sin_colocar_raw()[0]["ejecucion"]


def _claves_de_dos_grupos(man):
    grupos = man.grupos_sin_colocar_raw()
    assert len(grupos) == 2
    return [g["claves"][0] for g in grupos]


def _romper_el_segundo(monkeypatch):
    real = ri.sin_colocar.asignar_fotos
    llamadas = []

    def _asignar(*a, **k):
        llamadas.append(a[3])
        if len(llamadas) == 2:
            raise RuntimeError("disco lleno")
        return real(*a, **k)
    monkeypatch.setattr(ri.sin_colocar, "asignar_fotos", _asignar)


# --- I4: fallo parcial multi-grupo --------------------------------------------------------

def test_asignar_fallo_parcial_conserva_lo_escrito_en_parcial(tanda, monkeypatch):
    man, _e = _preparar(tanda)
    try:
        claves = _claves_de_dos_grupos(man)
        _romper_el_segundo(monkeypatch)
        parcial: list = []
        with pytest.raises(ri.sin_colocar.ErrorSinColocar) as exc:
            ri._asignar(man, tanda.destino, {"claves": claves, "fila": 1, "quien": "ana@pc"},
                        parcial=parcial)
        assert "1 de 2 grupos ya quedaron asignados" in str(exc.value) and "disco lleno" in str(exc.value)
        assert len(parcial) == 1 and parcial[0]["n_fotos"] == 1
        assert list(man.asignaciones_manuales()) == [claves[0]]
    finally:
        man.cerrar()


def test_pausa_asignar_parcial_entra_en_la_auditoria(tanda, monkeypatch):
    man, ejecucion = _preparar(tanda)
    claves = _claves_de_dos_grupos(man)
    _romper_el_segundo(monkeypatch)
    ri.armar(espera_s=30)
    respuesta = {}

    def _usuario():
        _esperar_estado()
        try:
            ri.enviar({"op": "asignar", "claves": claves, "fila": 1, "quien": "ana@pc"}).result(10)
        except Exception as error:  # noqa: BLE001
            respuesta["error"] = error
        ri.enviar({"op": "continuar", "modo": "tal_cual"}).result(10)

    hilo = _hilo(_usuario)
    try:
        res = ri.pausar_si_armada(man, SimpleNamespace(output_folder=tanda.destino), ejecucion,
                                  lambda _t: None, lambda _e: None)
        hilo.join(10)
        assert "disco lleno" in str(respuesta["error"])
        assert len(res["aplicadas"]) == 1
        audit = ri.leer_auditoria(man, ejecucion)
        assert len(audit["aplicadas"]) == 1 and audit["aplicadas"][0]["origen"] == "ana@pc"
    finally:
        man.cerrar()


def test_aceptar_fallo_parcial_conserva_lo_escrito(tanda, monkeypatch):
    man, ejecucion = _preparar(tanda)
    try:
        ids = [r["id"] for r in recomendar(man, tanda.destino, ejecucion, umbrales_desde_entorno())]
        assert len(ids) == 2
        _romper_el_segundo(monkeypatch)
        parcial: list = []
        with pytest.raises(ri.sin_colocar.ErrorSinColocar) as exc:
            ri._aceptar(man, tanda.destino, ejecucion, {"ids": ids, "quien": "ana@pc"},
                        parcial=parcial)
        assert "1 de 2 recomendaciones ya quedaron aplicadas" in str(exc.value)
        assert len(parcial) == 1
    finally:
        man.cerrar()


# --- I5: ERROR de aplicar_seguras en la pausa llega a la auditoría y al cierre --------------

def test_timeout_con_error_de_aplicacion_va_a_errores_de_la_auditoria(tanda, monkeypatch):
    man, ejecucion = _preparar(tanda)

    def _roto(*_a, **_k):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(ri.sin_colocar, "asignar_fotos", _roto)
    ri.armar(espera_s=0.2)
    lineas = []
    try:
        ri.pausar_si_armada(man, SimpleNamespace(output_folder=tanda.destino), ejecucion,
                            lineas.append, lambda _e: None)
        audit = ri.leer_auditoria(man, ejecucion)
    finally:
        man.cerrar()
    assert len(audit["errores"]) == 1
    assert audit["errores"][0].startswith("ERROR:") and "disco lleno" in audit["errores"][0]
    cierre = ri.lineas_auditoria(audit)
    errores = [l for l in cierre if l.startswith("ERROR:")]
    assert errores == [audit["errores"][0]]      # tal cual, sin «petición fallida» ni duplicada


def test_continuar_con_alta_con_error_de_aplicacion_va_a_errores(tanda, monkeypatch):
    man, ejecucion = _preparar(tanda)

    def _roto(*_a, **_k):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(ri.sin_colocar, "asignar_fotos", _roto)
    ri.armar(espera_s=30)

    def _usuario():
        _esperar_estado()
        ri.enviar({"op": "continuar", "modo": "con_alta"}).result(10)

    hilo = _hilo(_usuario)
    try:
        ri.pausar_si_armada(man, SimpleNamespace(output_folder=tanda.destino), ejecucion,
                            lambda _t: None, lambda _e: None)
        hilo.join(10)
        audit = ri.leer_auditoria(man, ejecucion)
    finally:
        man.cerrar()
    assert any("disco lleno" in e and e.startswith("ERROR:") for e in audit["errores"])


def test_peticion_fallida_sigue_explicandose():
    lineas = ri.lineas_auditoria({"resultado": "respondida", "errores": ["continuar (x): boom"]})
    assert lineas == ["ERROR: la revisión del índice: petición fallida: continuar (x): boom"]


# --- I6 / I7: motivo_fuera y sufijo en la recomendación y en el bloque ---------------------

def test_recomendacion_lleva_motivo_fuera_y_sufijo(tanda):
    man, ejecucion = _preparar(tanda)
    try:
        grupos = {g["grupo_id"]: g["motivo"] for g in man.grupos_sin_colocar_raw()}
        recs = recomendar(man, tanda.destino, ejecucion, umbrales_desde_entorno())
    finally:
        man.cerrar()
    assert recs
    for r in recs:
        assert r["motivo"] in ("vecino", "par-partido")
        assert r["motivo_fuera"] == grupos[r["grupo_id"]]
        assert "sufijo" in r


def test_fila_revision_publica_motivo_fuera_y_sufijo():
    rec = {"id": "g:0:vecino", "grupo_id": "g", "n_fotos": 2, "fila": 0, "pb": "3", "vuelo": "2",
           "sufijo": "a", "motivo": "vecino", "motivo_fuera": "fuera-de-ventana",
           "distancia_s": 45.0, "confianza": "media", "explicacion": "x"}
    fila = ind._fila_revision(rec)
    assert fila["motivo_fuera"] == "fuera-de-ventana" and fila["sufijo"] == "a"
    # sin el dato -> null, no inventado
    assert ind._fila_revision({"id": "x"})["motivo_fuera"] is None


# --- espera persistida (runs remotos) ------------------------------------------------------

def test_espera_guardada_ida_y_vuelta_y_validacion(tmp_path):
    ruta = str(tmp_path / "revision.json")
    assert ri.leer_espera_min(ruta) is None
    assert ri.guardar_espera_min(12, ruta) == 12.0
    assert ri.leer_espera_min(ruta) == 12.0
    for malo in (0, -3, "abc", None, True, 1441):
        with pytest.raises(ValueError):
            ri.guardar_espera_min(malo, ruta)
    assert ri.leer_espera_min(ruta) == 12.0          # lo malo no pisó lo bueno
    (tmp_path / "revision.json").write_text("{roto", encoding="utf-8")
    with pytest.raises(ValueError, match="ilegible"):
        ri.leer_espera_min(ruta)


def test_run_remoto_usa_la_espera_guardada(tmp_path, monkeypatch):
    ruta = str(tmp_path / "revision.json")
    monkeypatch.setattr(ri, "ruta_ajustes", lambda: ruta)
    assert aw.Api._revision_espera_s({}) == 1800.0
    ri.guardar_espera_min(7, ruta)
    assert aw.Api._revision_espera_s({}) == 7 * 60.0                      # control remoto: sin params
    assert aw.Api._revision_espera_s({"revision_indice": {"espera_min": 3}}) == 180.0  # params manda
    monkeypatch.setenv("ATOM_REVISION_ESPERA_MIN", "5")
    assert aw.Api._revision_espera_s({}) == 7 * 60.0                      # el ajuste manda sobre el entorno


def test_espera_guardada_ilegible_es_error_visible(tmp_path, monkeypatch):
    ruta = tmp_path / "revision.json"
    ruta.write_text('{"espera_min": 0}', encoding="utf-8")
    monkeypatch.setattr(ri, "ruta_ajustes", lambda: str(ruta))
    with pytest.raises(ValueError, match="ilegible"):
        aw.Api._revision_espera_s({})


def test_api_guardar_espera(tmp_path, monkeypatch):
    ruta = str(tmp_path / "revision.json")
    monkeypatch.setattr(ri, "ruta_ajustes", lambda: ruta)
    api = aw.Api(broker=True)
    assert api.revision_indice_guardar_espera(15) == {"ok": True, "espera_min": 15.0}
    assert json.load(open(ruta, encoding="utf-8")) == {"espera_min": 15.0}
    r = api.revision_indice_guardar_espera(0)
    assert r["ok"] is False and "inválida" in r["error"]


# --- I2: el front solo muestra el ajuste si el backend dice que el flag está encendido ----

def test_estado_dice_si_la_revision_esta_disponible(monkeypatch):
    api = aw.Api(broker=True)
    assert api.revision_indice_estado()["disponible"] is False
    monkeypatch.setenv("ATOM_REVISION_INDICE", "1")
    assert api.revision_indice_estado()["disponible"] is True


# --- diferido: el estado lleva la espera real --------------------------------------------

def test_estado_incluye_espera_s_mientras_hay_pausa(tanda):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=42)
    visto = {}

    def _usuario():
        _esperar_estado()
        visto.update(ri.estado())
        ri.enviar({"op": "continuar", "modo": "tal_cual"}).result(10)

    hilo = _hilo(_usuario)
    try:
        ri.pausar_si_armada(man, SimpleNamespace(output_folder=tanda.destino), ejecucion,
                            lambda _t: None, lambda _e: None)
        hilo.join(10)
    finally:
        man.cerrar()
    assert visto["espera_s"] == 42.0
    assert "espera_s" not in ri.estado()
