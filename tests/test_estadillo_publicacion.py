import pytest
from atom_core import estadillo_publicacion as pub

MANIFEST = {
    "version": 1,
    "planta": "Aerotools--CALAMOCHA--2026--T_Modulos",
    "subido_en": "2026-08-17T143022Z",
    "subido_por": "ofi@aerotools.es",
    "ficheros": [
        {"orden": 2, "objeto": "02__b1b2b3b4.xlsx", "nombre_original": "dia2.xlsx", "md5_b64": "sQ==", "bytes": 20},
        {"orden": 1, "objeto": "01__a1a2a3a4.csv", "nombre_original": "dia1.csv", "md5_b64": "oQ==", "bytes": 10},
    ],
    "validacion": {"vuelos_detectados": 5, "filas_con_problemas": 0},
}


def test_planta_desde_prefijo_saca_solo_el_campo_planta():
    assert pub.planta_desde_prefijo("Aerotools--CALAMOCHA--2026--T_Modulos") == "CALAMOCHA"


def test_planta_desde_prefijo_devuelve_none_si_no_son_cuatro_campos():
    assert pub.planta_desde_prefijo("CALAMOCHA") is None
    assert pub.planta_desde_prefijo("") is None
    assert pub.planta_desde_prefijo("a--b--c") is None


def test_planta_desde_prefijo_devuelve_none_si_la_planta_esta_vacia():
    # `_` es el placeholder de campo vacio de `prefijo_de_inspeccion`.
    assert pub.planta_desde_prefijo("Aerotools--_--2026--T_Modulos") is None


def test_plan_publicacion_usa_el_nombre_original_bajo_la_carpeta_de_subida():
    plan = pub.plan_publicacion("Aerotools--CALAMOCHA--2026--T_Modulos", MANIFEST)
    assert [p["objeto_destino"] for p in plan] == [
        "CALAMOCHA/ESTADILLOS/2026-08-17T143022Z/dia1.csv",
        "CALAMOCHA/ESTADILLOS/2026-08-17T143022Z/dia2.xlsx",
    ]


def test_plan_publicacion_no_mete_carpeta_PREPARACION():
    # Ruling de Cas 2026-08-17: el destino en el bucket de plantas NO lleva
    # nivel `PREPARACION`. Test explicito para que no vuelva por inercia.
    plan = pub.plan_publicacion("Aerotools--CALAMOCHA--2026--T_Modulos", MANIFEST)
    assert all("PREPARACION" not in p["objeto_destino"] for p in plan)


def test_plan_publicacion_ordena_por_orden_no_por_el_array():
    plan = pub.plan_publicacion("Aerotools--CALAMOCHA--2026--T_Modulos", MANIFEST)
    assert [p["orden"] for p in plan] == [1, 2]


def test_plan_publicacion_cae_al_objeto_si_no_hay_nombre_original():
    m = {**MANIFEST, "ficheros": [{"orden": 1, "objeto": "01__aa.csv", "md5_b64": "oQ==", "bytes": 1}]}
    plan = pub.plan_publicacion("Aerotools--CALAMOCHA--2026--T_Modulos", m)
    assert plan[0]["objeto_destino"].endswith("/01__aa.csv")


def test_plan_publicacion_rechaza_un_nombre_original_con_barras():
    m = {**MANIFEST, "ficheros": [{"orden": 1, "objeto": "01__aa.csv", "nombre_original": "../../x.csv", "bytes": 1}]}
    with pytest.raises(ValueError):
        pub.plan_publicacion("Aerotools--CALAMOCHA--2026--T_Modulos", m)


def test_plan_publicacion_sin_planta_no_produce_nada():
    assert pub.plan_publicacion("CALAMOCHA", MANIFEST) == []
