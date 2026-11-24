"""Worker secuencial de la cola de «Organizar» (`Api.cola_*`, `_cola_worker`)."""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest

import app_webview as aw
from atom_core import cancelacion
from atom_core import cola_organizar as co


class _Escenario:
    """Sustituye `organize.run_task`: cada origen puede tener su propio guion."""

    def __init__(self):
        self.guiones = {}
        self.inicios = []
        self.fines = []
        self.activos = 0
        self.max_activos = 0
        self._l = threading.Lock()

    def run_task(self, task, params, emit, advanced):
        nombre = params["origen"].rsplit("/", 1)[-1]
        with self._l:
            self.activos += 1
            self.max_activos = max(self.max_activos, self.activos)
            self.inicios.append(nombre)
        try:
            emit("plant", nombre)
            guion = self.guiones.get(nombre)
            if guion:
                guion(emit)
            else:
                time.sleep(0.05)
                emit("done", {"status": "ok", "errors": 0, "warnings": 0, "n_total": 3,
                              "n_procesadas": 3, "elapsed": 0.1})
        finally:
            with self._l:
                self.activos -= 1
                self.fines.append(nombre)


@pytest.fixture
def esc(monkeypatch, tmp_path):
    e = _Escenario()
    monkeypatch.setattr(co, "_ruta_cola", lambda: tmp_path / "cola_organizar.json")
    monkeypatch.setattr(aw.precarga, "precargar_pandas", lambda: None)
    mod = types.ModuleType("atom_core.organize")
    mod.run_task = e.run_task
    monkeypatch.setitem(sys.modules, "atom_core.organize", mod)
    import atom_core
    monkeypatch.setattr(atom_core, "organize", mod, raising=False)
    cancelacion.limpiar()
    yield e
    cancelacion.limpiar()


@pytest.fixture
def api(monkeypatch, esc, tmp_path):
    from atom_core import cola_subidas
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: tmp_path / "cola_subidas.json")
    a = aw.Api(broker=True)
    a.eventos_resultado = []
    monkeypatch.setattr(a, "_push_resultado", lambda d: a.eventos_resultado.append(d))
    monkeypatch.setattr(a, "_cola_drenar_subidas", lambda: None)
    monkeypatch.setattr(a, "_telemetria_org_iniciar", lambda task, params: None)
    monkeypatch.setattr(a, "_auto_subir_resultado", lambda params, done: None)
    monkeypatch.setattr(a, "_revision_aplica", lambda task, params: False)
    monkeypatch.setattr(a, "_COLA_ESPERA_S", 0.01)
    return a


def _p(tmp_path, n):
    return {"origen": f"/datos/{n}", "destino": str(tmp_path / f"out_{n}"), "estadillo": "e.csv"}


def _esperar(cond, t=5.0):
    fin = time.monotonic() + t
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _estados(api):
    return {j["params"]["origen"].rsplit("/", 1)[-1]: j["estado"] for j in api.cola_listar()["jobs"]}


def _quieta(api):
    return _esperar(lambda: api._cola_hilo is None and not api._running)


def test_trabajos_secuenciales_uno_a_la_vez(api, esc, tmp_path):
    for n in ("a", "b", "c"):
        assert api.cola_encolar(_p(tmp_path, n))["ok"]
    assert _quieta(api)
    assert esc.inicios == ["a", "b", "c"]
    assert esc.max_activos == 1
    assert _estados(api) == {"a": "hecho", "b": "hecho", "c": "hecho"}
    j = api.cola_listar()["jobs"][0]
    assert "3 de 3 imágenes" in j["resumen"] and j["iniciado"] and j["fin"]


def test_eventos_llevan_job_id_y_se_limpia_entre_trabajos(api, esc, tmp_path, monkeypatch):
    vistos = []
    orig = api._push

    def espia(detail):
        orig(detail)
        vistos.append(detail)  # tras `_push`: ya lleva el job_id
    monkeypatch.setattr(api, "_push", espia)
    r = api.cola_encolar(_p(tmp_path, "a"))
    assert _quieta(api)
    plantas = [d for d in vistos if d["kind"] == "plant"]
    assert plantas and plantas[0]["job_id"] == r["job"]["id"]
    assert api._job_actual is None
    api._push({"kind": "log", "text": "x"})  # fuera de la cola: sin job_id
    assert "job_id" not in vistos[-1]


