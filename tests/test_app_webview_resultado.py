"""`Api.resultado_*` y el disparo automático al terminar un run. Sin red ni hilos reales."""
from __future__ import annotations

import pytest

import app_webview as aw
from atom_core import cola_subidas, subida_resultado as sr
from atom_core.google_auth import AuthError


@pytest.fixture(autouse=True)
def sin_cliente_oauth(monkeypatch):
    from atom_core import cloud_config
    monkeypatch.setattr(cloud_config, "load_client", lambda base_dir=None: None)


class _HiloInmediato:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **_):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _AuthPwd:
    es_password = True

    def is_logged_in(self):
        return True


@pytest.fixture
def api(monkeypatch, tmp_path):
    a = aw.Api(broker=True)
    monkeypatch.setattr(aw.threading, "Thread", _HiloInmediato)
    monkeypatch.setattr(a, "_get_auth", lambda **k: _AuthPwd())
    monkeypatch.setattr(a, "cloud_asegurar_estado", lambda: {})
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: tmp_path / "cola.json")
    a.eventos = []
    monkeypatch.setattr(a, "_push_resultado", a.eventos.append)
    return a


def _resumen(modo, ok=True, cancelado=False):
    r = sr.ResumenResultado(modo=modo, prefijo="P/")
    r.cancelado = cancelado
    if not ok:
        r.fallidas = [("P/x", "boom")]
    return r


def test_urgencia_con_seguir_en_normal_encadena_los_dos_modos(api, monkeypatch, tmp_path):
    modos = []
    monkeypatch.setattr(sr, "subir_resultado", lambda destino, iid, modo, auth, **kw: modos.append(modo) or _resumen(modo))
    r = api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert r == {"started": True}
    assert modos == ["urgencia", "normal"]
    dones = [e for e in api.eventos if e["kind"] == "done"]
    assert [d["modo"] for d in dones] == ["urgencia", "normal"]
    assert dones[0]["continua"] is True and dones[1]["continua"] is False


def test_si_urgencia_falla_no_sigue_en_normal(api, monkeypatch, tmp_path):
    modos = []
    monkeypatch.setattr(sr, "subir_resultado", lambda d, i, modo, a, **kw: modos.append(modo) or _resumen(modo, ok=False))
    api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert modos == ["urgencia"]
    assert [e for e in api.eventos if e["kind"] == "done"][0]["continua"] is False


def test_encadenado_ok_lanza_resto_y_loguea(api, monkeypatch, tmp_path, caplog):
    modos = []
    monkeypatch.setattr(sr, "subir_resultado", lambda d, i, modo, a, **kw: modos.append(modo) or _resumen(modo))
    with caplog.at_level("INFO"):
        api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert modos == ["urgencia", "normal"]
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("resto en cola" in m for m in msgs) == 1 and sum("arranca el resto" in m for m in msgs) == 1
    assert api._subiendo_resultado is False  # una sola subida a la vez, liberada al final


def test_encadenado_con_fallos_no_lanza_resto_y_avisa(api, monkeypatch, tmp_path):
    modos = []
    monkeypatch.setattr(sr, "subir_resultado", lambda d, i, modo, a, **kw: modos.append(modo) or _resumen(modo, ok=False))
    api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert modos == ["urgencia"]
    assert {"kind": "aviso", "text": "Resto no lanzado: urgentes con 1 fallos"} in api.eventos


def test_encadenado_cancelado_no_lanza_resto(api, monkeypatch, tmp_path):
    modos = []
    monkeypatch.setattr(sr, "subir_resultado", lambda d, i, modo, a, **kw: modos.append(modo) or _resumen(modo, cancelado=True))
    api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert modos == ["urgencia"]
    assert any(e["kind"] == "aviso" and "Resto no lanzado" in e["text"] for e in api.eventos)


def test_desmarcar_el_resto_durante_urgentes_no_lo_lanza(api, monkeypatch, tmp_path):
    modos = []

    def sube(d, i, modo, a, **kw):
        modos.append(modo)
        assert api.resultado_encadenar(False) == {"ok": True, "activo": False}
        return _resumen(modo)
    monkeypatch.setattr(sr, "subir_resultado", sube)
    api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert modos == ["urgencia"]
    assert api.resultado_encadenar(True)["ok"] is False  # sin subida en curso


