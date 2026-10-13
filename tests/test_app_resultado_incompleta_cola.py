"""`Api.resultado_subir`: subida incompleta -> el job se queda en `cola_subidas`
(persistencia REAL, fichero en tmp) con tope de intentos, y la telemetría
(etapa="subida") cierra en error. Truncado = no verificable, sin re-encolar."""
from __future__ import annotations

import time
import types

import app_webview as aw
from atom_core import cola_subidas as cola
from atom_core import subida_resultado as sr


class _Rep:
    def __init__(self):
        self.llamadas = []
        self.activo = True

    def iniciar(self, **kw):
        self.llamadas.append(("iniciar", kw))

    def fin(self, **kw):
        self.llamadas.append(("fin", kw))


def _api(monkeypatch, tmp_path, resumen):
    monkeypatch.setattr(cola, "_ruta_cola", lambda: tmp_path / "cola.json")
    api = aw.Api(broker=True)
    rep = _Rep()
    errores = []
    monkeypatch.setattr(api, "_reporter_actual", lambda tipo="subida": rep)
    monkeypatch.setattr(api, "_get_auth", lambda: None)
    monkeypatch.setattr(api, "_push_resultado",
                        lambda d: errores.append(d["text"]) if d.get("kind") == "error" else None)
    monkeypatch.setattr(sr, "subir_resultado", lambda *a, **k: resumen)
    return api, rep, errores


def _lanzar(api, tmp_path):
    carpeta = tmp_path / "salida"
    carpeta.mkdir(exist_ok=True)
    assert api.resultado_subir(str(carpeta), 7, sr.MODO_URGENCIA, False)["started"]
    for _ in range(200):
        if not api._subiendo_resultado:
            break
        time.sleep(0.05)


def _incompleta(n=3):
    return sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/",
                               faltantes=[(f"P/{i}.jpg", "no esta en el bucket") for i in range(n)])


def test_incompleta_sigue_en_cola_con_intentos_y_tope_de_3(monkeypatch, tmp_path):
    api, rep, errores = _api(monkeypatch, tmp_path, _incompleta())
    _lanzar(api, tmp_path)
    jobs = cola.pendientes()
    assert len(jobs) == 1 and jobs[0]["intentos"] == 1 and "3 objetos sin subir" in jobs[0]["ultimo_error"]
    _lanzar(api, tmp_path)
    assert cola.pendientes()[0]["intentos"] == 2
    _lanzar(api, tmp_path)   # tercer intento: sale de la cola
    assert cola.pendientes() == []
    assert "subida incompleta tras 3 intentos: 3 objetos sin subir" in errores[-1]
    fines = [c[1] for c in rep.llamadas if c[0] == "fin"]
    assert len(fines) == 3 and all(f["ok"] is False for f in fines)
    assert "subida incompleta tras 3 intentos" in fines[-1]["error"]
    assert [c for c in rep.llamadas if c[0] == "iniciar"][0][1]["etapa"] == "subida"


