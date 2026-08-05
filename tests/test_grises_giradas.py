"""La vista en gris gira SIEMPRE igual que el TIFF, y viene activada de serie.

El `.tiff` es float32 de temperaturas y Windows lo abre BLANCO: la única forma de
comprobar a ojo que la térmica quedó con el norte arriba es la JPG en gris de
`Escala_de_grises/`. Si los dos arrays divergieran en el giro, esa comprobación
mentiría — que es peor que no tenerla. De ahí el invariante.
"""

import os

import numpy as np
import pytest

from pipeline import SplitImages


@pytest.fixture
def split(organizer_logger_stub):
    return SplitImages(organizer_logger_stub)


def _arrays():
    """Dos arrays distintos y asimétricos: un giro mal aplicado no puede pasar desapercibido."""
    arr = np.arange(12, dtype=np.float32).reshape(3, 4)
    array_to_normalize = arr * 10.0
    return arr, array_to_normalize


@pytest.mark.parametrize("degree", [90, 270, 0])
def test_los_dos_arrays_reciben_el_mismo_giro(split, degree):
    arr, norm = _arrays()

    arr_rot, norm_rot = split.rotar_arrays_termicos(arr, norm, degree)

    assert arr_rot.shape == norm_rot.shape
    # `norm` es `arr * 10`: si ambos giran igual, la relación se conserva celda a celda.
    np.testing.assert_allclose(norm_rot, arr_rot * 10.0)


def test_90_es_horario_y_270_antihorario(split):
    arr, norm = _arrays()

    np.testing.assert_array_equal(split.rotar_arrays_termicos(arr, norm, 90)[0], np.rot90(arr, 1, (1, 0)))
    np.testing.assert_array_equal(split.rotar_arrays_termicos(arr, norm, 270)[0], np.rot90(arr, 1, (0, 1)))


def test_sin_giro_devuelve_los_arrays_intactos(split):
    arr, norm = _arrays()

    arr_rot, norm_rot = split.rotar_arrays_termicos(arr, norm, 0)

    np.testing.assert_array_equal(arr_rot, arr)
    np.testing.assert_array_equal(norm_rot, norm)


class _FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def test_los_flags_manuales_mandan_sobre_el_automatico(split, monkeypatch):
    monkeypatch.setattr(split, "read_auto_rotate_degree", lambda *a, **k: 270)

    assert split.degree_de_giro(True, False, True, "/x", _FakeSignal()) == 90
    assert split.degree_de_giro(False, True, True, "/x", _FakeSignal()) == 270


def test_auto_lee_el_criterio_y_un_valor_raro_no_gira(split, monkeypatch):
    monkeypatch.setattr(split, "read_auto_rotate_degree", lambda *a, **k: 90)
    assert split.degree_de_giro(False, False, True, "/x", _FakeSignal()) == 90

    # Sin criterio (CSV ausente) o valor fuera de {90, 270}: no se gira, no se inventa.
    monkeypatch.setattr(split, "read_auto_rotate_degree", lambda *a, **k: 0)
    assert split.degree_de_giro(False, False, True, "/x", _FakeSignal()) == 0
    assert split.degree_de_giro(False, False, False, "/x", _FakeSignal()) == 0


def test_la_escala_de_grises_viene_activada_por_defecto():
    """Sin esto el .tiff se abre blanco y no hay forma de ver la orientación.

    Se lee como TEXTO a propósito: `atom_core/organize.py` arrastra Qt y no es importable
    en CI, y el default del front vive en un `.js`.
    """
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    organize = open(os.path.join(raiz, "atom_core", "organize.py"), encoding="utf-8").read()
    assert "convert_to_tif_create_gray_scale_images=True" in organize
    assert "convert_to_tif_create_gray_scale_images=False" not in organize

    schema = open(os.path.join(raiz, "webui", "src", "schema.js"), encoding="utf-8").read()
    for campo in ("create_gray_scale_images", "convert_to_tif_create_gray_scale_images"):
        linea = next(l for l in schema.splitlines() if f"name: '{campo}'" in l)
        assert "default: true" in linea, linea