def test_encadenar_sin_fase_de_resto_devuelve_ok_false_y_no_hace_push(api, monkeypatch, tmp_path):
    res = []

    def sube(d, i, modo, a, **kw):
        n = len(api.eventos)
        res.append((api.resultado_encadenar(False), len(api.eventos) - n))
        return _resumen(modo)
    monkeypatch.setattr(sr, "subir_resultado", sube)
    api.resultado_subir(str(tmp_path), 7, "urgencia", False)
    assert res[0][0]["ok"] is False and res[0][1] == 0
    assert [e for e in api.eventos if e["kind"] == "start"][0]["resto"] is False
    api.eventos.clear()
    api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    assert [e for e in api.eventos if e["kind"] == "start"][0]["resto"] is True


def test_rechaza_modo_inspeccion_o_carpeta_invalidos(api, tmp_path):
    assert api.resultado_subir(str(tmp_path), 7, "rapido")["started"] is False
    assert "inspección" in api.resultado_subir(str(tmp_path), None)["reason"]
    assert "carpeta" in api.resultado_subir(str(tmp_path / "no-existe"), 7)["reason"]


def test_no_arranca_dos_a_la_vez(api, monkeypatch, tmp_path):
    api._subiendo_resultado = True
    r = api.resultado_subir(str(tmp_path), 7)
    assert r["started"] is False and "en curso" in r["reason"]


def test_error_de_credencial_se_encola_y_se_avisa(api, monkeypatch, tmp_path):
    def falla(*a, **k):
        raise AuthError("Sesión caducada, vuelve a entrar con tu usuario")
    monkeypatch.setattr(sr, "subir_resultado", falla)
    api.resultado_subir(str(tmp_path), 7, "urgencia", True)
    err = [e for e in api.eventos if e["kind"] == "error"]
    assert err and "Sesión caducada" in err[0]["text"]
    jobs = cola_subidas.pendientes()
    assert len(jobs) == 1 and jobs[0]["tipo"] == "resultado" and jobs[0]["inspeccion_id"] == 7
    assert api._subiendo_resultado is False


def test_subida_ok_descarta_el_job_encolado(api, monkeypatch, tmp_path):
    cola_subidas.encolar(str(tmp_path), "@resultado", 7, tipo="resultado")
    monkeypatch.setattr(sr, "subir_resultado", lambda d, i, modo, a, **kw: _resumen(modo))
    api.resultado_subir(str(tmp_path), 7, "urgencia", False)
    assert cola_subidas.pendientes() == []


def test_error_de_modo_google_es_visible_no_silencioso(api, monkeypatch, tmp_path):
    def no_pwd(*a, **k):
        raise sr.SubidaResultadoError("Subir el resultado al bucket exige entrar con usuario y contraseña de ATOM Suite.")
    monkeypatch.setattr(sr, "subir_resultado", no_pwd)
    api.resultado_subir(str(tmp_path), 7)
    assert any(e["kind"] == "error" and "usuario y contraseña" in e["text"] for e in api.eventos)
    assert cola_subidas.pendientes() == []  # un error de modo no se reintenta solo


def test_cancelar_activa_la_bandera(api):
    assert api.resultado_cancelar() == {"ok": True}
    assert api._cancel_resultado is True


def test_auto_dispara_urgencia_con_seguir_en_normal(api, monkeypatch, tmp_path):
    llamadas = []
    monkeypatch.setattr(api, "resultado_subir", lambda *a, **k: llamadas.append((a, k)) or {"started": True})
    api._auto_subir_resultado({"destino": str(tmp_path), "inspeccion_id": 7}, {"status": "ok"})
    assert llamadas == [((str(tmp_path), 7, "urgencia", True), {})]


def test_auto_sin_inspeccion_avisa_y_no_sube(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "resultado_subir", lambda *a, **k: pytest.fail("no debe subir"))
    api._auto_subir_resultado({"destino": str(tmp_path)}, {"status": "ok"})
    assert api.eventos and api.eventos[0]["kind"] == "aviso" and "inspección" in api.eventos[0]["text"]


def test_auto_run_con_errores_no_sube_ni_avisa(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "resultado_subir", lambda *a, **k: pytest.fail("no debe subir"))
    api._auto_subir_resultado({"destino": str(tmp_path), "inspeccion_id": 7}, {"status": "error"})
    assert api.eventos == []


