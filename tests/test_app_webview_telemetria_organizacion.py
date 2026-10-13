"""Telemetría de la organización LOCAL (`_run_task_worker`) hacia `/organizer`.

Inicio (`etapa="organizacion"`), progreso/fase y fin ok/error/cancelado, todo
fail-open: la organización no puede romperse ni frenarse por la telemetría.
"""
from __future__ import annotations

import sys
import types

import pytest

import app_webview as aw
from atom_core.run_reporter import RunReporter


class _Rep:
    def __init__(self):
        self.llamadas = []
        self.activo = True
        self.ultimo_error = None

    def iniciar(self, **kw):
        self.llamadas.append(("iniciar", kw))

    def fase(self, **kw):
        self.llamadas.append(("fase", kw))

    def progreso(self, s):
        self.llamadas.append(("progreso", s))

    def fin(self, **kw):
        self.llamadas.append(("fin", kw))


def _api(monkeypatch, rep, organize_fn):
    api = aw.Api(broker=True)
    monkeypatch.setattr(api, "_reporter_actual", lambda tipo="subida": rep)
    monkeypatch.setattr(aw.precarga, "precargar_pandas", lambda: None)
    mod = types.ModuleType("atom_core.organize")
    mod.run_task = organize_fn
    monkeypatch.setitem(sys.modules, "atom_core.organize", mod)
    import atom_core
    monkeypatch.setattr(atom_core, "organize", mod, raising=False)
    return api


PARAMS = {"origen": "/x/PLANTA_B/", "inspeccion_id": 7}


def _kinds(rep):
    return [c[0] for c in rep.llamadas]


def test_ok_inicio_progreso_fin(monkeypatch):
    rep = _Rep()

    def fn(task, params, emit, adv):
        emit("phase", {"index": 1, "total": 3, "name": "Separar"})
        emit("stats", {"done": 5, "total": 10})
        emit("stats", {"done": 10, "total": 10, "final": True})
        emit("done", {"status": "ok"})

    api = _api(monkeypatch, rep, fn)
    api._run_task_worker("split_images", PARAMS, None)
    import time; time.sleep(0.1)  # hilo de la fase
    assert rep.llamadas[0] == ("iniciar", {"inspeccion": "PLANTA_B", "etapa": "organizacion",
                                             "inspeccion_id": 7})
    assert ("fase", {"index": 1, "total": 3, "nombre": "Separar"}) in rep.llamadas
    assert ("progreso", {"files_done": 10, "files_total": 10, "final": True}) in rep.llamadas
    assert rep.llamadas[-1] == ("fin", {"ok": True, "error": None}) or \
        ("fin", {"ok": True, "error": None}) in rep.llamadas
    assert [c for c in rep.llamadas if c[0] == "fin"] == [("fin", {"ok": True, "error": None})]


def test_error_y_excepcion_cierran_con_error(monkeypatch):
    rep = _Rep()

    def fn(task, params, emit, adv):
        raise RuntimeError("boom")

    api = _api(monkeypatch, rep, fn)
    api._run_task_worker("split_images", PARAMS, None)
    fins = [c[1] for c in rep.llamadas if c[0] == "fin"]
    assert fins == [{"ok": False, "error": "RuntimeError: boom"}]


def test_cancelado(monkeypatch):
    rep = _Rep()

    def fn(task, params, emit, adv):
        emit("done", {"status": "cancelled", "cancelled": True})

    api = _api(monkeypatch, rep, fn)
    api._run_task_worker("split_images", PARAMS, None)
    fins = [c[1] for c in rep.llamadas if c[0] == "fin"]
    assert fins == [{"ok": False, "error": "Cancelado por el usuario"}]


def test_done_con_errores_es_error(monkeypatch):
    rep = _Rep()
    api = _api(monkeypatch, rep,
               lambda t, p, e, a: e("done", {"status": "errors", "errors": 3}))
    api._run_task_worker("split_images", PARAMS, None)
    assert [c[1] for c in rep.llamadas if c[0] == "fin"] == [
        {"ok": False, "error": "3 imágenes con error"}]


def test_sin_login_no_hay_telemetria_y_organiza(monkeypatch):
    llamado = []
    api = _api(monkeypatch, None, lambda t, p, e, a: llamado.append(1))
    api._run_task_worker("split_images", PARAMS, None)
    assert llamado == [1]


def test_suite_no_acepta_alta_es_noop(monkeypatch):
    rep = _Rep()
    rep.activo = False
    api = _api(monkeypatch, rep, lambda t, p, e, a: e("done", {"status": "ok"}))
    api._run_task_worker("split_images", PARAMS, None)
    assert _kinds(rep) == ["iniciar"]


