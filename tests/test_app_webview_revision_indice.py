"""Endpoints de escritorio de la revisión del índice y su armado en `_run_task_worker`.

Flag apagado = comportamiento de siempre (no arma, ningún push `revision`). Con flag, el run
`completo` arma la pausa; las peticiones de la UI van por la cola al hilo del run.
"""
import sys
import threading
import time
import types

import pytest

import app_webview as aw
from atom_core import cancelacion
from atom_core import revision_indice as ri
from tests.test_recomendaciones_sin_colocar import _indexar
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


def _api(monkeypatch, run_task):
    api = aw.Api(broker=True)
    monkeypatch.setattr(api, "_reporter_actual", lambda tipo="subida": None)
    monkeypatch.setattr(aw.precarga, "precargar_pandas", lambda: None)
    mod = types.ModuleType("atom_core.organize")
    mod.run_task = run_task
    monkeypatch.setitem(sys.modules, "atom_core.organize", mod)
    import atom_core
    monkeypatch.setattr(atom_core, "organize", mod, raising=False)
    api.pushes = []
    monkeypatch.setattr(api, "_push", lambda d: api.pushes.append(d))
    return api


def _esperar(cond, plazo=5.0):
    fin = time.monotonic() + plazo
    while time.monotonic() < fin:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condición no cumplida a tiempo")


def test_flag_apagado_no_arma_ni_empuja_revision(monkeypatch):
    vistos = []
    api = _api(monkeypatch, lambda t, p, e, a: vistos.append(ri.armada()))
    api._run_task_worker("split_images", {"origen": "/x/P/"}, None)
    assert vistos == [False]
    assert not [p for p in api.pushes if p.get("kind") == "revision"]
    assert not ri.armada()


@pytest.mark.parametrize("params", [
    {"origen": "/x/P/", "fase": "indice"},
    {"origen": "/x/P/", "shard_count": 2, "fase": "indice"},
])
def test_flag_encendido_pero_run_no_admitido_no_arma(monkeypatch, params):
    monkeypatch.setenv("ATOM_REVISION_INDICE", "1")
    vistos = []
    api = _api(monkeypatch, lambda t, p, e, a: vistos.append(ri.armada()))
    api._run_task_worker("split_images", params, None)
    assert vistos == [False]


def test_otra_task_no_arma(monkeypatch):
    monkeypatch.setenv("ATOM_REVISION_INDICE", "1")
    vistos = []
    api = _api(monkeypatch, lambda t, p, e, a: vistos.append(ri.armada()))
    api._run_task_worker("otra", {}, None)
    assert vistos == [False]


def test_espera_por_defecto_params_y_entorno(monkeypatch):
    assert aw.Api._revision_espera_s({}) == 1800.0
    assert aw.Api._revision_espera_s({"revision_indice": {"espera_min": 2}}) == 120.0
    monkeypatch.setenv("ATOM_REVISION_ESPERA_MIN", "5")
    assert aw.Api._revision_espera_s({}) == 300.0
    assert aw.Api._revision_espera_s({"revision_indice": {"espera_min": 1}}) == 60.0
    with pytest.raises(ValueError):
        aw.Api._revision_espera_s({"revision_indice": {"espera_min": 0}})