def test_cloud_drenar_ignora_jobs_de_resultado_y_los_lanza_aparte(api, monkeypatch, tmp_path):
    from atom_core.credencial import ESTADO_OK
    monkeypatch.setattr(api._credencial, "actual", lambda: {"estado": ESTADO_OK})
    cola_subidas.encolar(str(tmp_path), "@resultado", 7, tipo="resultado")
    llamadas = {"crudo": 0, "resultado": []}
    monkeypatch.setattr(api, "cloud_upload", lambda *a, **k: llamadas.__setitem__("crudo", llamadas["crudo"] + 1) or {"started": True})
    monkeypatch.setattr(api, "resultado_subir", lambda *a, **k: llamadas["resultado"].append(a) or {"started": True})
    assert api.cloud_drenar() == {"lanzados": 1}
    assert llamadas["crudo"] == 0 and llamadas["resultado"] == [(str(tmp_path), 7, "urgencia", True)]


def test_get_auth_que_lanza_no_arranca_ni_deja_la_bandera(api, monkeypatch, tmp_path):
    def boom(**k):
        raise RuntimeError("sin credencial")
    monkeypatch.setattr(api, "_get_auth", boom)
    r = api.resultado_subir(str(tmp_path), 7)
    assert r["started"] is False and "sin credencial" in r["reason"]
    assert api._subiendo_resultado is False


def test_oserror_local_no_se_encola_y_es_visible(api, monkeypatch, tmp_path):
    def falla(*a, **k):
        raise FileNotFoundError("falta.tif")
    monkeypatch.setattr(sr, "subir_resultado", falla)
    api.resultado_subir(str(tmp_path), 7)
    assert any(e["kind"] == "error" and "FileNotFoundError" in e["text"] for e in api.eventos)
    assert cola_subidas.pendientes() == []
    assert api._subiendo_resultado is False


def test_error_de_red_se_encola_para_reintento(api, monkeypatch, tmp_path):
    def falla(*a, **k):
        raise ConnectionError("sin red")
    monkeypatch.setattr(sr, "subir_resultado", falla)
    api.resultado_subir(str(tmp_path), 7)
    assert any(e["kind"] == "error" and "reintentará" in e["text"] for e in api.eventos)
    assert len(cola_subidas.pendientes()) == 1


class _ReporterFalso:
    activo = True

    def __init__(self, falla=False):
        self.llamadas, self.falla = [], falla

    def progreso(self, s):
        if self.falla:
            raise RuntimeError("suite caida")
        self.llamadas.append(s)

    def fin(self, **kw):
        pass


def _estado(**extra):
    return {"fase": "urgentes", "urgentes": {"hechos": 2, "total": 5}, "resto": {"hechos": 1, "total": 3},
            "mbps": 9.0, "prefix": "P/", "bytes_done": 100, "bytes_total": 900, "eta": 42.0, **extra}


def test_progreso_del_resultado_va_a_la_telemetria_con_totales_y_eta(api, monkeypatch, tmp_path):
    rep = _ReporterFalso()
    monkeypatch.setattr(api, "_telemetria_resultado_iniciar", lambda i, f, *a: rep)

    def falso(destino, iid, modo, auth, on_estado=None, **kw):
        on_estado(_estado())
        return _resumen(modo)

    monkeypatch.setattr(sr, "subir_resultado", falso)
    api.resultado_subir(str(tmp_path), 7, "urgencia", False)
    assert rep.llamadas == [{"files_done": 3, "files_total": 8, "bytes_done": 100,
                             "bytes_total": 900, "eta": 42.0}]
    assert any(e["kind"] == "estado" and e["prefix"] == "P/" for e in api.eventos)


