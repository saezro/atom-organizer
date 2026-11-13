"""Cierre y `done` con la auditoría de la revisión del índice (`ajustes`): mismas líneas que la
aplicación, `error` visible, y sin auditoría el cierre no emite nada nuevo."""
import logging

import pytest

from atom_core import indice_excel, phases
from atom_core import revision_indice as ri
from atom_core.manifiesto import Manifiesto
from tests.test_apply_rgb import _SignalFalsa
from tests.test_cierre_organizado import _cfg, _crear_fichero, _fila_rgb

EJEC = 7
APLICADA = {"id": "g1:0:fuera-de-ventana", "grupo_id": "g1", "n_fotos": 2, "fila": 0, "pb": "1",
            "vuelo": "1", "motivo": "fuera-de-ventana", "distancia_s": 45.0,
            "confianza": "alta", "explicacion": "a 45 s de la fila 0",
            "origen": "sin respuesta en 30 min"}
DUDOSA = {"id": "g2:1:fuera-de-ventana", "grupo_id": "g2", "n_fotos": 1, "fila": 1, "pb": "1",
          "vuelo": "2", "motivo": "fuera-de-ventana", "distancia_s": 200.0,
          "confianza": "media", "explicacion": "a 200 s de la fila 1"}
HUERFANA = {"grupo_id": "g3", "motivo": "sin-estadillo", "confianza": None,
            "explicacion": "sin recomendación", "n_fotos": 3}


class _Host:
    organizer_logger_obj = type("L", (), {"logger": logging.getLogger("t")})()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    destino = tmp_path / "out"
    fila = _fila_rgb(destino, "/o/a.JPG", "PB1", "V1")
    man = Manifiesto(tmp_path / "m.db")
    man.crear_esquema()
    man.insertar_muchas([fila])
    f = man.todas()[0]
    _crear_fichero(f["ruta_salida_original"])
    man.marcar_hecha(f["id"], "v")
    cfg = _cfg(destino)
    monkeypatch.setattr(indice_excel, "escribir_indice",
                        lambda *a, **k: str(destino / "indice.xlsx"))
    return man, cfg


def _cierre(man, cfg):
    host, cb = _Host(), _SignalFalsa()
    phases.PipelinePhasesMixin._fase_cierre(
        host, man, cfg, cb, _SignalFalsa(), _SignalFalsa(), EJEC, None, {}, 0.0, "completo", None)
    return host, "".join(map(str, getattr(cb, "lineas", getattr(cb, "mensajes", []))))


def _audit(resultado, modo, **extra):
    return {"inicio": "x", "fin": "y", "resultado": resultado, "modo": modo,
            "espera_s": 1800 if modo == "pausa" else 0, "aplicadas": [APLICADA],
            "dudosas": [DUDOSA, HUERFANA], **extra}


def test_desatendida_emite_las_lineas_de_la_aplicacion(entorno):
    man, cfg = entorno
    ri.guardar_auditoria(man, EJEC, _audit("desatendida", "pausa"))
    host, salida = _cierre(man, cfg)
    assert ri._linea_aplicada(APLICADA, "sin respuesta en 30 min") in salida
    assert ri._aviso(DUDOSA, DUDOSA["explicacion"]) in salida
    assert ri._aviso_huerfanas("g3", "sin-estadillo", 3) in salida
    assert host.revision_indice["resultado"] == "desatendida"


def test_inmediata_emite_las_lineas_de_la_aplicacion(entorno):
    man, cfg = entorno
    ri.guardar_auditoria(man, EJEC, _audit("inmediata", "inmediato", aplicadas=[
        {**APLICADA, "origen": "aplicada al momento, sin canal de respuesta"}]))
    host, salida = _cierre(man, cfg)
    assert ri._linea_aplicada(
        {**APLICADA, "origen": "aplicada al momento, sin canal de respuesta"},
        "aplicada al momento, sin canal de respuesta") in salida
    assert ri._aviso(DUDOSA, DUDOSA["explicacion"]) in salida
    assert host.revision_indice["resultado"] == "inmediata"


def test_error_es_visible_en_el_cierre(entorno):
    man, cfg = entorno
    ri.guardar_auditoria(man, EJEC, _audit(
        "error", "pausa", error="RuntimeError: se rompió", errores=["continuar (con_alta): X: y"]))
    host, salida = _cierre(man, cfg)
    assert "ERROR: la revisión del índice terminó con un error: RuntimeError: se rompió" in salida
    assert "petición fallida: continuar (con_alta): X: y" in salida
    assert host.revision_indice["resultado"] == "error"


