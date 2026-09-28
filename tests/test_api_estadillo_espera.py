import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app_webview
import exif as exif_mod
from atom_core import google_auth as google_auth_mod

_TZ = ZoneInfo("Europe/Madrid")


@pytest.fixture
def api():
    return app_webview.Api()


def _esperar_calculo(api, timeout=2.0):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        estado = api.estadillo_espera_estado()
        if not estado.get("fotos", {}).get("calculando", False):
            return estado
        time.sleep(0.01)
    raise AssertionError("el calculo EXIF no termino a tiempo")


def test_estado_sin_iniciar(api):
    estado = api.estadillo_espera_estado()
    assert estado["esperando"] is False
    assert estado["caducado"] is False
    assert estado["fase"] == "inactivo"
    assert estado["ultimo_contacto"] is None
    assert estado["eventos"] == []
    assert estado["carpeta_seleccionada"] is False
    assert estado["carpeta"] is None


def test_iniciar_con_carpeta_valida_marca_carpeta_seleccionada(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})

    estado = api.estadillo_espera_estado()
    assert estado["carpeta_seleccionada"] is True
    assert estado["carpeta"] == str(tmp_path)
    assert "aviso" not in estado


def test_iniciar_con_carpeta_vacia_no_marca_seleccionada_y_avisa(api, monkeypatch):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar("", {})

    estado = api.estadillo_espera_estado()
    assert estado["esperando"] is True
    assert estado["carpeta_seleccionada"] is False
    assert estado["carpeta"] == ""
    assert estado["aviso"] == "No hay carpeta seleccionada en el Organizer"


def test_iniciar_con_carpeta_inexistente_no_marca_seleccionada_y_avisa(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    carpeta_borrada = str(tmp_path / "no_existe")
    api.estadillo_espera_iniciar(carpeta_borrada, {})

    estado = api.estadillo_espera_estado()
    assert estado["carpeta_seleccionada"] is False
    assert estado["carpeta"] == carpeta_borrada
    assert estado["aviso"] == "No hay carpeta seleccionada en el Organizer"


def test_iniciar_calcula_fotos_en_hilo_aparte(api, monkeypatch, tmp_path):
    monkeypatch.setattr(
        exif_mod, "rango_horas_exif",
        lambda carpeta: (3, "2026-09-20T08:00:00", "2026-09-20T09:10:00"),
    )

    res = api.estadillo_espera_iniciar(str(tmp_path), {"planta": "KL05"})
    assert res == {"ok": True}

    estado = _esperar_calculo(api)
    assert estado["esperando"] is True
    assert estado["caducado"] is False
    assert estado["fotos"] == {
        "total": 3, "primera": "2026-09-20T08:00:00",
        "ultima": "2026-09-20T09:10:00", "calculando": False,
    }
    assert estado["inspeccion"] == {"planta": "KL05"}
    assert estado["recibido"] is False
    assert "red" in estado
    assert "ips" in estado["red"]


def test_cancelar_deja_de_esperar(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})
    _esperar_calculo(api)

    api.estadillo_espera_cancelar()

    estado = api.estadillo_espera_estado()
    assert estado["esperando"] is False
    assert estado["caducado"] is False
    assert estado["fase"] == "inactivo"
    assert any(e["tipo"] == "cancelado" for e in estado["eventos"])


