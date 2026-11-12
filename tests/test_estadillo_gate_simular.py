"""`--fase apply --simular` no exige estadillo (el apply solo lee el manifiesto);
fuera de simulación el gate sigue abortando."""
from atom_core import organize, simulacion


class _HostCorta:
    def __getattr__(self, name):
        raise RuntimeError("fin del test: el gate de estadillo ya pasó")


def _correr(monkeypatch, tmp_path, simular):
    origen = tmp_path / "fotos"
    origen.mkdir()
    destino = tmp_path / "salida"
    destino.mkdir()
    monkeypatch.setattr(organize, "HeadlessHost", _HostCorta)
    eventos = []

    def emit(kind, payload=None):
        eventos.append((kind, payload))
    if simular:
        simulacion.activar()
    try:
        try:
            organize.run_task(
                "split_images",
                {"origen": str(origen), "destino": str(destino), "fase": "apply",
                 "ejecucion": "e1"},
                emit)
        except Exception:
            pass
    finally:
        simulacion.desactivar()
    return " ".join(str(p) for k, p in eventos if k == "error")


def test_apply_simulado_sin_estadillo_no_aborta(monkeypatch, tmp_path):
    assert "No hay estadillo" not in _correr(monkeypatch, tmp_path, True)


def test_apply_no_simulado_sin_estadillo_aborta(monkeypatch, tmp_path):
    assert "No hay estadillo" in _correr(monkeypatch, tmp_path, False)