def test_un_error_no_para_la_cola(api, esc, tmp_path):
    esc.guiones["b"] = lambda emit: emit("error", "DestinoOcupado: otro proceso usa el destino")
    for n in ("a", "b", "c"):
        api.cola_encolar(_p(tmp_path, n))
    assert _quieta(api)
    assert _estados(api) == {"a": "hecho", "b": "error", "c": "hecho"}
    b = [j for j in api.cola_listar()["jobs"] if j["params"]["origen"].endswith("/b")][0]
    assert "DestinoOcupado" in b["error"]


def test_excepcion_del_run_va_a_error_y_sigue(api, esc, tmp_path):
    def boom(emit):
        raise RuntimeError("se rompió")
    esc.guiones["a"] = boom
    api.cola_encolar(_p(tmp_path, "a"))
    api.cola_encolar(_p(tmp_path, "b"))
    assert _quieta(api)
    assert _estados(api) == {"a": "error", "b": "hecho"}


def test_cancelar_el_actual_no_vacia_la_cola(api, esc, tmp_path):
    arrancado = threading.Event()

    def largo(emit):
        arrancado.set()
        assert _esperar(cancelacion.cancelado)
        emit("done", {"status": "cancelled", "cancelled": True, "mensaje": "Cancelado por el usuario"})
    esc.guiones["a"] = largo
    api.cola_encolar(_p(tmp_path, "a"))
    api.cola_encolar(_p(tmp_path, "b"))
    assert arrancado.wait(5)
    assert _estados(api)["a"] == "en_curso"
    assert api.run_cancelar()["ok"]
    assert _quieta(api)
    assert _estados(api) == {"a": "cancelado", "b": "hecho"}


def test_sin_espacio_va_a_error_y_sigue(api, esc, tmp_path, monkeypatch):
    real = co.espacio_suficiente
    monkeypatch.setattr(co, "espacio_suficiente",
                        lambda o, d: (False, "Sin espacio en el destino") if o.endswith("/a") else real(o, d))
    api.cola_encolar(_p(tmp_path, "a"))
    api.cola_encolar(_p(tmp_path, "b"))
    assert _quieta(api)
    assert esc.inicios == ["b"]  # `a` nunca llegó a correr
    assert _estados(api) == {"a": "error", "b": "hecho"}
    assert not api._running


def test_origen_duplicado_rechazado_y_destino_duplicado_con_aviso(api, esc, tmp_path):
    gate = threading.Event()
    esc.guiones["a"] = lambda emit: (gate.wait(5), emit("done", {"status": "ok"}))
    assert api.cola_encolar(_p(tmp_path, "a"))["ok"]
    assert _esperar(lambda: api._running)
    r = api.cola_encolar(_p(tmp_path, "a"))
    assert not r["ok"] and "ya está en la cola" in r["error"]
    mismo = dict(_p(tmp_path, "b"), destino=_p(tmp_path, "a")["destino"])
    r = api.cola_encolar(mismo)
    assert r["ok"] and r["avisos"]
    gate.set()
    assert _quieta(api)


def test_run_directo_rechazado_mientras_corre_la_cola_y_la_cola_espera_al_directo(api, esc, tmp_path):
    gate = threading.Event()
    esc.guiones["a"] = lambda emit: (gate.wait(5), emit("done", {"status": "ok"}))
    api.cola_encolar(_p(tmp_path, "a"))
    assert _esperar(lambda: api._running)
    assert api.run_task("split_images", _p(tmp_path, "x"))["started"] is False
    gate.set()
    assert _quieta(api)
    # Run directo sin cola: sigue funcionando y la cola espera su turno.
    gate2 = threading.Event()
    esc.guiones["d"] = lambda emit: (gate2.wait(5), emit("done", {"status": "ok"}))
    assert api.run_task("split_images", _p(tmp_path, "d"))["started"] is True
    assert _esperar(lambda: "d" in esc.inicios)
    api.cola_encolar(_p(tmp_path, "e"))
    time.sleep(0.15)
    assert "e" not in esc.inicios
    gate2.set()
    assert _esperar(lambda: "e" in esc.fines)
    assert esc.max_activos == 1


