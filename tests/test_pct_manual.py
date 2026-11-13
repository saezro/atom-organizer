"""% de recorte manual (v3.4.169): `pct_manual`, `indice._resolver_recorte` y
`RGBCropping._pct_manual_imagen`."""
from types import SimpleNamespace

import pytest

import pipeline as pipeline_real
from atom_core import camara_rgb
from atom_core.camara_rgb import ErrorPorcentajeManual, pct_manual
from atom_core.indice import _resolver_recorte
from tests.test_indice_organizado import _PipelineDePrueba


@pytest.mark.parametrize("valor", [0, "0", "", "  ", "-", None])
def test_pct_manual_sin_recorte(valor):
    assert pct_manual(valor) is None


def test_pct_manual_valido():
    assert pct_manual(48.5) == 48.5
    assert pct_manual("48,5") == 48.5
    assert pct_manual(100) == 100


@pytest.mark.parametrize("valor", [-1, 101, "abc", "nan"])
def test_pct_manual_invalido(valor):
    with pytest.raises(ErrorPorcentajeManual):
        pct_manual(valor)


def _res(nombre, modelo, tabla, manual, camara="Z"):
    dato = SimpleNamespace(nombre=nombre, modelo=modelo)
    cfg = SimpleNamespace(cropping_rgb=True, cropping_mode_auto=False,
                          crop_percentage=manual, camara_rgb=camara)
    pipe = _PipelineDePrueba({}, pct_por_modelo=tabla)
    return _resolver_recorte(dato, "RGB", cfg, pipe, hay_zoom=True)


TABLA = {"H30T": 50.0, "H30T_Z": None}


def test_manual_z_se_recorta_aunque_tabla_tenga_centinela():
    assert _res("DJI_0001_Z.JPG", "H30T", TABLA, 30) == (True, 0.30)


def test_manual_z_cero_es_copia_integra():
    assert _res("DJI_0001_Z.JPG", "H30T", TABLA, 0) == (True, None)


def test_automatico_z_centinela_tabla_manda():
    dato = SimpleNamespace(nombre="DJI_0001_Z.JPG", modelo="H30T")
    cfg = SimpleNamespace(cropping_rgb=True, cropping_mode_auto=True,
                          crop_percentage=30, camara_rgb="Z")
    assert _resolver_recorte(dato, "RGB", cfg, _PipelineDePrueba({}, pct_por_modelo=TABLA), hay_zoom=True) == (True, None)


def test_manual_w_usa_manual():
    assert _res("DJI_0001_W.JPG", "H30T", TABLA, 30, "W") == (True, 0.30)


def test_manual_cero_es_copia_integra():
    assert _res("DJI_0001_W.JPG", "H30T", TABLA, 0, "W") == (True, None)
    assert _res("DJI_0001_W.JPG", "H30T", TABLA, "-", "W") == (True, None)


def _crop(manual, image="DJI_0001_Z.JPG"):
    obj = pipeline_real.RGBCropping.__new__(pipeline_real.RGBCropping)
    return obj._pct_manual_imagen("/x", image, TABLA, manual)


def test_pct_manual_imagen():
    assert _crop(30) == 30
    assert _crop(30, "DJI_0001_W.JPG") == 30
    assert _crop(0) is None
