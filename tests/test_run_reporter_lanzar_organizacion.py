import json
import urllib.error

from atom_core import run_reporter


def test_exito_manda_inspeccion_id_y_devuelve_la_respuesta(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None, **kwargs):
        capturado["metodo"] = metodo
        capturado["ruta"] = ruta
        capturado["cuerpo"] = cuerpo
        capturado["kwargs"] = kwargs
        return {"ok": True, "operacion_id": 42, "destino": "gs://bucket/X"}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    resultado = rep.lanzar_organizacion(inspeccion_id=7)

    assert capturado["metodo"] == "POST"
    assert capturado["ruta"] == "/api/organizer/lanzar-desde-subida"
    assert capturado["cuerpo"] == {"inspeccion_id": 7}
    # lanzar_organizacion pide el cuerpo de error tal cual (400/409 con causa),
    # no un None que se traga la razón del fallo.
    assert capturado["kwargs"] == {"detallar_error": True}
    assert resultado == {"ok": True, "operacion_id": 42, "destino": "gs://bucket/X"}


def test_incluye_opcionales_solo_si_no_son_falsy(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None, **kwargs):
        capturado["cuerpo"] = cuerpo
        return {"ok": True}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep.lanzar_organizacion(inspeccion_id=7, sin_rotacion=True, giro_tiff="cw")

    assert capturado["cuerpo"] == {
        "inspeccion_id": 7,
        "sin_rotacion": True,
        "giro_tiff": "cw",
    }


def test_sin_opcionales_no_van_en_el_body(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None, **kwargs):
        capturado["cuerpo"] = cuerpo
        return {"ok": True}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep.lanzar_organizacion(inspeccion_id=7, sin_rotacion=False, giro_tiff=None)

    assert capturado["cuerpo"] == {"inspeccion_id": 7}


def test_peticion_devuelve_none_no_lanza(monkeypatch):
    def fake_peticion(self, metodo, ruta, cuerpo=None, **kwargs):
        return None

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    resultado = rep.lanzar_organizacion(inspeccion_id=7)

    assert resultado is None


def test_peticion_con_error_se_propaga_tal_cual(monkeypatch):
    def fake_peticion(self, metodo, ruta, cuerpo=None, **kwargs):
        return {"ok": False, "error": "ya hay una operacion en curso"}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    resultado = rep.lanzar_organizacion(inspeccion_id=7)

    assert resultado == {"ok": False, "error": "ya hay una operacion en curso"}


class _FakeHTTPResponseBody:
    """Simula el objeto que `urllib.error.HTTPError.read()` devuelve."""

    def __init__(self, cuerpo: bytes) -> None:
        self._cuerpo = cuerpo

    def read(self) -> bytes:
        return self._cuerpo

    def close(self) -> None:
        pass


def _fake_urlopen_que_lanza(codigo: int, cuerpo_bytes: bytes):
    """Devuelve un stand-in de `urllib.request.urlopen` que siempre falla con
    un `HTTPError` con el código y cuerpo indicados (leíble vía `.read()`,
    igual que el objeto real de urllib)."""

    def _urlopen(req, timeout=None):
        error = urllib.error.HTTPError(
            req.full_url, codigo, "error", {}, _FakeHTTPResponseBody(cuerpo_bytes)
        )
        raise error

    return _urlopen


def test_lanzar_organizacion_409_con_causa_json_llega_con_su_error(monkeypatch):
    """Un 409 'operacion-en-curso' de la Suite debe llegar con su causa, no
    colapsarse a None: es justo lo que necesita leer el usuario."""
    cuerpo = json.dumps({"ok": False, "error": "operacion-en-curso"}).encode("utf-8")
    monkeypatch.setattr(
        run_reporter.urllib.request, "urlopen", _fake_urlopen_que_lanza(409, cuerpo)
    )

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep._base = "https://suite.atom-uas.com"
    rep._auth = None
    rep._secreto = "secreto-test"
    rep._timeout = 5

    resultado = rep.lanzar_organizacion(inspeccion_id=7)

    assert resultado is not None
    assert resultado["ok"] is False
    assert resultado["error"] == "operacion-en-curso"


def test_lanzar_organizacion_500_sin_cuerpo_json_cae_al_generico(monkeypatch):
    """Un 500 con cuerpo no-JSON (página de error, HTML, etc.) no debe
    reventar `_peticion`: cae al mensaje genérico 'HTTP <codigo>'."""
    cuerpo = b"<html>boom</html>"
    monkeypatch.setattr(
        run_reporter.urllib.request, "urlopen", _fake_urlopen_que_lanza(500, cuerpo)
    )

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep._base = "https://suite.atom-uas.com"
    rep._auth = None
    rep._secreto = "secreto-test"
    rep._timeout = 5

    resultado = rep.lanzar_organizacion(inspeccion_id=7)

    assert resultado == {"ok": False, "error": "HTTP 500"}


def test_subida_no_pide_detallar_error_y_sigue_devolviendo_none_en_fallo(monkeypatch):
    """`subida()` no pasa `detallar_error`, así que ante un HTTPError debe
    seguir siendo fail-open y devolver None (el resto de métodos no cambian
    de comportamiento por la nueva opción)."""
    cuerpo = json.dumps({"ok": False, "error": "no-deberia-verse"}).encode("utf-8")
    monkeypatch.setattr(
        run_reporter.urllib.request, "urlopen", _fake_urlopen_que_lanza(409, cuerpo)
    )

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep._base = "https://suite.atom-uas.com"
    rep._auth = None
    rep._secreto = "secreto-test"
    rep._timeout = 5

    resultado = rep.subida(inspeccion_id=7, estado="error")

    assert resultado is None