def test_caduca_tras_los_segundos_indicados(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    reloj = {"t": datetime(2026, 9, 21, 10, 0, 0, tzinfo=_TZ)}
    api._estadillo_reloj = lambda: reloj["t"]

    api.estadillo_espera_iniciar(str(tmp_path), {}, segundos=60)
    estado = _esperar_calculo(api)
    assert estado["esperando"] is True
    assert estado["segundos_restantes"] == 60

    reloj["t"] = reloj["t"] + timedelta(seconds=61)

    estado = api.estadillo_espera_estado()
    assert estado["esperando"] is False
    assert estado["caducado"] is True
    assert estado["fase"] == "caducado"
    assert any(e["tipo"] == "caducado" for e in estado["eventos"])


def test_reiniciar_resetea_el_contador_de_caducidad(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    reloj = {"t": datetime(2026, 9, 21, 10, 0, 0, tzinfo=_TZ)}
    api._estadillo_reloj = lambda: reloj["t"]

    api.estadillo_espera_iniciar(str(tmp_path), {}, segundos=10)
    reloj["t"] = reloj["t"] + timedelta(seconds=8)

    api.estadillo_espera_iniciar(str(tmp_path), {}, segundos=10)
    estado = _esperar_calculo(api)

    assert estado["esperando"] is True
    assert estado["segundos_restantes"] == 10


# ---- estadillo_espera_carpeta: actualizar carpeta sin reiniciar espera ----

def test_espera_carpeta_sin_espera_activa_es_noop(api):
    res = api.estadillo_espera_carpeta("/algo")
    assert res == {"ok": False, "motivo": "No hay modo espera activo."}
    assert api.estadillo_espera_estado()["esperando"] is False


def test_espera_carpeta_actualiza_sin_reiniciar_caducidad(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    reloj = {"t": datetime(2026, 9, 21, 10, 0, 0, tzinfo=_TZ)}
    api._estadillo_reloj = lambda: reloj["t"]

    api.estadillo_espera_iniciar("", {}, segundos=600)
    _esperar_calculo(api)
    reloj["t"] = reloj["t"] + timedelta(seconds=30)

    res = api.estadillo_espera_carpeta(str(tmp_path))
    assert res == {"ok": True}

    estado = api.estadillo_espera_estado()
    assert estado["carpeta"] == str(tmp_path)
    assert estado["carpeta_seleccionada"] is True
    assert "aviso" not in estado
    # No se reinicio el contador: siguen quedando ~570s, no 600.
    assert estado["segundos_restantes"] == 570


def test_espera_carpeta_con_none_quita_la_carpeta(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})
    _esperar_calculo(api)

    res = api.estadillo_espera_carpeta(None)
    assert res == {"ok": True}

    estado = api.estadillo_espera_estado()
    assert estado["carpeta"] is None
    assert estado["carpeta_seleccionada"] is False
    assert estado["aviso"] == "No hay carpeta seleccionada en el Organizer"


def test_recibir_sin_espera_activa_no_escribe_nada(api):
    res = api._estadillo_recibir([{"PB": "1"}])
    assert res == {"ok": False, "errores": ["No hay modo espera activo."]}


def _vuelo(vuelo, inicio, final):
    return {
        "Trabajo": "KL05", "Fecha": "2026:09:20", "Piloto": "Rebeca",
        "PB": "1", "Vuelo": vuelo, "Hora_de_inicio": inicio, "Hora_final": final,
        "Termica": "1", "RGB": "1", "Vuelo_abortado": "No",
        "dron": "M300", "GB1": f"GB1_{vuelo}", "GB2": f"GB2_{vuelo}",
    }


def test_recibir_convierte_json_valida_y_marca_recibido(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "estadillos_recibidos_dir",
                         lambda: tmp_path / "estadillos_recibidos")

    api.estadillo_espera_iniciar(str(tmp_path / "fotos"), {"planta": "KL05"})
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00"), _vuelo("2", "09:25:00", "09:40:00")]

    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    # Carpeta "fotos" nunca se crea (`tmp_path / "fotos"` no existe en
    # disco): sin EXIF que leer, `_estadillo_filtrar_por_tarjeta` no filtra
    # nada -los 2 vuelos llegan igual- y avisa.
    assert res["resumen"] == {
        "planta": "KL05", "fecha": "2026:09:20", "fechas": ["2026:09:20"],
        "pilotos": ["Rebeca"], "drones": ["M300"], "n_vuelos": 2,
        "vuelos": [
            {"pb": "1", "vuelo": "1", "fecha": "2026:09:20",
             "inicio": "09:00:00", "final": "09:20:00", "cruza_medianoche": False},
            {"pb": "1", "vuelo": "2", "fecha": "2026:09:20",
             "inicio": "09:25:00", "final": "09:40:00", "cruza_medianoche": False},
        ],
        "vuelos_recibidos": 2, "vuelos_en_tarjeta": 2, "vuelos_descartados": [],
        "avisos": [
            "No se ha podido leer la hora EXIF de ninguna foto de la carpeta elegida",
            "No se ha filtrado por tarjeta: se han aceptado TODOS los vuelos recibidos.",
        ],
    }

    estado = api.estadillo_espera_estado()
    assert estado["recibido"] is True
    assert len(estado["rutas"]) == 1
    assert estado["rutas"][0].endswith(".csv")

    df = pd.read_csv(estado["rutas"][0], sep=";")
    assert "Equipo_de_vuelo" in df.columns
    assert list(df["Equipo_de_vuelo"]) == ["M300", "M300"]


def test_recibido_con_caduca_en_pasado_sigue_mostrando_recibido_ok(api, monkeypatch, tmp_path):
    """El operario puede tardar en pulsar OK tras recibir el estadillo: la
    PANTALLA no debe perder la tarjeta de "recibido" solo porque paso
    `caduca_en` (`estado.recibido` no caduca a efectos de display, ver
    `EsperaEstadillo.jsx:derivarFase`, que mira `recibido` antes que
    `caducado`). El corte real para aceptar un POST nuevo como espera
    limpia (bug 2026-09-23) esta en el 409 de `webserver.py`, no aqui."""
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "estadillos_recibidos_dir",
                         lambda: tmp_path / "estadillos_recibidos")
    reloj = {"t": datetime(2026, 9, 21, 10, 0, 0, tzinfo=_TZ)}
    api._estadillo_reloj = lambda: reloj["t"]

    api.estadillo_espera_iniciar(str(tmp_path / "fotos"), {"planta": "KL05"}, segundos=60)
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00")]
    res = api._estadillo_recibir(vuelos)
    assert res["ok"] is True

    estado = api.estadillo_espera_estado()
    assert estado["recibido"] is True
    assert estado["fase"] == "recibido_ok"

    reloj["t"] = reloj["t"] + timedelta(seconds=61)

    estado = api.estadillo_espera_estado()
    assert estado["esperando"] is True
    assert estado["caducado"] is False
    assert estado["recibido"] is True
    assert estado["fase"] == "recibido_ok"
    assert estado["segundos_restantes"] == 0


def test_iniciar_nueva_espera_reinicia_recibido_previo(api, monkeypatch, tmp_path):
    """Iniciar una espera nueva (login + seleccion de carpeta nueva, via
    `estadillo_espera_iniciar`) sustituye el estado entero: aunque la
    espera anterior tuviera `recibido: True`, la nueva arranca limpia y NO
    hereda ese `recibido` (el kiosco vuelve a admitir un POST)."""
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "estadillos_recibidos_dir",
                         lambda: tmp_path / "estadillos_recibidos")

    api.estadillo_espera_iniciar(str(tmp_path / "fotos"), {"planta": "KL05"})
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00")]
    res = api._estadillo_recibir(vuelos)
    assert res["ok"] is True
    assert api.estadillo_espera_estado()["recibido"] is True

    api.estadillo_espera_iniciar(str(tmp_path / "fotos2"), {"planta": "KL05"})
    _esperar_calculo(api)

    estado = api.estadillo_espera_estado()
    assert estado["esperando"] is True
    assert estado["caducado"] is False
    assert estado["recibido"] is False
    assert estado["fase"] != "recibido_ok"


