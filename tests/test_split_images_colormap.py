import numpy as np
from pipeline import apply_thermal_colormap

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
