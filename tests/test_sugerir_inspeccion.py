from atom_core.inspecciones import Inspeccion, sugerir_inspeccion


def _ins(planta, anio="2026", fase="", empresa="EMP", tipo="T_Modulos"):
    return Inspeccion(empresa=empresa, planta=planta, anio=anio, tipo=tipo, fase=fase).to_dict()


CAT = [
    _ins("WILLKA", fase="Fase 1"),
    _ins("WILLKA", anio="2025"),
    _ins("OCAÑA"),
    _ins("PLANTA_B", fase="A"),
    _ins("PLANTA_B", fase="B"),
]


def test_unica_carpeta_y_estadillo():
    info = {"empresa": "X", "trabajo": "Willka", "fecha": "04/10/2026"}
    r = sugerir_inspeccion(r"D:\CHILE_2026\2026_10_04_Willka", info, CAT)
    assert r["estado"] == "unica"
    assert r["prefijo"] == CAT[0]["prefijo"]


def test_unica_solo_carpeta_sin_estadillo():
    r = sugerir_inspeccion("/x/2026_10_04_Willka", None, CAT)
    assert r["estado"] == "unica" and r["prefijo"] == CAT[0]["prefijo"]


def test_tildes_y_mayusculas():
    assert sugerir_inspeccion("/x/2026-10-04 ocana", None, CAT)["estado"] == "unica"
    assert sugerir_inspeccion("/x/2026_10_04_WILLKA/", None, CAT)["estado"] == "unica"


def test_varias_solo_difieren_en_fase():
    r = sugerir_inspeccion("/x/2026_10_04_Planta_B", None, CAT)
    assert r["estado"] == "varias" and r["candidatos"] == [CAT[3]["prefijo"]]
    assert "prefijo" not in r or not r["prefijo"]


def test_ninguna_planta_no_en_catalogo():
    assert sugerir_inspeccion("/x/2026_10_04_Otra", None, CAT)["estado"] == "ninguna"


def test_ninguna_anio_sin_inspeccion():
    assert sugerir_inspeccion("/x/2024_10_04_Willka", None, CAT)["estado"] == "ninguna"


def test_conflicto_plantas_distintas():
    info = {"trabajo": "Ocana", "fecha": "2026-10-04"}
    r = sugerir_inspeccion("/x/2026_10_04_Willka", info, CAT)
    assert r["estado"] == "conflicto"


def test_conflicto_anio():
    info = {"trabajo": "Willka", "fecha": "04/10/2025"}
    assert sugerir_inspeccion("/x/2026_10_04_Willka", info, CAT)["estado"] == "conflicto"


def test_carpeta_sin_fecha_usa_anio_del_estadillo():
    info = {"trabajo": "Willka", "fecha": "04/10/2026"}
    r = sugerir_inspeccion("/x/Willka", info, CAT)
    assert r["estado"] == "unica"


def test_carpeta_sin_fecha_ni_estadillo():
    assert sugerir_inspeccion("/x/Willka", None, CAT)["estado"] == "ninguna"
    assert sugerir_inspeccion("/x/Willka", {"error": "no"}, CAT)["estado"] == "ninguna"
