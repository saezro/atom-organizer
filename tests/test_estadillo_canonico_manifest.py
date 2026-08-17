from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec


def _ficheros():
    return [
        {
            "orden": 1,
            "objeto": "01__9f3c2e11.xlsx",
            "nombre_original": "Estadillo VUELOS 17 agosto (2).xlsx",
            "md5_b64": "nzwuEQ==",
            "bytes": 48213,
        }
    ]


def _validacion():
    return {"vuelos_detectados": 34, "filas_con_problemas": 0}


def test_manifest_lleva_version_y_planta():
    m = ec.construir_manifest(
        planta="MARISOLES_LOS MANGOS",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por="daniel@aerotools.es",
        ficheros=_ficheros(),
        validacion=_validacion(),
    )

    assert m["version"] == 1
    assert m["planta"] == "MARISOLES_LOS_MANGOS"
    assert m["subido_en"] == "2026-08-17T034501Z"
    assert m["subido_por"] == "daniel@aerotools.es"


def test_manifest_conserva_nombre_original_como_metadato():
    m = ec.construir_manifest(
        planta="X",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por=None,
        ficheros=_ficheros(),
        validacion=_validacion(),
    )

    assert m["ficheros"][0]["nombre_original"] == "Estadillo VUELOS 17 agosto (2).xlsx"
    assert m["ficheros"][0]["objeto"] == "01__9f3c2e11.xlsx"


def test_manifest_guarda_el_orden_de_prioridad():
    ficheros = [
        {"orden": 2, "objeto": "02__b.csv", "nombre_original": "b", "md5_b64": "x", "bytes": 1},
        {"orden": 1, "objeto": "01__a.xlsx", "nombre_original": "a", "md5_b64": "y", "bytes": 2},
    ]

    m = ec.construir_manifest(
        planta="X",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por=None,
        ficheros=ficheros,
        validacion=_validacion(),
    )

    assert [f["orden"] for f in m["ficheros"]] == [1, 2]


def test_manifest_incluye_resumen_de_validacion():
    m = ec.construir_manifest(
        planta="X",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por=None,
        ficheros=_ficheros(),
        validacion={"vuelos_detectados": 34, "filas_con_problemas": 2},
    )

    assert m["validacion"] == {"vuelos_detectados": 34, "filas_con_problemas": 2}


def test_normalizado_usa_el_shape_que_acepta_la_suite():
    vuelos = [
        {
            "fecha": "2026-08-17",
            "piloto": "Daniel",
            "equipo_vuelo": "E1",
            "pb": "1",
            "num_vuelo": "1",
            "hora_inicio": "09:12:33",
            "hora_fin": "09:41:02",
            "origen": None,
        }
    ]

    n = ec.construir_normalizado(vuelos)

    assert n["version"] == 1
    assert n["vuelos"] == vuelos