def test_otro_task_no_reporta(monkeypatch):
    rep = _Rep()
    api = _api(monkeypatch, rep, lambda t, p, e, a: e("done", {"status": "ok"}))
    api._run_task_worker("otra_cosa", PARAMS, None)
    assert rep.llamadas == []


def test_reporter_que_revienta_no_rompe_la_organizacion(monkeypatch):
    class Malo(_Rep):
        def iniciar(self, **kw):
            raise OSError("red")

    hecho = []
    api = _api(monkeypatch, Malo(), lambda t, p, e, a: (e("stats", {"done": 1, "total": 2}),
                                                         hecho.append(1)))
    api._run_task_worker("split_images", PARAMS, None)
    assert hecho == [1]


def test_reporter_real_fail_open_sin_red(monkeypatch):
    r = RunReporter(secreto="x", base="http://127.0.0.1:1", timeout=1)
    api = _api(monkeypatch, r, lambda t, p, e, a: e("done", {"status": "ok"}))
    api._run_task_worker("split_images", PARAMS, None)  # no lanza


def test_tipo_del_reporter_es_organizacion(monkeypatch):
    visto = []
    api = aw.Api(broker=True)
    monkeypatch.setattr(api, "_get_auth", lambda: types.SimpleNamespace(is_logged_in=lambda: True))
    monkeypatch.setattr(aw.RunReporter if hasattr(aw, "RunReporter") else RunReporter,
                        "iniciar", lambda self, **kw: visto.append(self._tipo), raising=False)
    api._telemetria_org_iniciar("split_images", PARAMS)
    assert visto == ["organizacion"]


def test_alta_rechazada_loguea_warning(monkeypatch, caplog):
    rep = _Rep()
    rep.activo = False
    rep.ultimo_error = "inspeccion_id-requerido"
    api = _api(monkeypatch, rep, lambda t, p, e, a: None)
    with caplog.at_level("WARNING"):
        assert api._telemetria_org_iniciar("split_images", {"origen": "/x/P"}) is None
    msgs = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert "inspeccion_id-requerido" in msgs and "sin inspeccion_id" in msgs


def test_inspeccion_id_resuelto_del_catalogo(monkeypatch):
    rep = _Rep()
    api = _api(monkeypatch, rep, lambda t, p, e, a: None)
    api._ids_inspeccion = {"PLANTA_B": 99}
    api._telemetria_org_iniciar("split_images", {"origen": "/x/PLANTA_B"})
    assert rep.llamadas[0][1]["inspeccion_id"] == 99


def test_inspeccion_id_sin_coincidencia_no_se_adivina(monkeypatch):
    rep = _Rep()
    api = _api(monkeypatch, rep, lambda t, p, e, a: None)
    api._ids_inspeccion = {"OTRA": 1}
    api._telemetria_org_iniciar("split_images", {"origen": "/x/PLANTA_B"})
    assert "inspeccion_id" not in rep.llamadas[0][1]


def test_reporter_real_captura_400(monkeypatch):
    r = RunReporter(secreto="x")
    monkeypatch.setattr(RunReporter, "_peticion",
                        lambda self, *a, **k: {"ok": False, "error": "inspeccion_id-requerido"})
    r.iniciar(inspeccion="X", etapa="organizacion")
    assert not r.activo and r.ultimo_error == "inspeccion_id-requerido"


def test_fin_de_telemetria_se_manda_tras_soltar_running(monkeypatch):
    rep = _Rep()
    estado = []
    orig = rep.fin
    api = _api(monkeypatch, rep, lambda t, p, e, a: e("done", {"status": "ok"}))
    rep.fin = lambda **kw: (estado.append(api._running), orig(**kw))
    api._running = True
    api._run_task_worker("split_images", PARAMS, None)
    assert estado == [False]


def test_fin_de_telemetria_se_manda_aunque_el_flush_lance(monkeypatch):
    rep = _Rep()
    api = _api(monkeypatch, rep, lambda t, p, e, a: e("done", {"status": "ok"}))

    def roto():
        raise RuntimeError("dispatch roto")

    monkeypatch.setattr(api, "_flush_push", roto)
    try:
        api._run_task_worker("split_images", PARAMS, None)
    except RuntimeError:
        pass
    assert [c for c in rep.llamadas if c[0] == "fin"] == [("fin", {"ok": True, "error": None})]
