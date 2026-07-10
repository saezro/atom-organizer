import numpy as np
from pipeline import apply_thermal_colormap
from matplotlib import cm


def _reference_colormap(array, temp_min, temp_max, name="inferno"):
    """Implementación de referencia (la vieja, get_cmap continuo) para comparar fidelidad."""
    clipped = np.clip(array, temp_min, temp_max)
    if temp_max > temp_min:
        normalized = (clipped - temp_min) / (temp_max - temp_min)
    else:
        normalized = np.zeros_like(clipped)
    rgba = cm.get_cmap(name)(normalized)
    return (rgba[..., :3] * 255).astype(np.uint8)


def test_lut_equivalente_a_referencia_dentro_de_1_255():
    temp_min, temp_max = 0.0, 60.0
    rng = np.linspace(-10.0, 70.0, 64 * 64).reshape(64, 64)  # incluye fuera de rango
    new = apply_thermal_colormap(rng, temp_min, temp_max)
    ref = _reference_colormap(rng, temp_min, temp_max)
    assert new.dtype == np.uint8 and new.shape == (64, 64, 3)
    diff = np.abs(new.astype(int) - ref.astype(int)).max()
    assert diff <= 1, f"LUT-1024 debe diferir ≤1/255 de la referencia continua, dio {diff}"


def test_lut_rango_completo_sin_indexerror():
    # valores muy por debajo y por encima no deben desbordar la LUT (idx 0..1023)
    arr = np.array([[-1000.0, 0.0], [30.0, 1e6]])
    out = apply_thermal_colormap(arr, 0.0, 60.0)
    assert out.shape == (2, 2, 3)


def test_misma_temperatura_mismo_color_en_rango():
    temp_min, temp_max = 10.0, 40.0
    arr1 = np.full((4, 4), 25.0)
    arr2 = np.full((4, 4), 25.0)
    rgb1 = apply_thermal_colormap(arr1, temp_min, temp_max)
    rgb2 = apply_thermal_colormap(arr2, temp_min, temp_max)
    assert rgb1.dtype == np.uint8
    assert rgb1.shape == (4, 4, 3)
    assert np.array_equal(rgb1, rgb2)

def test_temperaturas_fuera_de_rango_saturan():
    temp_min, temp_max = 10.0, 40.0
    arr = np.array([[temp_min, temp_max], [temp_min - 100, temp_max + 100]])
    rgb = apply_thermal_colormap(arr, temp_min, temp_max)
    # el pixel por debajo del mínimo debe dar el mismo color que el mínimo exacto
    assert np.array_equal(rgb[1, 0], rgb[0, 0])
    # el pixel por encima del máximo debe dar el mismo color que el máximo exacto
    assert np.array_equal(rgb[1, 1], rgb[0, 1])