def test_con_flag_arma_pausa_asigna_por_cola_y_desarma(monkeypatch, tanda):
    _indexar(tanda, FOTOS)    # antes del flag: con él el índice aplicaría la alta al momento
    monkeypatch.setenv("ATOM_REVISION_INDICE", "1")
    man = tanda.manifiesto()
    ejecucion = man.grupos_sin_colocar_raw()[0]["ejecucion"]
    clave2 = man._conexion().execute(
        "SELECT clave FROM imagenes WHERE nombre_original = 'DJI_0002_D.JPG'").fetchone()[0]
    resultado = {}

    def run(task, params, emit, adv):
        resultado["armada"] = ri.armada()
        resultado["pausa"] = ri.pausar_si_armada(
            man, types.SimpleNamespace(output_folder=tanda.destino), ejecucion,
            lambda _t: None, None)

    api = _api(monkeypatch, run)
    params = {"origen": "/x/P/", "destino": tanda.destino}
    hilo = threading.Thread(target=api._run_task_worker, args=("split_images", params, None),
                            daemon=True)
    hilo.start()
    try:
        _esperar(lambda: ri.estado()["estado"] == "esperando")
        assert api.revision_indice_estado()["activa"] is True
        assert [p for p in api.pushes if p.get("kind") == "revision"][0]["data"]["estado"] \
            == "esperando"
        cal = api.revision_indice_calendario(tanda.destino)
        assert cal["ok"] is True and cal["espera"]["activa"] is True
        asig = api.revision_indice_asignar([clave2], 1, tanda.destino)
        assert asig["ok"] is True and asig["resultado"]["aplicadas"][0]["n_fotos"] == 1
        assert api.revision_indice_latido() == {"ok": True}
        assert api.revision_indice_continuar("modo_raro")["ok"] is False   # error visible
        assert api.revision_indice_continuar("tal_cual")["ok"] is True
    finally:
        hilo.join(10)
    assert resultado["armada"] is True and resultado["pausa"]["resultado"] == "respondida"
    assert not ri.armada() and api.revision_indice_estado()["activa"] is False
    # pasó por la cola: la asignación consta firmada en el manifiesto del run
    manuales = man.asignaciones_manuales()
    [(c, a)] = manuales.items()
    assert c == clave2 and a["fila"] == 1 and "@" in a["quien"]
    man.cerrar()


def test_sin_pausa_asignar_va_directo_y_continuar_es_error(monkeypatch, tanda):
    _indexar(tanda, FOTOS)
    api = _api(monkeypatch, lambda *a: None)
    man = tanda.manifiesto()
    clave = man._conexion().execute(
        "SELECT clave FROM imagenes WHERE nombre_original = 'DJI_0002_D.JPG'").fetchone()[0]
    man.cerrar()
    r = api.revision_indice_asignar([clave], 1, tanda.destino)
    assert r["ok"] is True
    man = tanda.manifiesto()
    assert man.asignaciones_manuales()[clave]["fila"] == 1
    man.cerrar()
    c = api.revision_indice_continuar("tal_cual")
    assert c["ok"] is False and "no hay una revisión" in c["error"]


def test_errores_visibles_sin_destino(monkeypatch):
    api = _api(monkeypatch, lambda *a: None)
    r = api.revision_indice_calendario("")
    assert r["ok"] is False and "carpeta de destino" in r["error"]
    assert api.revision_indice_asignar(["x"], 1, "")["ok"] is False


def test_armada_sin_pausa_no_abre_segundo_manifiesto(monkeypatch, tanda):
    _indexar(tanda, FOTOS)
    api = _api(monkeypatch, lambda *a: None)
    abierto = []
    monkeypatch.setattr(aw.sin_colocar_mod if hasattr(aw, "sin_colocar_mod") else
                        __import__("atom_core.sin_colocar", fromlist=["x"]), "_abrir",
                        lambda *a, **k: abierto.append(1))
    ri.armar(60, None)    # run armado en índice/apply, aún sin pausa abierta
    for r in (api.revision_indice_asignar(["x"], 1, tanda.destino),
              api.revision_indice_aceptar(["x"], tanda.destino),
              api.revision_indice_continuar("tal_cual")):
        assert r["ok"] is False and "aún no ha llegado" in r["error"]
    assert abierto == []


def test_claves_e_ids_deben_ser_listas_de_textos(monkeypatch, tanda):
    _indexar(tanda, FOTOS)
    api = _api(monkeypatch, lambda *a: None)
    for malo in ("DJI_0002_D.JPG", [1, 2], None):
        r = api.revision_indice_asignar(malo, 1, tanda.destino)
        assert r["ok"] is False and "lista de textos" in r["error"]
        r = api.revision_indice_aceptar(malo, tanda.destino)
        assert r["ok"] is False and "lista de textos" in r["error"]
