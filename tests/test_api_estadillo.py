import pandas as pd
import pytest

import app_webview


@pytest.fixture
def api():
    return app_webview.Api()


def _csv(tmp_path, nombre="e.csv"):
    ruta = tmp_path / nombre
    pd.DataFrame(
        {
            "PB": ["1"],
            "Vuelo": ["1"],
            "Fecha": ["2026-08-17"],
            "Hora_de_inicio": ["09:12:33"],
            "Hora_final": ["09:41:02"],
        }
    ).to_csv(ruta, sep=";", index=False)
    return str(ruta)


def test_validar_devuelve_resumen_sin_las_filas(api, tmp_path):
    res = api.estadillo_validar([_csv(tmp_path)])

    assert res["ok"] is True
    assert res["vuelos_detectados"] == 1
    assert "vuelos" not in res


def test_validar_reporta_error_si_no_carga(api, tmp_path):
    ruta = tmp_path / "malo.csv"
    ruta.write_text("no;son;cabeceras\n1;2;3\n", encoding="utf-8")

    res = api.estadillo_validar([str(ruta)])

    assert res["ok"] is False
    assert res["error"]


def test_subir_no_arranca_si_la_validacion_falla(api, tmp_path):
    ruta = tmp_path / "malo.csv"
    ruta.write_text("no;son;cabeceras\n1;2;3\n", encoding="utf-8")

    res = api.estadillo_subir("MI_PLANTA", [str(ruta)])

    assert res["started"] is False
    assert res["reason"]


def test_subir_arranca_si_la_validacion_pasa(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_subir_estadillo_worker", lambda *a, **k: None)

    res = api.estadillo_subir("MI_PLANTA", [_csv(tmp_path)])

    assert res["started"] is True
