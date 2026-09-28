"""Telemetria de intentos de PIN del kiosco (`Api.pin_telemetria`).

Solo mide contadores/timing de un intento COMPLETO de KioskLock.jsx (paso
'verificar'), NUNCA el PIN tecleado. Se escribe como JSONL en
`user_log_dir()`, reusando la misma carpeta que el historial de procesos
(`logs_listar`/`logs_leer`), con un nombre que ese listado no reconoce.
"""
from __future__ import annotations

import json
import os

import external_tools
from app_webview import Api, _LOG_TELEMETRIA_PIN, _LIMITE_TELEMETRIA_PIN


def _api(monkeypatch, carpeta):
    monkeypatch.setattr(external_tools, "user_log_dir", lambda: str(carpeta), raising=False)
    return Api()


def _payload(**overrides):
    base = {
        "ts": "2026-09-22T10:00:00+02:00",
        "ok": True,
        "n_intento": 1,
        "toques_aceptados": 4,
        "toques_descartados_debounce": 0,
        "borrados": 0,
        "intervalos_ms": [120.0, 130.5, 110.0],
        "duracion_total_ms": 900.0,
    }
    base.update(overrides)
    return base


def _leer_ruta(carpeta):
    return os.path.join(str(carpeta), _LOG_TELEMETRIA_PIN)


def test_escribe_una_linea_jsonl_valida(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    res = api.pin_telemetria(_payload())

    assert res["ok"] is True
    ruta = _leer_ruta(carpeta)
    assert os.path.isfile(ruta)
    with open(ruta, encoding="utf-8") as f:
        lineas = f.readlines()
    assert len(lineas) == 1
    linea = json.loads(lineas[0])
    assert linea["ok"] is True
    assert linea["n_intento"] == 1
    assert linea["toques_aceptados"] == 4
    assert "ts_servidor" in linea


def test_hace_append_en_llamadas_sucesivas(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    api.pin_telemetria(_payload(n_intento=1))
    api.pin_telemetria(_payload(n_intento=2, ok=False))

    with open(_leer_ruta(carpeta), encoding="utf-8") as f:
        lineas = [json.loads(l) for l in f.readlines()]
    assert len(lineas) == 2
    assert [l["n_intento"] for l in lineas] == [1, 2]


def test_descarta_claves_no_esperadas(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    res = api.pin_telemetria(_payload(pin="1234", longitud_pin=4, digitos=["1", "2", "3", "4"]))

    assert res["ok"] is True
    with open(_leer_ruta(carpeta), encoding="utf-8") as f:
        linea = json.loads(f.readline())
    assert "pin" not in linea
    assert "longitud_pin" not in linea
    assert "digitos" not in linea
    assert "1234" not in json.dumps(linea)


def test_nunca_guarda_el_pin_ni_su_longitud(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    api.pin_telemetria(_payload())

    with open(_leer_ruta(carpeta), encoding="utf-8") as f:
        crudo = f.read()
    for clave_prohibida in ("pin", "digito", "longitud"):
        assert clave_prohibida not in crudo.lower()


def test_basura_no_revienta_y_no_escribe(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    assert api.pin_telemetria(None)["ok"] is False
    assert api.pin_telemetria("no soy un dict")["ok"] is False
    assert api.pin_telemetria([1, 2, 3])["ok"] is False
    assert api.pin_telemetria({})["ok"] is False  # falta ts/ok obligatorios
    assert api.pin_telemetria({"ts": "x"})["ok"] is False  # falta ok

    assert not os.path.isfile(_leer_ruta(carpeta))


def test_tipos_incorrectos_se_descartan_sin_reventar(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    res = api.pin_telemetria({
        "ts": "2026-09-22T10:00:00+02:00",
        "ok": True,
        "n_intento": "no es un numero",
        "toques_aceptados": 4,
        "intervalos_ms": "no es una lista",
        "duracion_total_ms": 900.0,
    })

    assert res["ok"] is True
    with open(_leer_ruta(carpeta), encoding="utf-8") as f:
        linea = json.loads(f.readline())
    assert "n_intento" not in linea
    assert "intervalos_ms" not in linea
    assert linea["toques_aceptados"] == 4


def test_rota_al_superar_el_limite_de_tamano(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    carpeta.mkdir(parents=True)
    ruta = _leer_ruta(carpeta)
    # Fichero ya por encima del limite antes de la escritura que dispara la rotacion.
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("x" * (_LIMITE_TELEMETRIA_PIN + 1))
    api = _api(monkeypatch, carpeta)

    api.pin_telemetria(_payload())

    rotado = ruta + ".1"
    assert os.path.isfile(rotado)
    assert os.path.getsize(rotado) > _LIMITE_TELEMETRIA_PIN
    with open(ruta, encoding="utf-8") as f:
        lineas = f.readlines()
    assert len(lineas) == 1  # el fichero activo solo tiene la linea nueva