def test_recibir_con_json_invalido_reporta_error_sin_marcar_recibido(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "estadillos_recibidos_dir",
                         lambda: tmp_path / "estadillos_recibidos")

    api.estadillo_espera_iniciar(str(tmp_path / "fotos"), {})
    _esperar_calculo(api)

    res = api._estadillo_recibir([{"nada_reconocido": "x"}])

    assert res["ok"] is False
    assert res["errores"]

    estado = api.estadillo_espera_estado()
    assert estado["recibido"] is False


# ---- fase / ultimo_contacto / eventos (actividad remota, kiosco) -----------

def test_evento_ping_actualiza_ultimo_contacto_y_pone_fase_conectado(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})
    _esperar_calculo(api)

    api._estadillo_registrar_evento("10.42.0.55", "ping")

    estado = api.estadillo_espera_estado()
    assert estado["fase"] == "conectado"
    assert estado["ultimo_contacto"] == {
        "ip": "10.42.0.55", "cuando": estado["ultimo_contacto"]["cuando"], "accion": "ping",
    }
    assert estado["eventos"][-1]["tipo"] == "ping"
    assert estado["eventos"][-1]["ip"] == "10.42.0.55"


def test_evento_ping_desde_loopback_no_actualiza_ultimo_contacto(api, monkeypatch, tmp_path):
    """127.0.0.1/::1 es el propio Chromium del kiosco, no un portatil real:
    no debe contar como "Portatil conectado" ni pasar la fase a 'conectado'."""
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})
    _esperar_calculo(api)

    api._estadillo_registrar_evento("127.0.0.1", "ping")
    api._estadillo_registrar_evento("::1", "consulta")

    estado = api.estadillo_espera_estado()
    assert estado["ultimo_contacto"] is None
    assert estado["fase"] == "esperando"


