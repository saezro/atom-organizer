"""Fusión de estadillos con cabeceras REALES divergentes (medido sobre los
116 estadillos reales de gs://plantas_pv_nl):

- Variante A (103/116 ficheros): 33 columnas, termina en
  `...;Tipologia;Vuelo_abortado`.
- Variante B (13/116): las mismas 33 columnas + `Si_corresponde;No_corresponde`
  al final (35 columnas en total).
- Al menos 2 plantas (LAS_CANES_DOU_RENARD, GRIJOTA_IV) mezclan ambas
  variantes dentro de la misma planta: fusionarlas NO puede lanzar
  `EstadilloHeaderError`, tiene que alinear por nombre de columna y dejar
  NaN donde falte.

`combinar_estadillos` solo debe reventar si falta una columna ESENCIAL para
el pipeline (PB, Vuelo, Fecha, Hora_de_inicio, Hora_final).
"""
from atom_core import estadillo

# Cabecera real, orden ES de `Utils.get_nombres_columnas` (utils.py:572-577).
_COLUMNAS_BASE = [
    "Empresa", "Trabajo", "Fecha", "Piloto", "Equipo_de_vuelo", "Pitch",
    "Hora_de_inicio", "Hora_final", "PB", "Vuelo", "Desplazado", "Vel_vuelo",
    "Alt_vuelo", "Vel_de_aire", "Temp_aire", "Nubes", "Radiacion",
    "Tiempo_vuelo", "Dist_Recorrida", "Set_Bat_1", "Set_Bat_2", "Set_Bat_3",
    "Volt_inicial", "Volt_final", "GB1/", "GB2/", "Anotaciones", "Termica",
    "RGB", "Cali_Ini", "Cali_Final", "Tipologia", "Vuelo_abortado",
]
assert len(_COLUMNAS_BASE) == 33

_COLUMNAS_EXTRA = ["Si_corresponde", "No_corresponde"]


def _fila_base(pb, vuelo, fecha, inicio, final):
    """Una fila completa (33 valores) para `_COLUMNAS_BASE`, con valores
    reconocibles en las columnas clave y placeholder en el resto."""
    valores = {
        "Empresa": "AEROTOOLS", "Trabajo": "T1", "Fecha": fecha,
        "Piloto": "Piloto1", "Equipo_de_vuelo": "Dron1", "Pitch": "0",
        "Hora_de_inicio": inicio, "Hora_final": final, "PB": pb, "Vuelo": vuelo,
        "Desplazado": "0", "Vel_vuelo": "5", "Alt_vuelo": "50",
        "Vel_de_aire": "1", "Temp_aire": "20", "Nubes": "0", "Radiacion": "800",
        "Tiempo_vuelo": "10", "Dist_Recorrida": "100", "Set_Bat_1": "1",
        "Set_Bat_2": "", "Set_Bat_3": "", "Volt_inicial": "16",
        "Volt_final": "15", "GB1/": "", "GB2/": "", "Anotaciones": "",
        "Termica": "SI", "RGB": "SI", "Cali_Ini": "SI", "Cali_Final": "SI",
        "Tipologia": "Fija", "Vuelo_abortado": "NO",
    }
    return [valores[c] for c in _COLUMNAS_BASE]


def _csv_variante_a(path, filas):
    """33 columnas: PB/Vuelo/Fecha/Hora_de_inicio/Hora_final/Tipologia/
    Vuelo_abortado... la variante mayoritaria (103/116 ficheros reales)."""
    lineas = [";".join(_COLUMNAS_BASE)]
    for pb, vuelo, fecha, inicio, final in filas:
        lineas.append(";".join(_fila_base(pb, vuelo, fecha, inicio, final)))
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


def _csv_variante_b(path, filas, si_corresponde="SI", no_corresponde=""):
    """33 columnas + Si_corresponde;No_corresponde (13/116 ficheros reales)."""
    columnas = _COLUMNAS_BASE + _COLUMNAS_EXTRA
    lineas = [";".join(columnas)]
    for pb, vuelo, fecha, inicio, final in filas:
        fila = _fila_base(pb, vuelo, fecha, inicio, final) + [si_corresponde, no_corresponde]
        lineas.append(";".join(fila))
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


# --- (a) variante A + variante B: fusión sin error, NaN donde no aplica -----

def test_fusion_variante_a_mas_variante_b_no_revienta_y_alinea_por_nombre(tmp_path):
    ruta_a = _csv_variante_a(tmp_path / "a.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
        ("1", "2", "2026:03:17", "10:10:00", "10:15:00"),
    ])
    ruta_b = _csv_variante_b(tmp_path / "b.csv", [
        ("2", "1", "2026:03:18", "11:00:00", "11:05:00"),
    ], si_corresponde="SI", no_corresponde="NO")

    combinado = estadillo.combinar_estadillos([ruta_a, ruta_b])

    # No se pierde ninguna fila.
    assert len(combinado) == 3
    # No se pierde ninguna columna: las 33 base + las 2 extra de B.
    assert set(_COLUMNAS_BASE) <= set(combinado.columns)
    assert set(_COLUMNAS_EXTRA) <= set(combinado.columns)

    # Las filas que vienen de la variante A (sin Si_corresponde/No_corresponde
    # en su fichero) quedan con NaN/vacío en esas columnas.
    filas_a = combinado[combinado[estadillo.COLUMNA_ORIGEN] == "a.csv"]
    assert len(filas_a) == 2
    assert filas_a["Si_corresponde"].isna().all()
    assert filas_a["No_corresponde"].isna().all()

    # Las filas de B sí conservan su valor en esas columnas.
    filas_b = combinado[combinado[estadillo.COLUMNA_ORIGEN] == "b.csv"]
    assert len(filas_b) == 1
    assert filas_b["Si_corresponde"].tolist() == ["SI"]
    assert filas_b["No_corresponde"].tolist() == ["NO"]

    # Las columnas base (esenciales u opcionales) no se tocan por la fusión:
    # cada fila conserva su Tipologia/Vuelo_abortado tal cual venían.
    assert combinado["Tipologia"].tolist() == ["Fija", "Fija", "Fija"]
    assert combinado["PB"].astype(str).tolist() == ["1", "1", "2"]


