import pytest

from pipeline import RGBCropping


def test_get_percentage_by_model_normaliza_mayusculas_y_espacios(organizer_logger_stub):
    """`percentage_by_models` se guarda con claves en MAYUSCULAS (external_tools.py,
    gui.py, app_webview.py), pero el modelo EXIF crudo puede venir en minúsculas
    o con espacios extra. m3t / M3T / 'M3T ' deben resolver al mismo porcentaje."""
    rc = RGBCropping(organizer_logger_stub)
    percentage_cropping_dict = {"M3T": 30}

    assert rc.get_percentage_by_model("m3t", percentage_cropping_dict) == 30
    assert rc.get_percentage_by_model("M3T ", percentage_cropping_dict) == 30
    assert rc.get_percentage_by_model("M3T", percentage_cropping_dict) == 30


def test_get_percentage_by_model_sin_coincidencia_lanza_keyerror(organizer_logger_stub):
    """El fallback cuando no hay coincidencia se mantiene igual: KeyError."""
    rc = RGBCropping(organizer_logger_stub)
    percentage_cropping_dict = {"M3T": 30}

    with pytest.raises(KeyError):
        rc.get_percentage_by_model("M4T", percentage_cropping_dict)