def test_al_reabrir_en_curso_interrumpido_y_pendientes_esperan_reanudar(api, esc, tmp_path):
    a = co.encolar(_p(tmp_path, "a"))["job"]["id"]
    co.encolar(_p(tmp_path, "b"))
    co.marcar(a, co.EN_CURSO)
    r = api.cola_listar()
    assert r["pausada"] is True
    assert _estados(api) == {"a": "interrumpido", "b": "pendiente"}
    time.sleep(0.15)
    assert esc.inicios == []  # nada se reanuda solo
    # Encolar estando en pausa añade pero no arranca.
    assert api.cola_encolar(_p(tmp_path, "c"))["pausada"] is True
    time.sleep(0.1)
    assert esc.inicios == []
    assert api.cola_reanudar()["ok"]
    assert _quieta(api)
    assert esc.inicios == ["b", "c"]
    assert _estados(api)["a"] == "interrumpido"
    # Reintentar el interrumpido lo ejecuta (la cola ya no está en pausa).
    assert api.cola_reintentar(a)["ok"]
    assert _quieta(api)
    assert _estados(api)["a"] == "hecho"


def test_reabrir_sin_pendientes_no_pausa(api, esc, tmp_path):
    a = co.encolar(_p(tmp_path, "a"))["job"]["id"]
    co.marcar(a, co.EN_CURSO)
    assert api.cola_listar()["pausada"] is False
    api.cola_encolar(_p(tmp_path, "b"))
    assert _quieta(api)
    assert esc.inicios == ["b"]


