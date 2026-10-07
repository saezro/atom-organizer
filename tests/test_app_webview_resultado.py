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
