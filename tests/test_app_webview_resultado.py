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
    monkeypatch.setattr(api, "_telemetria_resultado_iniciar", lambda i, f: rep)

    def falso(destino, iid, modo, auth, on_estado=None, **kw):
        on_estado(_estado())
        return _resumen(modo)

    monkeypatch.setattr(sr, "subir_resultado", falso)
    api.resultado_subir(str(tmp_path), 7, "urgencia", False)
    assert rep.llamadas == [{"files_done": 3, "files_total": 8, "bytes_done": 100,
                             "bytes_total": 900, "eta": 42.0}]
    assert any(e["kind"] == "estado" and e["prefix"] == "P/" for e in api.eventos)


def test_fallo_de_telemetria_no_rompe_la_subida(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_telemetria_resultado_iniciar", lambda i, f: _ReporterFalso(falla=True))

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
