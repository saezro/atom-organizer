"""Calendario de la revisión del índice (spec §5): cubetas por hora y recomendaciones."""
import pytest

from atom_core import calendario_revision as cr
from atom_core.sin_colocar import ErrorSinColocar
from tests.test_recomendaciones_sin_colocar import _indexar, _ventanas
from tests.test_sin_colocar import ESTADILLO, _t, tanda  # noqa: F401 (fixture)

FOTOS = [
    ("DJI_0001_D.JPG", _t(10, 10, 45)),      # fuera de ventana, vecino fila 0 (alta)
    ("DJI_0003_D.JPG", _t(10, 2, 0)),        # dentro de fila 0 (RGB)
    ("DJI_0004_D.JPG", _t(10, 3, 0)),        # dentro de fila 0 (RGB)
    ("DJI_0005_T.JPG", _t(10, 4, 0)),        # dentro de fila 0 (térmica)
    ("DJI_0006_D.JPG", _t(11, 5, 0)),        # dentro de fila 1
]


def test_calendario_cubetas_por_hora_y_forma(tanda):
    _indexar(tanda, FOTOS)
    cal = cr.calendario(tanda.destino)
    assert cal["ok"] is True and cal["zona_horaria"] == "exif-local"
    assert cal["destino"] == tanda.destino and cal["espera"]["activa"] is False
    v0, v1 = cal["vuelos"]
    assert (v0["fila"], v0["pb"], v0["vuelo"]) == (0, "1", "1")
    [h] = v0["horas"]
    assert h["hora"] == "2024-06-01T10" and (h["n_rgb"], h["n_t"]) == (2, 1)
    assert v0["n_total"] == 3
    assert v1["horas"][0]["hora"] == "2024-06-01T11" and v1["horas"][0]["n_rgb"] == 1
    [libre] = cal["libres"]
    assert libre["nombre"] == "DJI_0001_D.JPG" and libre["decision"] is None
    [rec] = cal["recomendaciones"]
    assert "claves" not in rec and rec["n_fotos"] == 1 and rec["confianza"] == "alta"
    assert libre["recomendacion"] == rec["id"]
    assert [g["estado"] for g in cal["grupos"]] == ["pendiente"]
    assert cal["horas_sin_fila"] == []


def test_calendario_sin_manifiesto_es_error_visible(tmp_path):
    with pytest.raises(ErrorSinColocar):
        cr.calendario(str(tmp_path / "nada"))


def test_dos_tandas_no_se_cuelan_en_las_horas(tanda):
    # Cada tanda con su carpeta de origen: el índice lee TODO el origen y, con el lector
    # Rust, los ficheros de la 1ª tanda (JPEG reales con fecha) entrarían en la 2ª.
    tanda.indexar([("DJI_0001_D.JPG", _t(10, 10, 45)), ("DJI_0003_D.JPG", _t(10, 2, 0))],
                  ventanas=_ventanas(ESTADILLO), origen=str(tanda.tmp / "origen1"))
    tanda.indexar([("DJI_0011_D.JPG", _t(10, 11, 30)), ("DJI_0012_D.JPG", _t(10, 3, 0)),
                   ("DJI_0013_D.JPG", _t(10, 4, 0))],
                  ventanas=_ventanas(ESTADILLO), origen=str(tanda.tmp / "origen2"))
    cal = cr.calendario(tanda.destino)
    v0 = cal["vuelos"][0]
    assert cal["ejecucion"] == 2
    assert v0["n_total"] == 2 and v0["horas"][0]["n_rgb"] == 2    # solo las de la ejecución 2
    cal1 = cr.calendario(tanda.destino, ejecucion_id=1)
    assert cal1["ejecucion"] == 1


def test_ficha_de_hora_ventana_semiabierta_y_fuera():
    f = {"fila": 0, "inicio": "2024-06-01T10:00:00", "fin": "2024-06-01T10:10:00"}
    g = {"fila": 1, "inicio": "2024-06-01T11:00:00", "fin": "2024-06-01T11:10:00"}
    assert cr._ficha_de_hora([f], "2024-06-01T10") is f
    assert cr._ficha_de_hora([f], "2024-06-01T12") is None      # fuera de la ventana
    assert cr._ficha_de_hora([f], "2024-06-01T09") is None      # termina justo al empezar la hora
    assert cr._ficha_de_hora([f, g], "2024-06-01T11") is g


def test_giro_desconocido_se_cuenta_aparte(tanda):
    _indexar(tanda, FOTOS)   # sin yaw: no hay voto de giro
    cal = cr.calendario(tanda.destino)
    v0 = cal["vuelos"][0]
    h = v0["horas"][0]
    assert h["n_giro_desconocido"] + h["n_90"] + h["n_270"] + h["n_0"] == h["n_rgb"] + h["n_t"]
    assert v0["n_giro_desconocido"] == h["n_giro_desconocido"]
