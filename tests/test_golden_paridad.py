"""Golden de paridad: la salida del camino ACTIVO debe coincidir con `tests/golden/*.json`.

Los golden los congeló `tests/golden/generar.py` con el camino Python (flag off). Hoy este
test corre con Python; tras borrar el camino Python correrá con Rust y debe dar lo mismo.
Con `atom_rs` instalado se repite además con las fases Rust activas (marcado `rust_ab`).

    python -m pytest tests/test_golden_paridad.py -q

Si falla `entrada_sha256` el que ha derivado es el GENERADOR del corpus (Pillow, piexif...),
no el código bajo prueba: regenerar con `python tests/golden/generar.py` y revisar el diff.
"""
from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path

import pytest
from PIL import features

from tests.golden import generar as g

_GOLDEN = Path(__file__).resolve().parent / "golden"


def _cargar(nombre: str) -> dict:
    return json.loads((_GOLDEN / f"{nombre}.json").read_text(encoding="utf-8"))


_IND, _RGB, _TER = _cargar("indice"), _cargar("rgb"), _cargar("termica")
_CASOS = g.corpus()


def _hay_rust() -> bool:
    try:
        m = importlib.import_module("atom_rs")
    except Exception:  # noqa: BLE001
        return False
    return hasattr(m, "parsear_cabecera") and hasattr(m, "rgb_procesar") \
        and hasattr(m, "termica_procesar")


#: "activo" = lo que diga el entorno (hoy Python); "rust" = fases Rust forzadas.
_MODOS = [
    pytest.param("activo", id="activo"),
    pytest.param("rust", id="rust", marks=[
        pytest.mark.rust_ab,
        pytest.mark.skipif(not _hay_rust(), reason="atom_rs no instalado")]),
]


@pytest.fixture(autouse=True)
def _entorno(request, monkeypatch):
    from atom_core import indice
    # El conftest lo apaga para imágenes vacías; el golden se tomó con el valor real (True).
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    for v in g._VAR_RUST:
        monkeypatch.delenv(v, raising=False)
    modo = request.node.callspec.params.get("modo") if hasattr(request.node, "callspec") else None
    if modo == "rust":
        monkeypatch.setenv("ATOM_RUST_FASES", "indice,rgb,termica")


def _mismo_libjpeg(meta: dict) -> bool:
    return meta["libjpeg_turbo"] == features.version("libjpeg_turbo")


@pytest.fixture
def logger_golden(tmp_path):
    return g._logger(tmp_path)


@pytest.mark.parametrize("modo", _MODOS)
@pytest.mark.parametrize("caso", sorted(_IND["casos"]))
def test_golden_indice(caso, modo, tmp_path, logger_golden):
    esperado = _IND["casos"][caso]
    ext, datos = _CASOS[caso]
    assert g.sha(datos) == esperado["entrada_sha256"], "deriva del generador del corpus"
    obtenido = g.resultado_indice(caso, ext, datos, tmp_path, logger_golden)
    assert obtenido == esperado


@pytest.mark.parametrize("modo", _MODOS)
@pytest.mark.parametrize("variante", [v[0] for v in g.VARIANTES_RGB])
@pytest.mark.parametrize("caso", sorted(_RGB["casos"]))
def test_golden_rgb(caso, variante, modo, tmp_path):
    if not _mismo_libjpeg(_RGB["_meta"]):
        pytest.skip("golden JPEG tomado con otra libjpeg-turbo: "
                    f"{_RGB['_meta']['libjpeg_turbo']} != {features.version('libjpeg_turbo')}")
    esperado = _RGB["casos"][caso]
    ext, datos = _CASOS[caso]
    assert g.sha(datos) == esperado["entrada_sha256"], "deriva del generador del corpus"
    v = next(x for x in g.VARIANTES_RGB if x[0] == variante)
    # Dispatcher real: flag off -> Python; flag on -> Rust (con su fallback a Pillow).
    obtenido = g.resultado_rgb(caso, ext, datos, tmp_path, v, python_puro=False)
    assert obtenido == esperado["variantes"][variante]


@pytest.mark.parametrize("modo", _MODOS)
@pytest.mark.parametrize("caso", [c[0] for c in g.CASOS_TERMICA])
def test_golden_termica(caso, modo, tmp_path, logger_golden):
    if not _mismo_libjpeg(_TER["_meta"]):
        pytest.skip("golden JPEG tomado con otra libjpeg-turbo")
    esperado = _TER["casos"][caso]
    c = next(x for x in g.CASOS_TERMICA if x[0] == caso)
    obtenido = g.resultado_termica(c, tmp_path, logger_golden)
    assert obtenido == esperado
