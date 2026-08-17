from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec

AHORA = datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc)


def _locales():
    return [
        {
            "orden": 1,
            "ruta": "/home/op/Estadillo raro (2).xlsx",
            "nombre_original": "Estadillo raro (2).xlsx",
            "md5_b64": "TRhjIcGn8PNUspfokUqyQA==",
            "bytes": 10,
            "ext": ".xlsx",
        }
    ]


def _plan():
    return ec.plan_subida(
        planta="MARISOLES_LOS MANGOS",
        ficheros_locales=_locales(),
        vuelos=[{"pb": "1"}],
        validacion={"vuelos_detectados": 1, "filas_con_problemas": 0},
        ahora=AHORA,
    )


def test_el_crudo_va_a_la_carpeta_con_timestamp_con_nombre_determinista():
    plan = _plan()
    base = "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS/2026-08-17T034501Z"

    assert plan[0]["remoto"] == f"{base}/01__4d186321.xlsx"
    assert plan[0]["ruta_local"] == "/home/op/Estadillo raro (2).xlsx"


def test_el_manifest_se_escribe_despues_del_normalizado_y_del_crudo():
    remotos = [p["remoto"] for p in _plan()]
    base = "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS/2026-08-17T034501Z"

    assert remotos.index(f"{base}/01__4d186321.xlsx") < remotos.index(
        f"{base}/estadillo.json"
    )
    assert remotos.index(f"{base}/estadillo.json") < remotos.index(
        f"{base}/manifest.json"
    )


def test_actual_se_escribe_entera_despues_de_la_carpeta_con_timestamp():
    remotos = [p["remoto"] for p in _plan()]
    base = "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS"

    assert remotos.index(f"{base}/2026-08-17T034501Z/manifest.json") < remotos.index(
        f"{base}/actual/01__4d186321.xlsx"
    )
    assert remotos[-1] == f"{base}/actual/manifest.json"


def test_cada_entrada_es_o_fichero_local_o_contenido_json():
    for entrada in _plan():
        assert ("ruta_local" in entrada) != ("contenido" in entrada)


def test_el_normalizado_contiene_los_vuelos():
    plan = _plan()
    normalizado = next(p for p in plan if p["remoto"].endswith("2026-08-17T034501Z/estadillo.json"))

    assert normalizado["contenido"]["vuelos"] == [{"pb": "1"}]