def test_evento_conectado_expira_pasados_30s(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    reloj = {"t": datetime(2026, 9, 21, 10, 0, 0, tzinfo=_TZ)}
    api._estadillo_reloj = lambda: reloj["t"]

    api.estadillo_espera_iniciar(str(tmp_path), {}, segundos=600)
    _esperar_calculo(api)
    api._estadillo_registrar_evento("10.42.0.55", "consulta")
    assert api.estadillo_espera_estado()["fase"] == "conectado"

    reloj["t"] = reloj["t"] + timedelta(seconds=31)
    assert api.estadillo_espera_estado()["fase"] == "esperando"


def test_evento_rechazado_pone_fase_rechazado(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})
    _esperar_calculo(api)

    api._estadillo_registrar_evento("10.42.0.55", "rechazado", "faltan vuelos (422)")

    estado = api.estadillo_espera_estado()
    assert estado["fase"] == "rechazado"
    assert estado["eventos"][-1]["detalle"] == "faltan vuelos (422)"


def test_solo_se_guardan_los_ultimos_10_eventos(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    api.estadillo_espera_iniciar(str(tmp_path), {})
    _esperar_calculo(api)

    for i in range(15):
        api._estadillo_registrar_evento("10.42.0.55", "consulta", str(i))

    estado = api.estadillo_espera_estado()
    assert len(estado["eventos"]) == 10
    assert estado["eventos"][-1]["detalle"] == "14"
    assert estado["eventos"][0]["detalle"] == "5"


def test_recibir_ok_guarda_resumen_en_el_estado(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "user_data_dir", lambda: tmp_path)

    api.estadillo_espera_iniciar(str(tmp_path / "fotos"), {"planta": "KL05"})
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00")]
    api._estadillo_recibir(vuelos)

    estado = api.estadillo_espera_estado()
    assert estado["fase"] == "recibido_ok"
    assert estado["resumen"]["n_vuelos"] == 1
    assert estado["resumen"]["planta"] == "KL05"


# ---- validacion vuelos<->fotos (atom_core.validacion_vuelos) --------------

def test_recibir_calcula_validacion_contra_fotos_reales(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "user_data_dir", lambda: tmp_path)
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()
    monkeypatch.setattr(
        exif_mod, "listar_horas_exif",
        lambda c: [datetime(2026, 9, 20, 9, 5, 0)] if c == str(carpeta) else [],
    )

    api.estadillo_espera_iniciar(str(carpeta), {"planta": "KL05"})
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00")]
    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    assert res["validacion"]["ok"] is True
    assert res["validacion"]["pendiente"] is False
    assert len(res["validacion"]["vuelos"]) == 1
    assert res["validacion"]["vuelos"][0]["fotos"] == 1
    assert res["validacion"]["vuelos"][0]["estado"] == "ok"

    estado = api.estadillo_espera_estado()
    assert estado["validacion"] == res["validacion"]


def test_recibir_avisa_de_vuelo_sin_fotos(api, monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "user_data_dir", lambda: tmp_path)
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()
    monkeypatch.setattr(exif_mod, "listar_horas_exif", lambda c: [])

    api.estadillo_espera_iniciar(str(carpeta), {"planta": "KL05"})
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00")]
    res = api._estadillo_recibir(vuelos)

    assert res["validacion"]["ok"] is False
    assert res["validacion"]["vuelos"][0]["estado"] == "sin_fotos"
    assert res["validacion"]["avisos"]


def test_recibir_marca_pendiente_si_el_escaneo_exif_tarda(api, monkeypatch, tmp_path):
    import threading

    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    monkeypatch.setattr(google_auth_mod, "user_data_dir", lambda: tmp_path)
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()

    liberar = threading.Event()

    def _lento(c):
        liberar.wait(timeout=2)
        return []

    monkeypatch.setattr(exif_mod, "listar_horas_exif", _lento)
    # El tope de 5s de `_estadillo_calcular_validacion` es real; se reduce
    # aqui a 0 para no alargar el test.
    monkeypatch.setattr(app_webview.Api, "_ESTADILLO_VALIDACION_TIMEOUT_S", 0)

    api.estadillo_espera_iniciar(str(carpeta), {"planta": "KL05"})
    _esperar_calculo(api)

    vuelos = [_vuelo("1", "09:00:00", "09:20:00")]
    res = api._estadillo_recibir(vuelos)

    assert res["validacion"]["pendiente"] is True
    assert res["validacion"]["ok"] is False

    liberar.set()

    fin = time.monotonic() + 2.0
    while time.monotonic() < fin:
        estado = api.estadillo_espera_estado()
        if not estado.get("validacion", {}).get("pendiente", True):
            break
        time.sleep(0.01)
    estado = api.estadillo_espera_estado()
    assert estado["validacion"]["pendiente"] is False
