"""Contrato público de `lectura_segura` (delegado en `atom_rs`, fase `io`).

Las esperas, lecturas cortas y EOF estable se prueban con inyección en
`tests/test_rust_lectura_paridad.py`; los helpers JPEG en `test_validacion_jpeg_origen.py`.
Aquí solo el camino real sin inyección; se salta si `atom_rs` no está instalado.
"""
import pytest

from atom_core import lectura_segura as ls
from atom_core import rust_bridge


@pytest.fixture(autouse=True)
def _requiere_rust():
    try:
        rust_bridge.exigir("io")
    except Exception as e:  # noqa: BLE001
        pytest.skip("atom_rs (fase io) no disponible: {0}".format(type(e).__name__))


def test_camino_normal(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    assert ls.leer_completo(f) == b"x" * 1000


def test_copiar_completo(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"abc")
    ls.copiar_completo(f, tmp_path / "b.jpg")
    assert (tmp_path / "b.jpg").read_bytes() == b"abc"


def test_bytes_origen_distinto_de_stat_falla_al_momento(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    for indexado in (200, 50):  # mayor o menor que el stat
        with pytest.raises(ls.LecturaIncompleta) as e:
            ls.leer_completo(f, intentos=5, bytes_origen=indexado)
        assert "el fichero cambió desde el índice: {0} vs 100 bytes".format(indexado) in str(e.value)


def test_bytes_origen_igual_a_stat_ok(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 100)
    assert ls.leer_completo(f, bytes_origen=100) == b"x" * 100