def test_completa_sale_de_cola_y_telemetria_ok(monkeypatch, tmp_path):
    api, rep, errores = _api(monkeypatch, tmp_path, sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/"))
    carpeta = tmp_path / "salida"
    carpeta.mkdir()
    cola.encolar(str(carpeta), "@resultado", 7, tipo=cola.TIPO_RESULTADO)
    _lanzar(api, tmp_path)
    assert cola.pendientes() == [] and errores == []
    assert [c[1] for c in rep.llamadas if c[0] == "fin"] == [{"ok": True}]


def test_no_verificado_por_red_sigue_en_cola(monkeypatch, tmp_path):
    api, rep, _ = _api(monkeypatch, tmp_path,
                       sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/", verificado=False))
    _lanzar(api, tmp_path)
    assert len(cola.pendientes()) == 1
    assert [c[1] for c in rep.llamadas if c[0] == "fin"][0]["ok"] is False


def test_truncado_no_ok_warning_y_sin_reencolar(monkeypatch, tmp_path, caplog):
    api, rep, errores = _api(monkeypatch, tmp_path,
                             sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/", truncado=True))
    with caplog.at_level("WARNING"):
        _lanzar(api, tmp_path)
    assert cola.pendientes() == []
    assert errores and "truncado" in errores[-1]
    assert any("truncado" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert [c[1] for c in rep.llamadas if c[0] == "fin"][0]["ok"] is False


def test_truncado_descarta_job_que_ya_estaba_en_cola(monkeypatch, tmp_path):
    api, rep, errores = _api(monkeypatch, tmp_path,
                             sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/", truncado=True))
    carpeta = tmp_path / "salida"
    carpeta.mkdir()
    cola.encolar(str(carpeta), "@resultado", 7, tipo=cola.TIPO_RESULTADO)
    assert len(cola.pendientes()) == 1
    _lanzar(api, tmp_path)
    assert cola.pendientes() == [] and errores and "truncado" in errores[-1]


def test_drenar_no_cuenta_intento_si_ya_hay_subida_en_curso(monkeypatch, tmp_path):
    api, _rep, _e = _api(monkeypatch, tmp_path, sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/"))
    carpeta = tmp_path / "salida"
    carpeta.mkdir()
    cola.encolar(str(carpeta), "@resultado", 7, tipo=cola.TIPO_RESULTADO)
    monkeypatch.setattr(api._credencial, "actual", lambda: {"estado": aw.ESTADO_OK})
    api._subiendo_resultado = True   # subida en curso: resultado_subir -> started False
    assert api.cloud_drenar()["lanzados"] == 0
    jobs = cola.pendientes()
    assert len(jobs) == 1 and jobs[0]["intentos"] == 0


def test_cancelado_por_usuario_descarta_job_de_la_cola(monkeypatch, tmp_path):
    api, _rep, _e = _api(monkeypatch, tmp_path,
                         sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/", cancelado=True))
    carpeta = tmp_path / "salida"
    carpeta.mkdir()
    cola.encolar(str(carpeta), "@resultado", 7, tipo=cola.TIPO_RESULTADO)
    _lanzar(api, tmp_path)
    assert cola.pendientes() == []


def test_fallidas_sin_faltantes_suman_intento_y_descartan_al_tope(monkeypatch, tmp_path):
    res = sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/", fallidas=[("P/a.jpg", "boom")])
    api, _rep, errores = _api(monkeypatch, tmp_path, res)
    _lanzar(api, tmp_path)
    jobs = cola.pendientes()
    assert len(jobs) == 1 and jobs[0]["intentos"] == 1
    _lanzar(api, tmp_path)
    assert cola.pendientes()[0]["intentos"] == 2
    _lanzar(api, tmp_path)
    assert cola.pendientes() == []
    assert "subida incompleta tras 3 intentos" in errores[-1]


def _drenar_api(monkeypatch, tmp_path, r):
    api, _rep, _e = _api(monkeypatch, tmp_path, sr.ResumenResultado(modo=sr.MODO_URGENCIA, prefijo="P/"))
    carpeta = tmp_path / "salida"
    carpeta.mkdir(exist_ok=True)
    cola.encolar(str(carpeta), "@resultado", 7, tipo=cola.TIPO_RESULTADO)
    monkeypatch.setattr(api._credencial, "actual", lambda: {"estado": aw.ESTADO_OK})
    monkeypatch.setattr(api, "resultado_subir", lambda *a, **k: r)
    return api


def test_drenar_busy_no_cuenta_intento(monkeypatch, tmp_path):
    api = _drenar_api(monkeypatch, tmp_path, {"started": False, "busy": True, "reason": "x"})
    for _ in range(5):
        api.cloud_drenar()
    jobs = cola.pendientes()
    assert len(jobs) == 1 and jobs[0]["intentos"] == 0


def test_drenar_otro_rechazo_descarta_al_tope(monkeypatch, tmp_path):
    api = _drenar_api(monkeypatch, tmp_path, {"started": False, "reason": "La carpeta de salida no existe."})
    api.cloud_drenar()
    assert cola.pendientes()[0]["intentos"] == 1
    api.cloud_drenar()
    assert cola.pendientes()[0]["intentos"] == 2
    api.cloud_drenar()
    assert cola.pendientes() == []