def test_sin_auditoria_no_emite_nada_nuevo(entorno):
    man, cfg = entorno
    host, salida = _cierre(man, cfg)
    assert host.revision_indice is None
    assert "Revisión del índice" not in salida and "SIN_ORDENAR" not in salida
    assert ri.lineas_auditoria(None) == []


def test_sin_pendientes_no_emite_lineas(entorno):
    man, cfg = entorno
    ri.guardar_auditoria(man, EJEC, {"inicio": "x", "fin": "y", "resultado": "sin_pendientes",
                                     "modo": "pausa", "espera_s": 1800, "aplicadas": [],
                                     "dudosas": []})
    _host, salida = _cierre(man, cfg)
    assert "Revisión del índice" not in salida


class _HostFalso:
    def __init__(self, auditoria):
        self._auditoria = auditoria

    def correr(self, cfg, pcb, pbar, psum):
        if self._auditoria is not None:
            self.revision_indice = self._auditoria      # lo que deja `_fase_cierre`


@pytest.mark.parametrize("auditoria", [{"resultado": "desatendida", "aplicadas": []}, None])
def test_done_lleva_el_bloque_y_none_sin_auditoria(monkeypatch, auditoria):
    from atom_core import organize
    from utils import RenameImagesConfig

    monkeypatch.setitem(organize._TASKS, "cierre_test", ("correr", RenameImagesConfig))
    monkeypatch.setattr(organize, "HeadlessHost", lambda: _HostFalso(auditoria))
    eventos = []
    organize.run_task("cierre_test", {}, lambda k, p=None: eventos.append((k, p)))
    dones = [p for k, p in eventos if k == "done"]
    assert len(dones) == 1 and dones[0]["revision_indice"] == auditoria


def test_avisos_de_la_auditoria_no_terminan_sin_problemas(entorno):
    man, cfg = entorno
    ri.guardar_auditoria(man, EJEC, _audit("desatendida", "pausa"))
    _host, salida = _cierre(man, cfg)
    assert "Organizado completado sin problemas." not in salida
    assert "Organizado completado con 2 aviso(s)." in salida     # dudosa + huérfana


def test_error_de_la_auditoria_cuenta_como_error_del_cierre(entorno):
    import json
    from atom_core.apply import STATS_ERRORES_PREFIX
    man, cfg = entorno
    ri.guardar_auditoria(man, EJEC, _audit("error", "pausa", error="RuntimeError: x"))
    host, cb = _Host(), _SignalFalsa()
    phases.PipelinePhasesMixin._fase_cierre(
        host, man, cfg, cb, _SignalFalsa(), _SignalFalsa(), EJEC, None, {}, 0.0, "completo", None)
    salida = "".join(map(str, cb.mensajes))
    marcador = [m for m in cb.mensajes if str(m).strip().startswith(STATS_ERRORES_PREFIX)]
    datos = json.loads(str(marcador[-1]).strip()[len(STATS_ERRORES_PREFIX):])
    assert datos["n_fallos"] == 1 and datos["cierre"] is True
    assert any("RuntimeError: x" in d for d in datos["detalle_errores"])
    assert "Organizado completado" not in salida
    assert "HA HABIDO ERRORES: la revisión del índice" in salida


def test_sin_auditoria_el_cierre_sigue_sin_problemas(entorno):
    man, cfg = entorno
    _host, salida = _cierre(man, cfg)
    assert "Organizado completado sin problemas." in salida


def test_origen_registrado_se_lee_y_si_falta_se_dice(entorno):
    man, cfg = entorno
    sin_origen = {k: v for k, v in APLICADA.items() if k != "origen"}
    sin_origen_man = {"grupo_id": "g9", "n_fotos": 1, "fila": 2, "quien": None}
    ri.guardar_auditoria(man, EJEC, _audit("respondida", "pausa", aplicadas=[
        {**APLICADA, "origen": "ana@pc"}, sin_origen, sin_origen_man]))
    _host, salida = _cierre(man, cfg)
    assert ri._linea_aplicada(APLICADA, "ana@pc") in salida
    assert ri._linea_aplicada(APLICADA, "origen no registrado") in salida
    assert "a la fila 2 [origen no registrado]" in salida
    assert "respondida]" not in salida

