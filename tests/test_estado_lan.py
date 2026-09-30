"""Página de estado que ve la LAN en `/` (no kiosco, no AP) y `GET
/api/estado`: version, ID de Raspberry, usuario, wifi, batería y disco. NO
lleva el estado del modo espera del estadillo (eso es solo del kiosco, ver
`tests/test_api_estadillo_espera.py` y `EsperaEstadillo.jsx`).
"""
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from atom_core import estado_lan, webserver
from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor


class _ApiEstado:
    def __init__(self):
        self._ap_token = ""
        self.usuario = "pilotoa@ejemplo.com"
        self.conexion = {"ok": True, "tipo": "wifi", "ssid": "CASA", "senal": 80, "ip": "192.168.1.50"}

    def ping(self, who="?"):
        return {"ok": True, "msg": f"pong {who}"}

    def estadillo_espera_estado(self):
        return {"esperando": False, "caducado": False}

    def _estadillo_registrar_evento(self, ip, tipo, detalle=""):
        pass

    def _cuenta_actual(self):
        return self.usuario

    def red_conexion(self):
        return self.conexion


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head>ATOM UI</html>", encoding="utf-8")
    (tmp_path / "atom-logo.svg").write_text("<svg></svg>", encoding="utf-8")
    api = _ApiEstado()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get_json(base, ruta):
    req = urllib.request.Request(f"{base}{ruta}", method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


# ---- GET /api/estado --------------------------------------------------------

def test_api_estado_incluye_los_campos_esperados(servidor, monkeypatch):
    _, api, base = servidor
    monkeypatch.setattr(estado_lan, "serial_raspberry", lambda: "1000000012345678")
    monkeypatch.setattr(estado_lan, "bateria", lambda: None)
    monkeypatch.setattr(estado_lan, "disco_sistema", lambda: {"libre_gb": 10.0, "total_gb": 32.0})
    monkeypatch.setattr(estado_lan, "discos_externos", lambda: [])

    status, body = _get_json(base, "/api/estado")
    assert status == 200
    assert body["serial"] == "1000000012345678"
    assert body["usuario"] == "pilotoa@ejemplo.com"
    assert body["wifi"] == {"tipo": "wifi", "ssid": "CASA", "ip": "192.168.1.50"}
    assert body["bateria"] is None
    assert body["disco_sistema"] == {"libre_gb": 10.0, "total_gb": 32.0}
    assert body["discos_externos"] == []
    assert "version" in body
    assert "hostname" in body
    assert "ips" in body


def test_api_estado_con_discos_externos_y_bateria(servidor, monkeypatch):
    _, api, base = servidor
    monkeypatch.setattr(estado_lan, "bateria", lambda: {"porcentaje": 87, "estado": "Discharging"})
    monkeypatch.setattr(estado_lan, "discos_externos", lambda: [
        {"nombre": "USB1", "punto_montaje": "/media/pi/USB1", "libre_gb": 5.5, "total_gb": 32.0},
    ])

    status, body = _get_json(base, "/api/estado")
    assert status == 200
    assert body["bateria"] == {"porcentaje": 87, "estado": "Discharging"}
    assert body["discos_externos"] == [
        {"nombre": "USB1", "punto_montaje": "/media/pi/USB1", "libre_gb": 5.5, "total_gb": 32.0},
    ]


def test_api_estado_es_tolerante_a_fallos_de_lectura(servidor, monkeypatch):
    _, api, base = servidor

    def _revienta():
        raise OSError("sin permiso")

    monkeypatch.setattr(estado_lan, "serial_raspberry", _revienta)
    monkeypatch.setattr(estado_lan, "bateria", _revienta)
    monkeypatch.setattr(estado_lan, "disco_sistema", _revienta)
    monkeypatch.setattr(estado_lan, "discos_externos", _revienta)

    def _usuario_revienta():
        raise RuntimeError("sin sesion")

    api._cuenta_actual = _usuario_revienta

    status, body = _get_json(base, "/api/estado")
    assert status == 200
    assert body["serial"] == ""
    assert body["bateria"] is None
    assert body["disco_sistema"] is None
    assert body["discos_externos"] == []
    assert body["usuario"] is None


def test_api_estado_sin_token_ni_loopback(servidor, monkeypatch):
    """Sin token: es informativo, nada que proteger (mismo criterio que las
    rutas LAN abiertas del estadillo)."""
    _, api, base = servidor
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    status, body = _get_json(base, "/api/estado")
    assert status == 200
    assert body["usuario"] == "pilotoa@ejemplo.com"


# ---- pagina "/" para la LAN --------------------------------------------------

def test_pagina_lan_no_lleva_estado_del_estadillo(servidor, monkeypatch):
    _, api, base = servidor
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        cuerpo = r.read()
    assert b"ATOM UI" not in cuerpo
    assert b"ATOM" in cuerpo and b"ORGANIZER" in cuerpo
    assert b"/api/estadillo" not in cuerpo
    assert b"amarillo" not in cuerpo and b"verde" not in cuerpo and b"rojo" not in cuerpo
    assert b"/api/estado" in cuerpo
    assert b'href="/red"' in cuerpo


def test_ruta_red_sirve_lo_mismo_que_raiz_sin_autenticar(servidor, monkeypatch):
    _, api, base = servidor
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    with urllib.request.urlopen(f"{base}/red", timeout=5) as r:
        cuerpo = r.read()
    assert b"ATOM UI" not in cuerpo
    assert b"ORGANIZER" in cuerpo


def test_ruta_red_con_token_valido_sirve_la_ui_completa(servidor, monkeypatch):
    _, api, base = servidor
    monkeypatch.setattr(webserver, "_IPS_LOOPBACK", frozenset())
    api._ap_token = "tok123"
    with urllib.request.urlopen(f"{base}/red?t=tok123", timeout=5) as r:
        cuerpo = r.read()
    assert b"ATOM UI" in cuerpo


# ---------------------------------------------------------------------------
# es_raspberry: no basta con "es Linux", tiene que ser una Pi de verdad
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _limpiar_cache_es_raspberry():
    estado_lan.es_raspberry.cache_clear()
    yield
    estado_lan.es_raspberry.cache_clear()


def test_es_raspberry_con_modelo_raspberry_pi(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    modelo = tmp_path / "model"
    modelo.write_bytes(b"Raspberry Pi 4 Model B Rev 1.4\x00")
    monkeypatch.setattr(estado_lan, "_RUTAS_MODEL_DEVICETREE", (str(modelo),))
    assert estado_lan.es_raspberry() is True


def test_es_raspberry_sin_fichero_de_modelo(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        estado_lan, "_RUTAS_MODEL_DEVICETREE", (str(tmp_path / "no-existe"),)
    )
    assert estado_lan.es_raspberry() is False


def test_es_raspberry_con_otro_modelo(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    modelo = tmp_path / "model"
    modelo.write_bytes(b"Some Other Board\x00")
    monkeypatch.setattr(estado_lan, "_RUTAS_MODEL_DEVICETREE", (str(modelo),))
    assert estado_lan.es_raspberry() is False


def test_es_raspberry_no_linux_ni_siquiera_mira_el_fichero(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    modelo = tmp_path / "model"
    modelo.write_bytes(b"Raspberry Pi 4 Model B\x00")
    monkeypatch.setattr(estado_lan, "_RUTAS_MODEL_DEVICETREE", (str(modelo),))
    assert estado_lan.es_raspberry() is False
