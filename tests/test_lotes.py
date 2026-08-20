"""Contrato del nombre de lote y del manifest v2 (atom_core/lotes.py).

`nombre_lote` tiene que dar EXACTAMENTE lo mismo que `nombreLote` de
Atom-suite/lib/organizer-lotes.js (mismo sello, mismo separador `__`, mismo
saneado del usuario). Si un test aquí cambia, el equivalente en
lib/organizer-lotes.test.mjs tiene que cambiar igual.
"""
from datetime import datetime, timezone

import pytest

from atom_core.lotes import (
    estado_lote_carpeta,
    manifest_lote,
    marcar_lote_completo,
    nombre_lote,
    registrar_lote,
)


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


# ---------------------------------------------------------------------------
# Estado local por carpeta: `registrar_lote`/`marcar_lote_completo`/
# `estado_lote_carpeta`. Aísla `user_data_dir` a `tmp_path` para no tocar el
# `~/.config/atom-organizer` real de quien corre los tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _user_data_dir_aislado(monkeypatch, tmp_path):
    from atom_core import google_auth

    destino = tmp_path / "config-atom-organizer"
    monkeypatch.setattr(google_auth, "user_data_dir", lambda: destino)


def test_carpeta_sin_estado_previo_no_tiene_lote(tmp_path):
    carpeta = tmp_path / "vuelo"
    carpeta.mkdir()
    assert estado_lote_carpeta(carpeta) is None


def test_registrar_lote_queda_incompleto(tmp_path):
    carpeta = tmp_path / "vuelo"
    carpeta.mkdir()
    registrar_lote(carpeta, "L1")
    assert estado_lote_carpeta(carpeta) == {"lote": "L1", "completo": False}


def test_marcar_lote_completo_lo_pasa_a_completo(tmp_path):
    carpeta = tmp_path / "vuelo"
    carpeta.mkdir()
    registrar_lote(carpeta, "L1")
    marcar_lote_completo(carpeta, "L1")
    assert estado_lote_carpeta(carpeta) == {"lote": "L1", "completo": True}


def test_marcar_lote_completo_no_toca_si_el_lote_no_coincide(tmp_path):
    """Se registró un lote distinto entre medias: `marcar_lote_completo` de un
    lote antiguo no debe pisar el nuevo (carrera improbable, pero el efecto de
    marcar el equivocado sería peor que no marcar nada)."""
    carpeta = tmp_path / "vuelo"
    carpeta.mkdir()
    registrar_lote(carpeta, "L1")
    registrar_lote(carpeta, "L2")  # otro lote nuevo pisa al primero
    marcar_lote_completo(carpeta, "L1")  # el viejo, ya no vigente
    assert estado_lote_carpeta(carpeta) == {"lote": "L2", "completo": False}


def test_dos_carpetas_distintas_tienen_lotes_independientes(tmp_path):
    a = tmp_path / "vuelo_a"
    b = tmp_path / "vuelo_b"
    a.mkdir()
    b.mkdir()
    registrar_lote(a, "LA")
    registrar_lote(b, "LB")
    assert estado_lote_carpeta(a) == {"lote": "LA", "completo": False}
    assert estado_lote_carpeta(b) == {"lote": "LB", "completo": False}
