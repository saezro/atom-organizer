"""Rutas `/api/control/revision/*` y `revision` en `/api/control/estado`."""
import pytest

from tests.test_webserver_control import (  # noqa: F401 — fixtures y helpers compartidos
    PIN_CABECERA, _fijar_pin, _get, _post_json, _reset_control_pin_lockout, servidor,
)

H = {PIN_CABECERA: "1234"}


def test_estado_incluye_revision_esperando(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    api.revision_indice_estado = lambda: {"ok": True, "activa": True, "estado": "esperando",
                                          "deadline_epoch": 9.0, "inactividad_s": 1.0}
    status, body, _ = _get(base, "/api/control/estado", H)
    assert status == 200
    assert body["revision"]["estado"] == "esperando"


def test_asignar_llega_a_la_cola(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    api.revision_indice_estado = lambda: {"ok": True, "activa": True, "estado": "esperando",
                                          "deadline_epoch": 9.0, "inactividad_s": 1.0}
    llam = []
    api.revision_indice_asignar = lambda claves, fila=None, ruta_salida="": (
        llam.append((claves, fila)) or {"ok": True, "resultado": {}})
    status, body, _ = _post_json(base, "/api/control/revision/asignar",
                                 {"claves": ["a", "b"], "fila": 3}, H)
    assert status == 200 and body["ok"] is True
    assert llam == [(["a", "b"], 3)]


@pytest.mark.parametrize("ruta,payload", [
    ("asignar", {"claves": ["a"], "fila": 1}), ("aceptar", {"ids": ["x"]}),
    ("continuar", {"modo": "tal_cual"}),
])
def test_sin_pausa_da_409(servidor, ruta, payload):
    _, api, base = servidor
    _fijar_pin(api)
    api.revision_indice_estado = lambda: {"ok": True, "activa": False, "estado": None,
                                          "deadline_epoch": None, "inactividad_s": None}
    status, body, _ = _post_json(base, f"/api/control/revision/{ruta}", payload, H)
    assert status == 409
    assert body == {"ok": False, "codigo": "sin_revision"}


def test_error_del_metodo_se_enseña(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    api.revision_indice_estado = lambda: {"ok": True, "activa": True, "estado": "esperando",
                                          "deadline_epoch": 9.0, "inactividad_s": 1.0}
    api.revision_indice_aceptar = lambda ids, ruta_salida="": {"ok": False, "error": "boom"}
    status, body, _ = _post_json(base, "/api/control/revision/aceptar", {"ids": ["x"]}, H)
    assert status == 400 and body["error"] == "boom"


def test_calendario_get(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    api.revision_indice_calendario = lambda ruta_salida="": {"ok": True, "dias": []}
    status, body, _ = _get(base, "/api/control/revision/calendario", H)
    assert status == 200 and body["ok"] is True


@pytest.mark.parametrize("metodo,ruta", [
    ("get", "/api/control/revision/estado"), ("get", "/api/control/revision/calendario"),
    ("post", "/api/control/revision/asignar"), ("post", "/api/control/revision/aceptar"),
    ("post", "/api/control/revision/continuar"),
])
def test_exigen_pin(servidor, metodo, ruta):
    _, api, base = servidor
    _fijar_pin(api)
    if metodo == "get":
        status, body, _ = _get(base, ruta)
    else:
        status, body, _ = _post_json(base, ruta, {})
    assert status == 401 and body["codigo"] == "pin_invalido"
