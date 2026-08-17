from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec

AHORA = datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc)


def _plan():
    return ec.plan_subida(
        planta="X",
        ficheros_locales=[
            {
                "orden": 1,
                "ruta": "/tmp/e.xlsx",
                "nombre_original": "e.xlsx",
                "md5_b64": "TRhjIcGn8PNUspfokUqyQA==",
                "bytes": 10,
                "ext": ".xlsx",
            }
        ],
        vuelos=[{"pb": "1"}],
        validacion={"vuelos_detectados": 1, "filas_con_problemas": 0},
        ahora=AHORA,
    )


def test_sube_todo_el_plan_en_orden():
    escritos = []

    res = ec.ejecutar_plan(
        _plan(),
        subir_fichero=lambda remoto, ruta: escritos.append(remoto),
        subir_json=lambda remoto, obj: escritos.append(remoto),
    )

    assert res["ok"] is True
    assert res["subidos"] == 6
    assert escritos[-1].endswith("actual/manifest.json")


def test_devuelve_la_ruta_del_manifest_con_timestamp_no_la_de_actual():
    res = ec.ejecutar_plan(
        _plan(),
        subir_fichero=lambda remoto, ruta: None,
        subir_json=lambda remoto, obj: None,
    )

    assert res["ruta_manifest"] == (
        "X/ESTADILLOS/2026-08-17T034501Z/manifest.json"
    )


def test_aborta_sin_escribir_manifest_si_falla_un_crudo():
    escritos = []

    def subir_fichero(remoto, ruta):
        raise OSError("conexion caida")

    res = ec.ejecutar_plan(
        _plan(),
        subir_fichero=subir_fichero,
        subir_json=lambda remoto, obj: escritos.append(remoto),
    )

    assert res["ok"] is False
    assert res["error"]
    assert res["ruta_manifest"] is None
    assert escritos == []