def test_fusion_variante_b_mas_variante_a_alinea_igual_en_cualquier_orden(tmp_path):
    """Mismo caso pero B primero: el resultado no depende de qué variante
    venga en cabeza (las 2 plantas reales que mezclan variantes no
    garantizan un orden fijo)."""
    ruta_b = _csv_variante_b(tmp_path / "b.csv", [
        ("2", "1", "2026:03:18", "11:00:00", "11:05:00"),
    ], si_corresponde="SI", no_corresponde="NO")
    ruta_a = _csv_variante_a(tmp_path / "a.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
    ])

    combinado = estadillo.combinar_estadillos([ruta_b, ruta_a])

    assert len(combinado) == 2
    assert set(_COLUMNAS_EXTRA) <= set(combinado.columns)
    filas_a = combinado[combinado[estadillo.COLUMNA_ORIGEN] == "a.csv"]
    assert filas_a["Si_corresponde"].isna().all()


# --- (b) falta columna esencial: EstadilloHeaderError con fichero+columna --

def test_fusion_falta_columna_esencial_pb_da_error_con_fichero_y_columna(tmp_path):
    columnas_sin_pb = [c for c in _COLUMNAS_BASE if c != "PB"]
    ruta_ok = _csv_variante_a(tmp_path / "ok.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
    ])
    ruta_rota = tmp_path / "roto.csv"
    valores = _fila_base("1", "1", "2026:03:18", "11:00:00", "11:05:00")
    fila_sin_pb = [v for c, v in zip(_COLUMNAS_BASE, valores) if c != "PB"]
    ruta_rota.write_text(
        ";".join(columnas_sin_pb) + "\n" + ";".join(fila_sin_pb) + "\n", encoding="utf-8")

    import pytest
    with pytest.raises(estadillo.EstadilloHeaderError) as excinfo:
        estadillo.combinar_estadillos([ruta_ok, str(ruta_rota)])

    mensaje = str(excinfo.value)
    assert "roto.csv" in mensaje
    assert "PB" in mensaje


def test_fusion_falta_columna_esencial_pero_tipologia_no_es_esencial(tmp_path):
    """Al revés: falta una columna NO esencial (Tipologia) en un fichero.
    Eso NO debe lanzar `EstadilloHeaderError` -es justo el caso real de
    variante A vs B-, la fusión tiene que completarse."""
    columnas_sin_tipologia = [c for c in _COLUMNAS_BASE if c != "Tipologia"]
    ruta_completa = _csv_variante_a(tmp_path / "completo.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
    ])
    ruta_sin_tipologia = tmp_path / "sin_tipologia.csv"
    valores = _fila_base("2", "1", "2026:03:18", "11:00:00", "11:05:00")
    fila = [v for c, v in zip(_COLUMNAS_BASE, valores) if c != "Tipologia"]
    ruta_sin_tipologia.write_text(
        ";".join(columnas_sin_tipologia) + "\n" + ";".join(fila) + "\n", encoding="utf-8")

    combinado = estadillo.combinar_estadillos([ruta_completa, str(ruta_sin_tipologia)])

    assert len(combinado) == 2
    filas_sin_tipologia = combinado[combinado[estadillo.COLUMNA_ORIGEN] == "sin_tipologia.csv"]
    assert filas_sin_tipologia["Tipologia"].isna().all()


# --- (c) el orden de las filas se preserva tras la fusión -------------------

def test_fusion_preserva_orden_de_filas_entre_ficheros_y_dentro_de_cada_uno(tmp_path):
    ruta_a = _csv_variante_a(tmp_path / "a.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
        ("1", "2", "2026:03:17", "10:10:00", "10:15:00"),
    ])
    ruta_b = _csv_variante_b(tmp_path / "b.csv", [
        ("2", "1", "2026:03:18", "11:00:00", "11:05:00"),
        ("2", "2", "2026:03:18", "11:10:00", "11:15:00"),
    ])

    combinado = estadillo.combinar_estadillos([ruta_a, ruta_b])

    # Orden: primero TODAS las filas de a.csv (en su orden interno), luego
    # TODAS las de b.csv (en su orden interno) -es el desempate "gana el
    # primero" que usa gen_folder_struct para PB+Vuelo colisionado-.
    assert combinado["Vuelo"].astype(str).tolist() == ["1", "2", "1", "2"]
    assert combinado[estadillo.COLUMNA_ORIGEN].tolist() == [
        "a.csv", "a.csv", "b.csv", "b.csv"]
