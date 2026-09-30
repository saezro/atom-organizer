"""Filtro por tarjeta del estadillo recibido por LAN (`Api._estadillo_recibir`
/ `Api._estadillo_filtrar_por_tarjeta`, `app_webview.py`): de TODOS los
vuelos que manda la app de Christian (la campaña entera) se queda solo con
los que caen dentro del horario de fotos reales de la carpeta que el kiosco
tiene elegida (la tarjeta que se está organizando).

También cubre que `sync_uid` viaja intacto de principio a fin (JSON recibido
-> CSV intermedio -> `resumen`/`validacion` que se le manda a la Suite) y que
las horas del estadillo aceptan `HH:MM` y `HH:MM:SS` indistintamente.
"""
import exif as exif_mod
import pytest

import app_webview


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Espera activa con carpeta seleccionada, sin lanzar el hilo real de
    escaneo EXIF de `estadillo_espera_iniciar` (se mockea `rango_horas_exif`,
    igual que el resto de tests de `estadillo_espera_iniciar`)."""
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    a = app_webview.Api()
    a.estadillo_espera_iniciar(str(tmp_path), {})
    return a


def _vuelo(pb, num_vuelo, hora_inicio, hora_final, fecha="2026-09-20",
           piloto="PilotoA", sync_uid=None):
    v = {
        "Trabajo": "PLANTA_A",
        "Fecha": fecha,
        "Piloto": piloto,
        "Equipo_de_vuelo": "M300",
        "PB": pb,
        "Vuelo": num_vuelo,
        "Hora_de_inicio": hora_inicio,
        "Hora_final": hora_final,
    }
    if sync_uid is not None:
        v["sync_uid"] = sync_uid
    return v


def test_sync_uid_se_conserva_y_reenvia(api, monkeypatch, tmp_path):
    """Sin carpeta con EXIF legible no se filtra nada, así que sirve para
    aislar el comportamiento de `sync_uid` sin que el filtro interfiera."""
    monkeypatch.setattr(exif_mod, "listar_horas_exif", lambda carpeta: [])

    vuelos = [_vuelo("1", "1", "09:00:00", "09:20:00", sync_uid="2026-09-20_PILOTOA_V1_H090000_PB1")]
    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    assert res["resumen"]["vuelos"][0]["pb"] == "1"
    with api._estadillo_espera_lock:
        vuelos_reenviados = api._estadillo_espera["validacion"] is not None  # solo humo
    # `validacion["vuelos"]` es lo que `_subir_estadillo_worker` reenvia a la
    # Suite via `RunReporter.estadillo`; se valida directo sobre el CSV.
    from atom_core import estadillo as estadillo_mod
    val = estadillo_mod.validar_para_subida(api._estadillo_espera["rutas"])
    assert val["vuelos"][0]["sync_uid"] == "2026-09-20_PILOTOA_V1_H090000_PB1"


def test_horas_con_y_sin_segundos_no_rompen_la_recepcion(api, monkeypatch):
    monkeypatch.setattr(exif_mod, "listar_horas_exif", lambda carpeta: [])

    vuelos = [
        _vuelo("1", "1", "09:00:00", "09:20:00"),
        _vuelo("1", "2", "09:25", "09:45"),
    ]
    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    assert res["resumen"]["vuelos_recibidos"] == 2
    assert res["resumen"]["vuelos_en_tarjeta"] == 2


def test_filtro_por_tarjeta_descarta_vuelos_sin_fotos(api, monkeypatch):
    """Fotos EXIF (mockeadas) solo dentro del horario del vuelo 1: el vuelo
    2, en otra franja horaria sin fotos, debe descartarse."""
    import datetime

    fotos = [
        datetime.datetime(2026, 9, 20, 9, 5, 0),
        datetime.datetime(2026, 9, 20, 9, 10, 0),
    ]
    monkeypatch.setattr(exif_mod, "listar_horas_exif", lambda carpeta: fotos)

    vuelos = [
        _vuelo("1", "1", "09:00:00", "09:20:00"),
        _vuelo("1", "2", "11:00:00", "11:20:00"),
    ]
    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    resumen = res["resumen"]
    assert resumen["vuelos_recibidos"] == 2
    assert resumen["vuelos_en_tarjeta"] == 1
    assert resumen["n_vuelos"] == 1
    assert resumen["vuelos"][0]["vuelo"] == "1"
    assert len(resumen["vuelos_descartados"]) == 1
    assert resumen["vuelos_descartados"][0]["vuelo"] == "2"
    assert any("descartado" in a for a in resumen["avisos"])


def test_filtro_por_tarjeta_mantiene_vuelos_dentro_del_margen(api, monkeypatch):
    """Una foto 2 min antes del inicio oficial del vuelo cae dentro del
    margen (`_ESTADILLO_FILTRO_TARJETA_MARGEN_S`, 5 min): el vuelo NO se
    descarta."""
    import datetime

    fotos = [datetime.datetime(2026, 9, 20, 8, 58, 0)]
    monkeypatch.setattr(exif_mod, "listar_horas_exif", lambda carpeta: fotos)

    vuelos = [_vuelo("1", "1", "09:00:00", "09:20:00")]
    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    assert res["resumen"]["vuelos_en_tarjeta"] == 1
    assert res["resumen"]["vuelos_descartados"] == []


def test_sin_exif_legible_no_filtra_y_avisa(api, monkeypatch):
    """Carpeta sin ninguna foto con EXIF legible (o vacía): no se filtra
    nada -se aceptan TODOS los vuelos recibidos- y queda un aviso explícito
    en el resumen."""
    monkeypatch.setattr(exif_mod, "listar_horas_exif", lambda carpeta: [])

    vuelos = [
        _vuelo("1", "1", "09:00:00", "09:20:00"),
        _vuelo("1", "2", "11:00:00", "11:20:00"),
    ]
    res = api._estadillo_recibir(vuelos)

    assert res["ok"] is True
    resumen = res["resumen"]
    assert resumen["vuelos_recibidos"] == 2
    assert resumen["vuelos_en_tarjeta"] == 2
    assert resumen["vuelos_descartados"] == []
    assert any("No se ha filtrado por tarjeta" in a for a in resumen["avisos"])


def test_sin_carpeta_seleccionada_no_filtra_y_avisa(monkeypatch, tmp_path):
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda carpeta: (0, None, None))
    a = app_webview.Api()
    a.estadillo_espera_iniciar("", {})  # sin carpeta

    vuelos = [_vuelo("1", "1", "09:00:00", "09:20:00")]
    res = a._estadillo_recibir(vuelos)

    assert res["ok"] is True
    resumen = res["resumen"]
    assert resumen["vuelos_en_tarjeta"] == resumen["vuelos_recibidos"] == 1
    assert any("No hay ninguna carpeta seleccionada" in a_ for a_ in resumen["avisos"])
