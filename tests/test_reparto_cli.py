"""Puerta de entrada del reparto: qué combinaciones de `--etapa` y shard acepta
`run_task`, y de dónde salen los números cuando no se pasan.

Estos tests cubren la clase de fallo que NO se ve: una combinación mal aceptada
no revienta, corre bonita y deja el vuelo corrupto en el bucket con exit code 0.
"""
import pytest

from atom_core import organize


def _emisor():
    eventos = []

    def emit(kind, payload=None):
        eventos.append((kind, payload))
    return eventos, emit


def _errores(eventos):
    return [p for k, p in eventos if k == "error"]


def test_todo_repartido_se_rechaza():
    """`--etapa todo` con varias tareas encadena las tres etapas sin barrera: una
    tarea podría estar estructurando mientras otra todavía separa, y el
    post-proceso trabajaría sobre un destino a medio hacer. Cada tarea vería su
    parte cuadrada, así que ningún check lo detectaría."""
    eventos, emit = _emisor()
    organize.run_task("split_images",
                      {"origen": "/no/existe", "destino": "/tmp/x",
                       "etapa": "todo", "shard_index": 0, "shard_count": 8},
                      emit)
    errores = _errores(eventos)
    assert errores, "se aceptó una combinación que corrompe el destino"
    assert "no se puede repartir" in errores[0]


def test_etapa_desconocida_se_rechaza():
    eventos, emit = _emisor()
    organize.run_task("split_images",
                      {"origen": "/no/existe", "destino": "/tmp/x",
                       "etapa": "postt"},
                      emit)
    errores = _errores(eventos)
    assert errores and "Etapa desconocida" in errores[0]


@pytest.mark.parametrize("etapa", ["split", "struct", "post"])
def test_las_etapas_repartibles_admiten_shard(etapa, tmp_path):
    """No debe salir el error de reparto: solo `todo` está vetado.

    `struct` entró en esta lista en v3.4.31, cuando pasó a repartirse por imagen
    (hash del nombre) en vez de por vuelo del estadillo."""
    eventos, emit = _emisor()
    origen = tmp_path / "origen"
    origen.mkdir()
    organize.run_task("split_images",
                      {"origen": str(origen), "destino": str(tmp_path / "destino"),
                       "etapa": etapa, "shard_index": 1, "shard_count": 4},
                      emit)
    assert not [e for e in _errores(eventos) if "no se puede repartir" in e]


def test_el_guard_de_carpeta_vacia_sigue_activo_sin_reparto(tmp_path):
    """La corrida normal (la de la app de escritorio) tiene que seguir
    rechazando un destino con residuos: sobre ellos, `unique_dest` duplica con
    `_1` y el recorte re-procesa `_CROP` viejos."""
    origen = tmp_path / "origen"
    origen.mkdir()
    destino = tmp_path / "destino"
    destino.mkdir()
    (destino / "residuo.JPG").write_bytes(b"x")

    eventos, emit = _emisor()
    organize.run_task("split_images",
                      {"origen": str(origen), "destino": str(destino)}, emit)
    assert any("no está vacía" in e for e in _errores(eventos))


def test_el_guard_de_carpeta_vacia_no_aplica_con_reparto(tmp_path):
    """Con N tareas al mismo destino solo la primera lo vería vacío; y `struct` y
    `post` trabajan por definición sobre lo que dejó la etapa anterior."""
    origen = tmp_path / "origen"
    origen.mkdir()
    destino = tmp_path / "destino"
    destino.mkdir()
    (destino / "RGB").mkdir()

    eventos, emit = _emisor()
    organize.run_task("split_images",
                      {"origen": str(origen), "destino": str(destino),
                       "etapa": "post", "shard_index": 0, "shard_count": 4}, emit)
    assert not [e for e in _errores(eventos) if "no está vacía" in e]
