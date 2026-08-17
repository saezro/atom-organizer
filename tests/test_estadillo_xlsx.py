import pandas as pd

from atom_core import estadillo


def test_lee_xlsx_si_openpyxl_disponible(tmp_path):
    ruta = tmp_path / "estadillo.xlsx"
    pd.DataFrame(
        {
            "PB": ["1"],
            "Vuelo": ["1"],
            "Fecha": ["2026-08-17"],
            "Hora_de_inicio": ["09:12:33"],
            "Hora_final": ["09:41:02"],
        }
    ).to_excel(ruta, index=False)

    df = estadillo._read_dataframe(str(ruta))

    assert list(df["PB"]) == ["1"]
