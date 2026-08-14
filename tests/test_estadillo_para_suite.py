"""`atom_core.estadillo.filas_para_suite`: DataFrame combinado -> filas para
`POST /api/organizer/estadillo` (`RunReporter.estadillo`).

Cubre lo mínimo que el backend necesita para no reventar ni recibir basura:
conversión de fecha EXIF (`2026:03:17`), descarte de filas sin PB/hora de
inicio, normalización de hora corta (`HH:MM` -> `HH:MM:SS`) y que el mapeo de
columnas usa el mismo `Utils.get_nombres_columnas` que el resto del módulo
(nombres EN también resuelven)."""
import pandas as pd

from atom_core.estadillo import COLUMNA_ORIGEN, filas_para_suite


def _df(filas, columnas):
    return pd.DataFrame(filas, columns=columnas)


def test_fecha_exif_dos_puntos_se_normaliza_a_guiones():
    df = _df(
        [["PB1", "V1", "2026:03:17", "08:00:00", "09:00:00", "Ana", "DJI"]],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final", "Piloto", "Equipo_de_vuelo"],
    )
    filas = filas_para_suite(df)
    assert len(filas) == 1
    assert filas[0]["fecha"] == "2026-03-17"


def test_fecha_ya_con_guiones_o_barras_tambien_normaliza():
    df = _df(
        [
            ["PB1", "V1", "2026-03-17", "08:00:00", "09:00:00"],
            ["PB2", "V2", "2026/03/18", "08:00:00", "09:00:00"],
        ],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    filas = filas_para_suite(df)
    assert [f["fecha"] for f in filas] == ["2026-03-17", "2026-03-18"]


def test_fila_sin_pb_se_descarta():
    df = _df(
        [
            ["", "V1", "2026:03:17", "08:00:00", "09:00:00"],
            ["PB2", "V2", "2026:03:17", "08:00:00", "09:00:00"],
        ],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    filas = filas_para_suite(df)
    assert len(filas) == 1
    assert filas[0]["pb"] == "PB2"


def test_fila_sin_hora_inicio_se_descarta():
    df = _df(
        [
            ["PB1", "V1", "2026:03:17", "", "09:00:00"],
            ["PB2", "V2", "2026:03:17", "08:00:00", "09:00:00"],
        ],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    filas = filas_para_suite(df)
    assert len(filas) == 1
    assert filas[0]["pb"] == "PB2"


def test_fila_sin_fecha_parseable_se_descarta():
    df = _df(
        [["PB1", "V1", "no-es-una-fecha", "08:00:00", "09:00:00"]],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    assert filas_para_suite(df) == []


def test_hora_corta_hhmm_se_normaliza_a_hhmmss():
    df = _df(
        [["PB1", "V1", "2026:03:17", "08:30", "09:30"]],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    filas = filas_para_suite(df)
    assert filas[0]["hora_inicio"] == "08:30:00"
    assert filas[0]["hora_fin"] == "09:30:00"


def test_hora_final_vacia_queda_none_sin_descartar_la_fila():
    df = _df(
        [["PB1", "V1", "2026:03:17", "08:30:00", ""]],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    filas = filas_para_suite(df)
    assert len(filas) == 1
    assert filas[0]["hora_fin"] is None


def test_mapeo_de_columnas_piloto_equipo_pb_vuelo_horas():
    df = _df(
        [["PB9", "V9", "2026:03:17", "08:00:00", "09:00:00", "Ana", "DJI M300"]],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final", "Piloto", "Equipo_de_vuelo"],
    )
    filas = filas_para_suite(df)
    assert filas[0] == {
        "fecha": "2026-03-17",
        "piloto": "Ana",
        "equipo_vuelo": "DJI M300",
        "pb": "PB9",
        "num_vuelo": "V9",
        "hora_inicio": "08:00:00",
        "hora_fin": "09:00:00",
        "origen": None,
    }


def test_mapeo_columnas_en_ingles_tambien_resuelve():
    """`Utils.get_nombres_columnas` también reconoce la variante EN
    (`Pilot`, `Flight_equipment`, `Initial_hour`, `Final_hour`, `Flight`)."""
    df = _df(
        [["PB1", "V1", "2026:03:17", "08:00:00", "09:00:00", "Ana", "DJI"]],
        ["PB", "Flight", "Date", "Initial_hour", "Final_hour", "Pilot", "Flight_equipment"],
    )
    filas = filas_para_suite(df)
    assert len(filas) == 1
    assert filas[0]["num_vuelo"] == "V1"
    assert filas[0]["piloto"] == "Ana"
    assert filas[0]["equipo_vuelo"] == "DJI"


def test_origen_desde_columna_interna_de_combinar_estadillos():
    df = _df(
        [["PB1", "V1", "2026:03:17", "08:00:00", "09:00:00", "a.csv"]],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final", COLUMNA_ORIGEN],
    )
    filas = filas_para_suite(df)
    assert filas[0]["origen"] == "a.csv"


def test_origen_por_fila_explicito_tiene_prioridad():
    df = _df(
        [
            ["PB1", "V1", "2026:03:17", "08:00:00", "09:00:00"],
            ["PB2", "V2", "2026:03:17", "08:00:00", "09:00:00"],
        ],
        ["PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final"],
    )
    filas = filas_para_suite(df, origen_por_fila=["x.csv", "y.csv"])
    assert [f["origen"] for f in filas] == ["x.csv", "y.csv"]
