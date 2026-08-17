from datetime import datetime, timedelta, timezone

import pytest

from atom_core import estadillo_canonico as ec


def test_carpeta_subida_usa_timestamp_utc_ordenable():
    ahora = datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc)

    assert ec.carpeta_subida(ahora) == "2026-08-17T034501Z"


def test_carpetas_de_subida_ordenan_alfabeticamente_por_tiempo():
    antes = ec.carpeta_subida(datetime(2026, 8, 14, 9, 12, 33, tzinfo=timezone.utc))
    despues = ec.carpeta_subida(datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc))

    assert sorted([despues, antes]) == [antes, despues]


def test_prefijo_planta_normaliza_el_nombre():
    assert ec.prefijo_planta("MARISOLES_LOS MANGOS") == (
        "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS"
    )


def test_nombre_objeto_lleva_orden_con_dos_digitos_y_md5_corto():
    assert ec.nombre_objeto(1, "9f3c2e11aabbccdd", ".xlsx") == "01__9f3c2e11.xlsx"
    assert ec.nombre_objeto(12, "9f3c2e11aabbccdd", ".csv") == "12__9f3c2e11.csv"


def test_nombre_objeto_normaliza_extension_sin_punto():
    assert ec.nombre_objeto(1, "9f3c2e11aabbccdd", "xlsx") == "01__9f3c2e11.xlsx"


def test_md5_hex_desde_b64_convierte_el_hash_de_gcs():
    # MD5 de b"hola" es 4d186321c1a7f0f354b297e8914ab240
    import base64
    import hashlib

    b64 = base64.b64encode(hashlib.md5(b"hola").digest()).decode()

    assert ec.md5_hex_desde_b64(b64) == "4d186321c1a7f0f354b297e8914ab240"


def test_carpeta_subida_rechaza_si_datetime_naive():
    with pytest.raises(ValueError):
        ec.carpeta_subida(datetime(2026, 8, 17, 9, 12, 33))


def test_carpeta_subida_normaliza_si_zona_no_utc():
    madrid = timezone(timedelta(hours=2))
    ahora = datetime(2026, 8, 17, 11, 12, 33, tzinfo=madrid)
    assert ec.carpeta_subida(ahora) == "2026-08-17T091233Z"
