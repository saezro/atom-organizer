import pytest

from atom_core.equipo import equipo_coincide, texto_coincide


@pytest.mark.parametrize("estadillo, modelo, esperado", [
    ("DJI M300", "M4T", False),
    ("DJI M200", "M4T", False),
    ("Mavic 2EA", "M4T", False),
    ("AT-M2EA-01", "MAVIC2-ENTERPRISE-ADVANCED", True),
    ("Mavic 2EA", "MAVIC2-ENTERPRISE-ADVANCED", True),
    ("DJI Matrice 4T", "M4T", True),
    ("DJI M4T", "Matrice 4T", True),
    ("DJI M300", "XT2", True),
    ("DJI M300 RTK", "ZH20T", True),
    ("DJI M200", "ZH20T", False),
    ("DJI M30T", "M3T", False),
    ("DJI M300", "M30T", False),
    ("Dron1", "M4T", None),
    ("", "M4T", None),
    ("DJI M300", None, None),
    ("DJI M300", "FC6310", None),
    ("DJI M300", "M4T\x00", False),
])
def test_equipo_coincide(estadillo, modelo, esperado):
    assert equipo_coincide(estadillo, modelo) is esperado


def test_texto_coincide():
    assert (texto_coincide(True), texto_coincide(False), texto_coincide(None)) == ("Sí", "No", None)