def test_fallo_de_telemetria_no_rompe_la_subida(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_telemetria_resultado_iniciar", lambda i, f, *a: _ReporterFalso(falla=True))

    def falso(destino, iid, modo, auth, on_estado=None, **kw):
        on_estado(_estado())
        return _resumen(modo)

    monkeypatch.setattr(sr, "subir_resultado", falso)
    api.resultado_subir(str(tmp_path), 7, "urgencia", False)
    assert [e["kind"] for e in api.eventos].count("done") == 1
    assert not [e for e in api.eventos if e["kind"] == "error"]


# ---- credencial (Google / password / Pi / sin login) -------------------------------
class _AuthGoogle:
    es_password = False
    es_google = True

    class identity:  # noqa: N801
        email = "u@aerotools.es"


class _AuthPi:
    es_password = False
    es_google = False
    es_broker = True


def test_credencial_password_y_google_ok(api, monkeypatch):
    assert api.resultado_credencial() == {"ok": True, "tipo": "password", "usuario": None, "reason": ""}
    monkeypatch.setattr(api, "_get_auth", lambda **k: _AuthGoogle())
    r = api.resultado_credencial()
    assert r["ok"] and r["tipo"] == "google" and r["usuario"] == "u@aerotools.es"


def test_credencial_pi_rechazada_y_sin_login(api, monkeypatch):
    monkeypatch.setattr(api, "_get_auth", lambda **k: _AuthPi())
    r = api.resultado_credencial()
    assert not r["ok"] and r["tipo"] is None and "Pi" in r["reason"]
    monkeypatch.setattr(api, "_get_auth", lambda **k: None)
    r = api.resultado_credencial()
    assert not r["ok"] and "sesión" in r["reason"]


def test_subir_con_google_arranca(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_get_auth", lambda **k: _AuthGoogle())
    monkeypatch.setattr(sr, "subir_resultado", lambda d, i, modo, a, **kw: _resumen(modo))
    assert api.resultado_subir(str(tmp_path), 7, "urgencia", False) == {"started": True}


def test_subir_sin_login_o_pi_no_crea_run_ni_cola(api, monkeypatch, tmp_path):
    iniciados = []
    monkeypatch.setattr(api, "_telemetria_resultado_iniciar", lambda *a: iniciados.append(a))
    for auth in (None, _AuthPi()):
        monkeypatch.setattr(api, "_get_auth", lambda auth=auth, **k: auth)
        r = api.resultado_subir(str(tmp_path), 7)
        assert r["started"] is False and r["reason"]
    assert iniciados == [] and api.eventos == [] and cola_subidas.pendientes() == []
    assert api._subiendo_resultado is False


def test_inspeccionar_destino_bridge_exige_credencial(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_get_auth", lambda **k: None)
    r = api.resultado_inspeccionar_destino(str(tmp_path), 7)
    assert r["ok"] is False and r["error"]


def test_prechequeo_bridge_delega_y_captura_errores(api, monkeypatch, tmp_path):
    from atom_core import prechequeo_resultado as pr
    monkeypatch.setattr(pr, "prechequear", lambda d: {"ok": True, "checks": []})
    assert api.resultado_prechequeo(str(tmp_path)) == {"ok": True, "checks": []}

    def boom(d):
        raise ValueError("roto")
    monkeypatch.setattr(pr, "prechequear", boom)
    r = api.resultado_prechequeo(str(tmp_path))
    assert r["ok"] is False and r["checks"][0]["estado"] == "error" and r["checks"][0]["detalle"] == "roto"


def test_drenar_sin_credencial_no_suma_intento_ni_descarta(api, monkeypatch, tmp_path):
    from atom_core.credencial import ESTADO_OK
    monkeypatch.setattr(api._credencial, "actual", lambda: {"estado": ESTADO_OK})
    monkeypatch.setattr(api, "_get_auth", lambda **k: None)
    cola_subidas.encolar(str(tmp_path), "@resultado", 7, tipo="resultado")
    for _ in range(sr_max()):
        assert api.cloud_drenar() == {"lanzados": 0}
    pend = cola_subidas.pendientes()
    assert len(pend) == 1 and not pend[0].get("intentos")


def sr_max():
    return aw.MAX_INTENTOS_RESULTADO + 1


def test_inspeccionar_destino_start_corre_en_hilo_y_empuja_con_token(api, monkeypatch, tmp_path):
    import threading
    llamadas = []

    def lento(destino, inspeccion_id):
        llamadas.append((destino, inspeccion_id))
        return {"ok": True, "existe": False, "n_objetos": 0, "bytes": 0, "sobrescribe": []}

    monkeypatch.setattr(api, "resultado_inspeccionar_destino", lento)
    r = api.resultado_inspeccionar_destino_start(str(tmp_path), 7, "t1")
    assert r == {"started": True}
    for _ in range(200):
        if api.eventos:
            break
        threading.Event().wait(0.01)
    ev = api.eventos[0]
    assert ev["kind"] == "destino" and ev["token"] == "t1" and ev["ok"] is True
    assert llamadas == [(str(tmp_path), 7)]


def test_inspeccionar_destino_start_error_llega_visible_y_sin_token_se_rechaza(api, monkeypatch, tmp_path):
    import threading

    def roto(destino, inspeccion_id):
        raise RuntimeError("bucket caído")

    monkeypatch.setattr(api, "resultado_inspeccionar_destino", roto)
    assert api.resultado_inspeccionar_destino_start(str(tmp_path), 7, "")["started"] is False
    api.resultado_inspeccionar_destino_start(str(tmp_path), 7, "t2")
    for _ in range(200):
        if api.eventos:
            break
        threading.Event().wait(0.01)
    assert api.eventos[0]["ok"] is False and "bucket caído" in api.eventos[0]["error"]


def test_inspeccionar_destino_carpeta_inexistente_error_visible_y_log(api, monkeypatch, tmp_path, caplog):
    import logging
    monkeypatch.setattr(api, "_get_auth", lambda **k: None)
    monkeypatch.setattr(sr, "credencial_resultado", lambda auth: {"ok": True})
    falsa = str(tmp_path / "CASTRUM_ORG")
    with caplog.at_level(logging.INFO):
        r = api.resultado_inspeccionar_destino(falsa, 7)
    assert r["ok"] is False
    assert r["error"] == f"La carpeta {falsa} no existe o no es accesible"
    assert any("inspeccionar_destino" in m and "FALLO" in m for m in caplog.messages)


def test_inspeccionar_destino_start_esta_en_la_allowlist_del_modo_server():
    from atom_core.webserver import METODOS_EXPUESTOS
    assert "resultado_inspeccionar_destino_start" in METODOS_EXPUESTOS
    assert callable(getattr(aw.Api, "resultado_inspeccionar_destino_start"))


def test_inspeccionar_destino_start_hilo_con_token_viejo_no_consulta_ni_empuja(api, monkeypatch, tmp_path):
    llamadas = []
    monkeypatch.setattr(api, "resultado_inspeccionar_destino",
                        lambda d, i: llamadas.append(d) or {"ok": True})
    captura = []

    class _HiloDiferido:
        def __init__(self, target=None, **_):
            self._t = target

        def start(self):
            captura.append(self._t)

    monkeypatch.setattr(aw.threading, "Thread", _HiloDiferido)
    api.resultado_inspeccionar_destino_start(str(tmp_path), 7, "viejo")
    api.resultado_inspeccionar_destino_start(str(tmp_path), 7, "nuevo")
    for w in captura:
        w()
    assert llamadas == [str(tmp_path)]
    assert [e["token"] for e in api.eventos] == ["nuevo"]


def test_inspeccionar_destino_start_push_con_excepcion_no_rompe_el_hilo(api, monkeypatch, tmp_path, caplog):
    import logging
    monkeypatch.setattr(api, "resultado_inspeccionar_destino", lambda d, i: {"ok": True})

    def roto(detail):
        raise RuntimeError("sink caído")

    monkeypatch.setattr(api, "_push_resultado", roto)
    with caplog.at_level(logging.ERROR):
        assert api.resultado_inspeccionar_destino_start(str(tmp_path), 7, "t9") == {"started": True}
    assert any("no se pudo empujar" in m for m in caplog.messages)


def test_iniciar_del_run_resultado_lleva_items_y_bytes_total(api, monkeypatch, tmp_path):
    (tmp_path / "FOTOS_GENERALES").mkdir()
    (tmp_path / "FOTOS_GENERALES" / "a.jpg").write_bytes(b"x" * 10)

    class _Rep:
        activo = True

        def __init__(self):
            self.kw = None

        def iniciar(self, **kw):
            self.kw = kw

    rep = _Rep()
    monkeypatch.setattr(api, "_reporter_actual", lambda: rep)
    r = api._telemetria_resultado_iniciar(7, str(tmp_path), sr.MODO_NORMAL)
    assert r is rep
    assert rep.kw["etapa"] == "subida" and rep.kw["inspeccion_id"] == 7
    assert rep.kw["items_total"] == 1 and rep.kw["bytes_total"] == 10
