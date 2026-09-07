"""El .spec de Windows debe recoger ENTERAS las librerías con C-extensions.

Esto no es celo: el mismo fallo ha roto `organizar` en producción dos veces
seguidas, y las dos veces con un mensaje que apunta a otro sitio.

  - v3.4.74 · numpy → "DLL load failed while importing _multiarray_umath"
  - v3.4.75 · pandas → "partially initialized module 'pandas' has no attribute
    '_pandas_datetime_CAPI' (most likely due to a circular import)"

Ninguno de los dos mensajes menciona el empaquetado, y el de pandas además
sugiere un import circular que NO existe: lo que pasa es que el primer `import
pandas` revienta a medias por una extensión ausente y el siguiente encuentra el
módulo ya en `sys.modules`, incompleto.

El fallo solo se ve en el .exe empaquetado, nunca en desarrollo (donde las
librerías están completas en el venv), así que ningún test funcional lo caza.
Este comprueba lo único comprobable desde aquí: que el .spec las recoge.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent.parent / "atom_organizer_webview.spec"

# Librerías cuyo binario nativo se carga por C-API y que PyInstaller no recoge
# entero con su hook genérico. Añadir aquí cualquier otra que dé "DLL load
# failed" o "partially initialized module" en el .exe.
NATIVAS = ("numpy", "pandas")


@pytest.fixture(scope="module")
def spec_texto() -> str:
    assert SPEC.exists(), f"no está el spec de Windows: {SPEC}"
    return SPEC.read_text(encoding="utf-8")


@pytest.mark.parametrize("lib", NATIVAS)
def test_el_spec_hace_collect_all(spec_texto, lib):
    assert re.search(rf"collect_all\(['\"]{lib}['\"]\)", spec_texto), (
        f"falta collect_all('{lib}') en {SPEC.name}: el .exe arrancará pero "
        f"petará al primer import de {lib} al organizar"
    )


@pytest.mark.parametrize("lib", NATIVAS)
@pytest.mark.parametrize("destino", ["binaries", "datas", "hiddenimports"])
def test_el_resultado_de_collect_all_se_usa(spec_texto, lib, destino):
    """collect_all() devuelve una tupla (datas, binaries, hiddenimports) y las
    TRES hay que enchufarlas al Analysis. Declararla y no usar alguna es el
    error silencioso más fácil de cometer aquí."""
    sufijo = {"binaries": "binaries", "datas": "datas", "hiddenimports": "hidden"}[destino]
    assert f"{lib}_{sufijo}" in spec_texto.split("a = Analysis(", 1)[-1], (
        f"{lib}_{sufijo} no llega al Analysis: collect_all('{lib}') se declara "
        f"pero su parte de `{destino}` se queda sin usar"
    )
