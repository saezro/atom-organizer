from atom_core import run_reporter


def test_el_body_incluye_la_ruta_del_manifest(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None):
        capturado["metodo"] = metodo
        capturado["ruta"] = ruta
        capturado["cuerpo"] = cuerpo
        return {"ok": True}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep.estadillo(
        [{"pb": "1"}],
        planta_id=None,
        inspeccion_id=None,
        ruta_manifest="X/PREPARACION/ESTADILLOS/2026-08-17T034501Z/manifest.json",
    )

    assert capturado["ruta"] == "/api/organizer/estadillo"
    assert capturado["cuerpo"]["ruta_manifest"] == (
        "X/PREPARACION/ESTADILLOS/2026-08-17T034501Z/manifest.json"
    )
    assert capturado["cuerpo"]["vuelos"] == [{"pb": "1"}]


def test_sin_ruta_el_body_sigue_siendo_valido(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None):
        capturado["cuerpo"] = cuerpo
        return {"ok": True}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep.estadillo([{"pb": "1"}])

    assert capturado["cuerpo"]["ruta_manifest"] is None
