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

    # El tipo de la celda no se asserta: `pd.read_excel` infiere `int` para
    # celdas de solo dígitos, igual que hace `pd.read_csv` en la otra rama.
    # La normalización a texto es de `filas_para_suite` (`str(v).strip()`),
    # no de la lectura.
    assert [str(v) for v in df["PB"]] == ["1"]
