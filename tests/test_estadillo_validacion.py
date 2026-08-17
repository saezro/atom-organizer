import pandas as pd

from atom_core import estadillo


def _escribir_csv(ruta, filas):
    pd.DataFrame(filas).to_csv(ruta, sep=";", index=False)


def _fila(pb="1", vuelo="1", fecha="2026-08-17", inicio="09:12:33", final="09:41:02"):
    return {
        "PB": pb,
        "Vuelo": vuelo,
        "Fecha": fecha,
        "Hora_de_inicio": inicio,
        "Hora_final": final,
    }


def test_ok_si_cabeceras_correctas(tmp_path):
    ruta = tmp_path / "e.csv"
    _escribir_csv(ruta, [_fila(), _fila(vuelo="2")])

    res = estadillo.validar_para_subida([str(ruta)])

    assert res["ok"] is True
    assert res["error"] is None
    assert res["vuelos_detectados"] == 2
    assert len(res["vuelos"]) == 2


def test_falla_si_falta_columna_esencial(tmp_path):
    ruta = tmp_path / "e.csv"
    fila = _fila()
    del fila["PB"]
    _escribir_csv(ruta, [fila])

    res = estadillo.validar_para_subida([str(ruta)])

    assert res["ok"] is False
    assert res["error"]
    assert res["vuelos"] == []
    assert res["vuelos_detectados"] == 0


def test_falla_si_no_hay_rutas():
    res = estadillo.validar_para_subida([])

    assert res["ok"] is False
    assert res["error"]


def test_cuenta_filas_con_problemas_si_falta_fecha(tmp_path):
    ruta = tmp_path / "e.csv"
    _escribir_csv(ruta, [_fila(), _fila(vuelo="2", fecha="")])

    res = estadillo.validar_para_subida([str(ruta)])

    assert res["ok"] is True
    assert res["vuelos_detectados"] == 2
    assert res["filas_con_problemas"] == 1