def test_pausa_de_revision_se_omite_y_queda_en_el_resumen(api, esc, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_revision_aplica", lambda task, params: True)
    armadas = []
    monkeypatch.setattr(api, "_armar_revision_indice", lambda t, p: armadas.append(1) or True)
    api.cola_encolar(_p(tmp_path, "a"))
    assert _quieta(api)
    assert armadas == []
    j = api.cola_listar()["jobs"][0]
    assert j["estado"] == "hecho" and "revisión del índice omitida" in j["resumen"]


def test_estadillo_lan_se_resuelve_al_encolar_no_al_ejecutar(api, esc, tmp_path, monkeypatch):
    llamadas = []

    def mover(params):
        llamadas.append(params["origen"])
        return dict(params, estadillo="/movido/e.csv")
    monkeypatch.setattr(api, "_mover_estadillo_espera_si_toca", mover)
    api.cola_encolar(_p(tmp_path, "a"))
    assert _quieta(api)
    assert llamadas == ["/datos/a"]  # una sola vez: al encolar
    assert api.cola_listar()["jobs"][0]["params"]["estadillo"] == "/movido/e.csv"


def test_quitar_pendiente_y_no_en_curso(api, esc, tmp_path):
    gate = threading.Event()
    esc.guiones["a"] = lambda emit: (gate.wait(5), emit("done", {"status": "ok"}))
    a = api.cola_encolar(_p(tmp_path, "a"))["job"]["id"]
    b = api.cola_encolar(_p(tmp_path, "b"))["job"]["id"]
    assert _esperar(lambda: _estados(api).get("a") == "en_curso")
    assert not api.cola_quitar(a)["ok"]
    assert api.cola_quitar(b)["ok"]
    gate.set()
    assert _quieta(api)
    assert esc.inicios == ["a"]


def test_sin_espacio_no_toca_ultimo_destino(api, esc, tmp_path, monkeypatch):
    monkeypatch.setattr(co, "espacio_suficiente", lambda o, d: (False, "Sin espacio"))
    api._ultimo_destino = "/previo"
    api.cola_encolar(_p(tmp_path, "a"))
    assert _quieta(api)
    assert _estados(api) == {"a": "error"} and api._ultimo_destino == "/previo"


def test_baseexception_en_el_run_no_deja_en_curso(api, esc, tmp_path, monkeypatch):
    def muere(task, params, emit, advanced):
        raise SystemExit(3)
    esc.run_task = muere
    sys.modules["atom_core.organize"].run_task = muere
    job = co.encolar(_p(tmp_path, "a"))["job"]
    with pytest.raises(SystemExit):
        api._cola_ejecutar(co.obtener(job["id"]))
    assert co.obtener(job["id"])["estado"] == "interrumpido"
    assert api._running is False


def test_en_curso_solo_si_seguia_pendiente(api, esc, tmp_path):
    job = co.encolar(_p(tmp_path, "a"))["job"]
    snapshot = co.obtener(job["id"])
    co.marcar(job["id"], co.CANCELADO)       # cambió mientras esperaba su turno
    api._cola_ejecutar(snapshot)
    assert esc.inicios == [] and co.obtener(job["id"])["estado"] == "cancelado"
    assert api._running is False


def test_cancelar_antes_de_arrancar_no_se_propaga_al_run_nuevo(api, esc):
    assert api._lanzar_run({"destino": "/d"})
    assert api.run_cancelar()["ok"]            # cancelación del run viejo
    with api._run_lock:
        api._running = False                   # el run viejo acaba sin limpiar
    assert api._lanzar_run({"destino": "/d"})
    assert not cancelacion.cancelado()        # el nuevo no hereda la bandera
    api._running = False


def test_encolado_pero_arranque_falla_es_ok_con_aviso(api, esc, tmp_path, monkeypatch):
    def revienta():
        raise RuntimeError("no hay hilos")
    monkeypatch.setattr(api, "_cola_arrancar", revienta)
    r = api.cola_encolar(_p(tmp_path, "a"))
    assert r["ok"] is True and "codigo" not in r
    assert any("no arrancó" in a for a in r["avisos"])
    assert _estados(api) == {"a": "pendiente"}


def test_origen_duplicado_lleva_codigo_ya_en_cola(api, esc, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_cola_arrancar", lambda: None)
    assert api.cola_encolar(_p(tmp_path, "a"))["ok"]
    r = api.cola_encolar(_p(tmp_path, "a"))
    assert r["ok"] is False and r["codigo"] == "ya_en_cola"


def test_cola_ilegible_al_encolar_es_cola_error(api, esc, tmp_path, monkeypatch):
    def ilegible(*a, **k):
        raise co.ColaIlegible("EIO")
    monkeypatch.setattr(co, "encolar", ilegible)
    r = api.cola_encolar(_p(tmp_path, "a"))
    assert r["ok"] is False and r["codigo"] == "cola_error"


def _con_subida(tmp_path, n, insp=7, subir=True):
    p = _p(tmp_path, n)
    p["subir_al_terminar"] = subir
    if insp is not None:
        p["inspeccion_id"] = insp
    return p


def test_hecho_con_subir_al_terminar_encola_la_subida_del_resultado(api, esc, tmp_path):
    from atom_core import cola_subidas
    assert api.cola_encolar(_con_subida(tmp_path, "a"))["ok"]
    assert _quieta(api)
    j = api.cola_listar()["jobs"][0]
    assert j["estado"] == "hecho" and j["subir_al_terminar"] is True
    assert j["subida"]["estado"] == "encolada"
    pend = cola_subidas.pendientes()
    assert len(pend) == 1
    assert pend[0]["tipo"] == cola_subidas.TIPO_RESULTADO
    assert pend[0]["folder"] == str(tmp_path / "out_a") and pend[0]["inspeccion_id"] == 7


def test_sin_flag_no_se_encola_ninguna_subida(api, esc, tmp_path):
    from atom_core import cola_subidas
    assert api.cola_encolar(_con_subida(tmp_path, "a", subir=False))["ok"]
    assert _quieta(api)
    assert api.cola_listar()["jobs"][0]["subida"] is None
    assert cola_subidas.pendientes() == []


def test_error_o_cancelado_no_sube(api, esc, tmp_path):
    from atom_core import cola_subidas
    esc.guiones["a"] = lambda emit: emit("error", "boom")
    assert api.cola_encolar(_con_subida(tmp_path, "a"))["ok"]
    assert _quieta(api)
    assert _estados(api) == {"a": "error"}
    assert cola_subidas.pendientes() == []


def test_sin_inspeccion_no_sube_y_avisa(api, esc, tmp_path):
    from atom_core import cola_subidas
    assert api.cola_encolar(_con_subida(tmp_path, "a", insp=None))["ok"]
    assert _quieta(api)
    j = api.cola_listar()["jobs"][0]
    assert j["estado"] == "hecho" and j["subida"]["estado"] == "omitida"
    assert "inspección" in j["subida"]["detalle"]
    assert cola_subidas.pendientes() == []
    assert any(e["kind"] == "aviso" and "inspección" in e["text"] for e in api.eventos_resultado)
