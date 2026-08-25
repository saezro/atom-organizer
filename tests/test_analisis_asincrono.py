import time
import pytest

from app_webview import Api


class SinkFalso:
    def __init__(self):
        self.eventos = []

    def dispatch(self, event, detail):
        self.eventos.append((event, detail))

    def dispatch_many(self, mensajes):
        for event, detail in mensajes:
            self.dispatch(event, detail)


def _esperar(sink, kind, timeout=5.0):
    """Espera a que llegue un evento atom:analisis con ese kind."""
    fin = time.time() + timeout
    while time.time() < fin:
        for event, detail in list(sink.eventos):
            if event == "atom:analisis" and detail.get("kind") == kind:
                return detail
        time.sleep(0.01)
    raise AssertionError(f"no llego el evento {kind}: {sink.eventos}")


@pytest.fixture
def api_con_sink():
    api = Api()
    sink = SinkFalso()
    api._sink = sink
    return api, sink


def test_detect_suffixes_start_devuelve_al_instante_y_emite_done(tmp_path, api_con_sink):
    api, sink = api_con_sink
    (tmp_path / "DJI_0001_T.JPG").write_bytes(b"x")
    (tmp_path / "DJI_0001_W.JPG").write_bytes(b"x")

    assert api.detect_suffixes_start(str(tmp_path)) == {"started": True}
    done = _esperar(sink, "done")
    assert done["scope"] == "suffixes"
    assert done["data"]["thermal"] == "_T"


def test_detect_suffixes_start_rechaza_dos_analisis_a_la_vez(tmp_path, api_con_sink):
    api, _ = api_con_sink
    api._analizando = True
    r = api.detect_suffixes_start(str(tmp_path))
    assert r["started"] is False
    assert "curso" in r["reason"]


def test_detect_suffixes_start_emite_error_si_la_carpeta_no_existe(tmp_path, api_con_sink):
    api, sink = api_con_sink
    api.detect_suffixes_start(str(tmp_path / "no-existe"))
    done = _esperar(sink, "done")
    assert done["data"]["ok"] is False


def test_analisis_cancel_corta_el_escaneo(tmp_path, api_con_sink):
    api, sink = api_con_sink
    for i in range(600):
        (tmp_path / f"DJI_{i:04d}_T.JPG").write_bytes(b"x")
    api._cancel_analisis = True   # cancelado antes de arrancar: corta en el primer chequeo
    api.detect_suffixes_start(str(tmp_path))
    _esperar(sink, "cancelled")
    assert api._analizando is False


def test_detect_suffixes_sincrono_sigue_existiendo(tmp_path, api_con_sink):
    """El metodo viejo no se toca: hay tests y llamadas que dependen de el."""
    api, _ = api_con_sink
    (tmp_path / "DJI_0001_T.JPG").write_bytes(b"x")
    r = api.detect_suffixes(str(tmp_path))
    assert r["thermal"] == "_T"


def test_cloud_prepare_start_emite_el_plan_por_evento(tmp_path, api_con_sink, monkeypatch):
    api, sink = api_con_sink
    (tmp_path / "DJI_0001_T.JPG").write_bytes(b"x" * 10)
    monkeypatch.setattr(api, "_destino", lambda f, p: (tmp_path, "EMPRESA--PLANTA--2026--TIPO", None))
    monkeypatch.setattr(api, "_get_auth", lambda: None)

    assert api.cloud_prepare_start(str(tmp_path), "EMPRESA--PLANTA--2026--TIPO") == {"started": True}
    done = _esperar(sink, "done")
    assert done["scope"] == "plan"
    assert done["data"]["ok"] is True
    assert done["data"]["files"] == 1


def test_cloud_prepare_start_rechaza_si_ya_hay_analisis(tmp_path, api_con_sink):
    api, _ = api_con_sink
    api._analizando = True
    r = api.cloud_prepare_start(str(tmp_path), "X")
    assert r["started"] is False


def test_cloud_prepare_sincrono_sigue_existiendo(api_con_sink):
    api, _ = api_con_sink
    assert callable(api.cloud_prepare)
