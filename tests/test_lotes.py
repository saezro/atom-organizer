"""Contrato del nombre de lote y del manifest v2 (atom_core/lotes.py).

`nombre_lote` tiene que dar EXACTAMENTE lo mismo que `nombreLote` de
Atom-suite/lib/organizer-lotes.js (mismo sello, mismo separador `__`, mismo
saneado del usuario). Si un test aquí cambia, el equivalente en
lib/organizer-lotes.test.mjs tiene que cambiar igual.
"""
from datetime import datetime, timezone

from atom_core.lotes import nombre_lote, manifest_lote


def test_nombre_lote_igual_que_la_suite():
    ahora = datetime(2026, 8, 20, 15, 42, 10, tzinfo=timezone.utc)
    assert nombre_lote(ahora, "rodrigo.saez") == "2026-08-20T154210Z__rodrigo_saez"


def test_nombre_lote_sin_usuario():
    ahora = datetime(2026, 8, 20, 15, 42, 10, tzinfo=timezone.utc)
    assert nombre_lote(ahora, "") == "2026-08-20T154210Z___"


def test_nombre_lote_con_acentos_y_espacios():
    ahora = datetime(2026, 8, 20, 15, 42, 10, tzinfo=timezone.utc)
    assert nombre_lote(ahora, "José Ñ") == "2026-08-20T154210Z__Jose_N"


def test_nombre_lote_con_puntos_y_caracteres_raros():
    ahora = datetime(2026, 8, 20, 15, 42, 10, tzinfo=timezone.utc)
    assert nombre_lote(ahora, "a.b(c)!") == "2026-08-20T154210Z__a_bc"


def test_nombre_lote_convierte_a_utc_si_viene_en_otra_zona():
    from datetime import timedelta
    ahora_local = datetime(2026, 8, 20, 17, 42, 10,
                           tzinfo=timezone(timedelta(hours=2)))
    assert nombre_lote(ahora_local, "rodrigo.saez") == "2026-08-20T154210Z__rodrigo_saez"


def test_manifest_lote_v2():
    m = manifest_lote("L", "rodrigo.saez", ["ESTADILLOS/e.csv"], 91)
    assert m["version"] == 2
    assert m["lote"] == "L"
    assert m["estadillos"] == ["ESTADILLOS/e.csv"]
    assert m["num_objetos"] == 91
    assert m["subido_por"] == "rodrigo.saez"
    assert m["subido_en"].endswith("Z")


def test_manifest_lote_sin_usuario_es_null():
    m = manifest_lote("L", "", ["ESTADILLOS/e.csv"], 1)
    assert m["subido_por"] is None
