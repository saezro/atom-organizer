import json
import threading
import urllib.error
import urllib.request

import pytest

from atom_core import webserver
from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor


class _ApiEstadillo:
    def __init__(self):
        self.estado = {"esperando": False, "caducado": False}
        self.recibir_llamadas = []
        self.recibir_resultado = {
            "ok": True,
            "resumen": {"planta": "KL05", "fecha": "2026:09:20",
                        "pilotos": ["Rebeca"], "drones": ["M300"], "n_vuelos": 2},
        }
        self.eventos_registrados = []

    def ping(self, who="?"):
        return {"ok": True, "msg": f"pong {who}"}

    def estadillo_espera_estado(self):
        return self.estado

    def _estadillo_recibir(self, vuelos):
        self.recibir_llamadas.append(vuelos)
        return self.recibir_resultado

    def _estadillo_registrar_evento(self, ip, tipo, detalle=""):
        self.eventos_registrados.append({"ip": ip, "tipo": tipo, "detalle": detalle})

    def _cuenta_actual(self):
        return None

    def red_conexion(self):
        return {"ok": True, "tipo": "ninguna", "ssid": "", "senal": None, "ip": ""}


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head>ATOM UI</html>", encoding="utf-8")
    (tmp_path / "atom-logo.svg").write_text("<svg></svg>", encoding="utf-8")
    api = _ApiEstadillo()
    sink = QueueSink()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, sink)
    srv.sink = sink  # expuesto solo para que los tests suscriban la cola SSE
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base, ruta, headers=None):
    req = urllib.request.Request(f"{base}{ruta}", headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def _post_json(base, ruta, payload, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        f"{base}{ruta}", data=json.dumps(payload).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ---- GET /api/estadillo/espera --------------------------------------------

def test_espera_sin_modo_activo_da_409(servidor):
    _, _, base = servidor
    status, body, headers = _get(base, "/api/estadillo/espera")
    assert status == 409
    assert body == {
        "ok": False, "codigo": "sin_espera_activa",
        "motivo": body["motivo"],
        "esperando": False, "caducado": False,
        "carpeta_seleccionada": False, "carpeta": None,
        "estadillo_en_carpeta": None,
    }
    assert body["motivo"]
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_espera_caducada_da_409_con_caducado_true(servidor):
    _, api, base = servidor
    api.estado = {"esperando": False, "caducado": True}
    status, body, _ = _get(base, "/api/estadillo/espera")
    assert status == 409
    assert body == {
        "ok": False, "codigo": "espera_caducada",
        "motivo": body["motivo"],
        "esperando": False, "caducado": True,
        "carpeta_seleccionada": False, "carpeta": None,
        "estadillo_en_carpeta": None,
    }
    assert body["motivo"]


def test_espera_activa_da_200_con_datos(servidor):
    _, api, base = servidor
    api.estado = {
        "esperando": True, "caducado": False,
        "inspeccion": {"planta": "KL05"},
        "fotos": {"total": 10, "primera": "2026-09-20T08:00:00",
                  "ultima": "2026-09-20T09:00:00", "calculando": False},
        "recibido": False, "caduca_en": "2026-09-20T10:00:00+02:00",
        "segundos_restantes": 500,
        "red": {"hostname": "raspi-kl05", "puerto": 80, "ips": [], "url": "http://raspi-kl05"},
        "carpeta_seleccionada": True, "carpeta": "/home/pi/vuelo/KL05",
    }
    status, body, _ = _get(base, "/api/estadillo/espera")
    assert status == 200
    assert body["esperando"] is True
    assert body["inspeccion"] == {"planta": "KL05"}
    assert body["fotos"]["total"] == 10
    assert body["segundos_restantes"] == 500
    assert body["red"]["hostname"] == "raspi-kl05"
    assert body["carpeta_seleccionada"] is True
    assert body["carpeta"] == "/home/pi/vuelo/KL05"
    assert "aviso" not in body


def test_espera_activa_sin_carpeta_avisa(servidor):
    _, api, base = servidor
    api.estado = {
        "esperando": True, "caducado": False,
        "inspeccion": {}, "fotos": {"total": 0, "primera": None, "ultima": None, "calculando": False},
        "recibido": False, "caduca_en": "2026-09-20T10:00:00+02:00",
        "segundos_restantes": 500,
        "red": {"hostname": "raspi-kl05", "puerto": 80, "ips": [], "url": "http://raspi-kl05"},
        "carpeta_seleccionada": False, "carpeta": None,
        "aviso": "No hay carpeta seleccionada en el Organizer",
    }
    status, body, _ = _get(base, "/api/estadillo/espera")
    assert status == 200
    assert body["carpeta_seleccionada"] is False
    assert body["carpeta"] is None
    assert body["aviso"] == "No hay carpeta seleccionada en el Organizer"


# ---- GET /api/estadillo/ping ------------------------------------------------

def test_ping_siempre_200(servidor):
    _, api, base = servidor
    status, body, headers = _get(base, "/api/estadillo/ping")
    assert status == 200
    assert body["organizer"] is True
    assert "version" in body
    assert body["esperando"] is False
    assert headers["Access-Control-Allow-Origin"] == "*"

    api.estado = {"esperando": True, "caducado": False}
    status, body, _ = _get(base, "/api/estadillo/ping")
    assert status == 200
    assert body["esperando"] is True


# ---- POST /api/estadillo ----------------------------------------------------

def test_post_sin_modo_activo_da_409(servidor):
    _, api, base = servidor
    status, body = _post_json(base, "/api/estadillo", {"vuelos": [{"PB": "1"}]})
    assert status == 409
    assert body["ok"] is False
    assert body["codigo"] == "sin_espera_activa"
    assert body["motivo"]
    assert body["esperando"] is False
    assert api.recibir_llamadas == []


def test_post_con_espera_activa_llega_a_la_api_y_devuelve_200(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    vuelos = [{"PB": "1", "Vuelo": "1"}, {"PB": "1", "Vuelo": "2"}]
    status, body = _post_json(base, "/api/estadillo", {"vuelos": vuelos})

    assert status == 200
    assert body["ok"] is True
    assert body["resumen"]["n_vuelos"] == 2
    assert api.recibir_llamadas == [vuelos]


def test_post_remoto_emite_atom_control_ui_api_remota(servidor, monkeypatch):
    # Caso principal (app de Christian): un POST a `/api/estadillo` desde un
    # cliente NO loopback enciende el marco azul del kiosco, igual que
    # `/api/control/*` (`webserver._marcar_si_api_remota`).
    srv, api, base = servidor
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}
    q = srv.sink.subscribe()
    status, _ = _post_json(base, "/api/estadillo", {"vuelos": [{"PB": "1"}]})
    assert status == 200
    evento, detalle = q.get(timeout=5)
    assert evento == "atom:control_ui"
    assert detalle == {"accion": "api_remota"}


def test_post_local_no_emite_atom_control_ui(servidor):
    # El propio kiosco (loopback) no debe encender su propio marco remoto.
    srv, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}
    q = srv.sink.subscribe()
    status, _ = _post_json(base, "/api/estadillo", {"vuelos": [{"PB": "1"}]})
    assert status == 200
    with pytest.raises(Exception):
        q.get(timeout=0.3)


def test_post_tras_recibido_da_409_y_no_reintenta(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": True,
                  "carpeta_seleccionada": True}

    status, body = _post_json(base, "/api/estadillo", {"vuelos": [{"PB": "1"}]})

    assert status == 409
    assert body["ok"] is False
    assert body["codigo"] == "estadillo_ya_recibido"
    assert body["motivo"]
    assert body["recibido"] is True
    assert api.recibir_llamadas == []


def test_post_sin_carpeta_seleccionada_se_recibe_igual_y_avisa_pendiente_carpeta(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": False}

    vuelos = [{"PB": "1", "Vuelo": "1"}]
    status, body = _post_json(base, "/api/estadillo", {"vuelos": vuelos})

    # El estadillo se recibe SIEMPRE, aunque no haya carpeta elegida aun:
    # se guarda y queda asociado a la espera (ver `_estadillo_recibir` /
    # `estadillo_espera_carpeta`), nunca se pierde.
    assert status == 200
    assert body["ok"] is True
    assert body["pendiente_carpeta"] is True
    assert api.recibir_llamadas == [vuelos]


def test_post_sin_vuelos_da_422(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    status, body = _post_json(base, "/api/estadillo", {})
    assert status == 422
    assert body["ok"] is False
    assert body["codigo"] == "faltan_vuelos"
    assert body["motivo"]


def test_post_content_type_incorrecto_da_415(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    status, body = _post_json(
        base, "/api/estadillo", {"vuelos": [{"PB": "1"}]},
        headers={"Content-Type": "text/plain"})
    assert status == 415
    assert body["ok"] is False
    assert body["codigo"] == "content_type_invalido"
    assert body["motivo"]


def test_post_body_demasiado_grande_da_413(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    req = urllib.request.Request(
        f"{base}/api/estadillo", data=b"{}",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(1024 * 1024 + 1)},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 413
    body = json.loads(exc.value.read())
    assert body["ok"] is False
    assert body["codigo"] == "cuerpo_demasiado_grande"
    assert body["motivo"]


def test_post_json_invalido_da_400(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    req = urllib.request.Request(
        f"{base}/api/estadillo", data=b"{no es json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400
    body = json.loads(exc.value.read())
    assert body["ok"] is False
    assert body["codigo"] == "json_invalido"
    assert body["motivo"]


def test_post_content_length_invalido_da_400(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    req = urllib.request.Request(
        f"{base}/api/estadillo", data=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "no-numero"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400
    body = json.loads(exc.value.read())
    assert body["ok"] is False
    assert body["codigo"] == "content_length_invalido"
    assert body["motivo"]


def test_post_error_interno_da_500(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    def _explota(vuelos):
        raise RuntimeError("boom")

    api._estadillo_recibir = _explota

    status, body = _post_json(base, "/api/estadillo", {"vuelos": [{"PB": "1"}]})
    assert status == 500
    assert body["ok"] is False
    assert body["codigo"] == "error_interno"
    assert body["motivo"]


def test_post_validacion_fallida_da_422(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}
    api.recibir_resultado = {"ok": False, "errores": ["cabecera de vuelo desconocida"]}

    status, body = _post_json(base, "/api/estadillo", {"vuelos": [{"PB": "1"}]})
    assert status == 422
    assert body["ok"] is False
    assert body["codigo"] == "validacion_estadillo"
    assert body["motivo"] == "cabecera de vuelo desconocida"
    assert body["errores"] == ["cabecera de vuelo desconocida"]


def test_post_sin_token_ni_origin_funciona_igual(servidor):
    """Sin token: a diferencia de los metodos `/api/<metodo>` normales, esta
    ruta no exige ni token ni Origin permitido (decision de diseño)."""
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "carpeta_seleccionada": True}

    req = urllib.request.Request(
        f"{base}/api/estadillo",
        data=json.dumps({"vuelos": [{"PB": "1"}]}).encode(),
        headers={"Content-Type": "application/json", "Origin": "https://cualquiera.example"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


# ---- OPTIONS (preflight CORS) ----------------------------------------------

def test_options_estadillo_espera_responde_204_con_cors(servidor):
    _, _, base = servidor
    req = urllib.request.Request(f"{base}/api/estadillo/espera", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert "POST" in r.headers["Access-Control-Allow-Methods"]


def test_options_ruta_no_lan_da_404(servidor):
    _, _, base = servidor
    req = urllib.request.Request(f"{base}/api/ping", method="OPTIONS")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 404


# ---- Host sin puerto --------------------------------------------------------

def test_host_sin_puerto_no_rompe_ruta_estadillo(servidor):
    _, api, base = servidor
    api.estado = {"esperando": True, "caducado": False, "recibido": False,
                  "inspeccion": {}, "fotos": {}, "red": {}}
    status, body, _ = _get(base, "/api/estadillo/espera", headers={"Host": "organizer.local"})
    assert status == 200
    assert body["esperando"] is True


def test_host_sin_puerto_no_rompe_metodo_normal(servidor):
    _, _, base = servidor
    req = urllib.request.Request(
        f"{base}/api/ping",
        data=json.dumps({"args": []}).encode(),
        headers={"Content-Type": "application/json", "Host": "organizer.local"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def test_host_sin_puerto_sirve_index_desde_loopback(servidor):
    _, _, base = servidor
    req = urllib.request.Request(f"{base}/", headers={"Host": "organizer.local"}, method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert b"ATOM UI" in r.read()


# ---- pagina LAN simple vs UI completa ---------------------------------------

def test_raiz_desde_loopback_sirve_la_ui_completa(servidor):
    _, _, base = servidor
    status, _, _ = (None, None, None)
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        cuerpo = r.read()
    assert b"ATOM UI" in cuerpo


def test_raiz_desde_no_loopback_sirve_pagina_simple(servidor, monkeypatch):
    _, api, base = servidor
    # No hay forma de que una conexion real por socket llegue como IP no
    # loopback en este entorno de test: se fuerza tratando 127.0.0.1 como si
    # no lo fuera (mismo mecanismo que usa el propio servidor: `_es_local`
    # consulta `_IPS_LOOPBACK`).
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as r:
            status = r.status
            cuerpo = r.read()
    finally:
        pass
    assert status == 200
    assert b"ATOM UI" not in cuerpo
    assert b"ATOM" in cuerpo and b"ORGANIZER" in cuerpo
    assert b"/api/estadillo" not in cuerpo  # el modo espera es solo del kiosco
    assert b"/api/estado" in cuerpo
    assert b'href="/red"' in cuerpo


def test_raiz_desde_no_loopback_con_token_valido_sirve_ui(servidor, monkeypatch):
    _, api, base = servidor
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    api._ap_token = "tok123"
    with urllib.request.urlopen(f"{base}/?t=tok123", timeout=5) as r:
        cuerpo = r.read()
    assert b"ATOM UI" in cuerpo
