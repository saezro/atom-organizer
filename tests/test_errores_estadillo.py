"""Errores de estadillo: dicen qué falla y de qué fichero viene."""
import types

import pandas as pd
import pytest

import pipeline
from atom_core import estadillo as est
from atom_core.errores_estadillo import ErrorEstadillo
from atom_core.indice import _ventanas_por_vuelo

COLS = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha", "Hora_de_inicio": "Hora_de_inicio",
        "Hora_final": "Hora_final"}


class _Pipe:
    ventana_horaria_vuelo = pipeline.GenStructFolder.ventana_horaria_vuelo


def _df(hora_inicio):
    return pd.DataFrame({
        "PB": ["1", "2"], "Vuelo": ["1", "3"], "Fecha": ["2026:10:27"] * 2,
        "Hora_de_inicio": ["09:00:00", hora_inicio], "Hora_final": ["09:30:00", "10:30:00"],
        est.COLUMNA_ORIGEN: ["a.csv", "2026_10_27_estadillo_NORTHWOLD.csv"],
        est.COLUMNA_ORIGEN_RUTA: ["/x/a.csv", "/x/2026_10_27_estadillo_NORTHWOLD.csv"],
    })


def _run(df):
    cfg = types.SimpleNamespace(seconds_range=0, mismatch_hours=0, mismatch_minutes=0)
    cb = types.SimpleNamespace(emit=lambda *a, **k: None)
    return _ventanas_por_vuelo(df, COLS, _Pipe(), cfg, cb)


def test_hora_con_dos_puntos_sobrante_dice_fichero_pb_vuelo_valor():
    with pytest.raises(ErrorEstadillo) as ei:
        _run(_df("09:15:00:"))
    msg = str(ei.value)
    for trozo in ("2026_10_27_estadillo_NORTHWOLD.csv", "PB 2", "vuelo 3", "09:15:00:",
                  "Hora_de_inicio", "Fila 1", "HH:MM:SS", "Corrígelo en el estadillo"):
        assert trozo in msg, trozo
    assert isinstance(ei.value, ValueError)


def test_hora_valida_no_falla():
    assert len(_run(_df("9:15:00"))) == 2


def test_fecha_invalida_dice_fichero():
    df = _df("09:15:00")
    df.loc[1, "Fecha"] = "27/10/2026"
    with pytest.raises(ErrorEstadillo) as ei:
        _run(df)
    assert "NORTHWOLD" in str(ei.value) and "27/10/2026" in str(ei.value)


def test_lectura_ilegible_dice_fichero(tmp_path):
    f = tmp_path / "roto.xlsx"
    f.write_bytes(b"no es un excel")
    with pytest.raises(ErrorEstadillo) as ei:
        est.combinar_estadillos([str(f)])
    assert "roto.xlsx" in str(ei.value)
