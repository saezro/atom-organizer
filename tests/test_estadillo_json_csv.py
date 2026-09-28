import pandas as pd
import pytest

from atom_core import estadillo as estadillo_mod


def _vuelo(pb="1", vuelo="1", fecha="2026:09:20", inicio="09:00:00", final="09:20:00",
           piloto="Rebeca", dron="M300"):
    return {
        "Trabajo": "KL05",
        "Fecha": fecha,
        "Piloto": piloto,
        "PB": pb,
        "Vuelo": vuelo,
        "Hora_de_inicio": inicio,
        "Hora_final": final,
        "Termica": "1",
        "RGB": "1",
        "Vuelo_abortado": "No",
        "dron": dron,       # alias -> Equipo_de_vuelo
        "GB1": "GB1_" + vuelo,  # alias -> GB1/
        "GB2": "GB2_" + vuelo,  # alias -> GB2/
        "campo_desconocido": "se ignora",
    }


def test_escribir_csv_traduce_alias_y_usa_cabeceras_es(tmp_path):
    ruta = estadillo_mod.escribir_csv_desde_json([_vuelo(), _vuelo(vuelo="2")], tmp_path)

    assert ruta.endswith(".csv")
    df = pd.read_csv(ruta, sep=";")
    assert "Equipo_de_vuelo" in df.columns
    assert "GB1/" in df.columns
    assert "GB2/" in df.columns
    assert "campo_desconocido" not in df.columns
    assert list(df["Equipo_de_vuelo"]) == ["M300", "M300"]
    assert len(df) == 2


def test_escribir_csv_nombre_fichero_con_fecha_y_piloto(tmp_path):
    ruta = estadillo_mod.escribir_csv_desde_json([_vuelo()], tmp_path)

    nombre = ruta.split("/")[-1]
    assert nombre == "20260920_estadillo_Rebeca.csv"


def test_escribir_csv_sin_vuelos_lanza_value_error(tmp_path):
    with pytest.raises(ValueError):
        estadillo_mod.escribir_csv_desde_json([], tmp_path)


def test_escribir_csv_sin_columnas_reconocidas_lanza_value_error(tmp_path):
    with pytest.raises(ValueError):
        estadillo_mod.escribir_csv_desde_json([{"algo_random": "x"}], tmp_path)


def test_csv_generado_pasa_el_gate_de_validar_para_subida(tmp_path):
    ruta = estadillo_mod.escribir_csv_desde_json(
        [_vuelo(), _vuelo(vuelo="2", inicio="09:25:00", final="09:40:00")], tmp_path)

    res = estadillo_mod.validar_para_subida([ruta])

    assert res["ok"] is True
    assert res["vuelos_detectados"] == 2
    assert len(res["vuelos"]) == 2


def test_csv_generado_se_puede_leer_con_read_estadillo_info(tmp_path):
    ruta = estadillo_mod.escribir_csv_desde_json(
        [_vuelo(), _vuelo(vuelo="2", inicio="09:25:00", final="09:40:00")], tmp_path)

    info = estadillo_mod.read_estadillo_info(ruta)

    assert info["trabajo"] == "KL05"
    assert info["pilotos"] == ["Rebeca"]
    assert info["drones"] == ["M300"]
    assert info["num_vuelos"] == 2


def test_sync_uid_se_conserva_hasta_filas_para_suite(tmp_path):
    vuelo = _vuelo()
    vuelo["sync_uid"] = "2026-09-20_REBECA_V1_H090000_PB1"
    ruta = estadillo_mod.escribir_csv_desde_json([vuelo], tmp_path)

    df = pd.read_csv(ruta, sep=";")
    assert "Sync_UID" in df.columns

    res = estadillo_mod.validar_para_subida([ruta])
    assert res["ok"] is True
    assert res["vuelos"][0]["sync_uid"] == "2026-09-20_REBECA_V1_H090000_PB1"


def test_horas_con_y_sin_segundos_normalizan_igual(tmp_path):
    con_segundos = _vuelo(inicio="09:00:00", final="09:20:00")
    sin_segundos = _vuelo(vuelo="2", inicio="09:25", final="09:45")
    ruta = estadillo_mod.escribir_csv_desde_json([con_segundos, sin_segundos], tmp_path)

    res = estadillo_mod.validar_para_subida([ruta])

    assert res["ok"] is True
    assert res["vuelos"][0]["hora_inicio"] == "09:00:00"
    assert res["vuelos"][1]["hora_inicio"] == "09:25:00"
    assert res["vuelos"][1]["hora_fin"] == "09:45:00"


def test_falta_columna_esencial_no_pasa_el_gate(tmp_path):
    vuelo = _vuelo()
    del vuelo["PB"]
    ruta = estadillo_mod.escribir_csv_desde_json([vuelo], tmp_path)

    res = estadillo_mod.validar_para_subida([ruta])

    assert res["ok"] is False
    assert res["error"]
