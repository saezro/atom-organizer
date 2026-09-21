"""Sin estadillo, `run_task("split_images", ...)` debe pintar el checklist de
fases (`plan`) ANTES de cortar, y marcar la fase "Índice" como la que falla
(`phase` con index 1) en vez de dejar solo un banner de `error` suelto.
Ningún método real del pipeline debe llegar a arrancar (ver comentario del
gate en `atom_core.organize.run_task`)."""
from atom_core import organize


def _emisor():
    eventos = []

    def emit(kind, payload=None):
        eventos.append((kind, payload))
    return eventos, emit


def _de_tipo(eventos, kind):
    return [p for k, p in eventos if k == kind]


def test_sin_estadillo_pinta_plan_y_falla_en_fase_indice(monkeypatch, tmp_path):
    origen = tmp_path / "fotos"
    origen.mkdir()
    destino = tmp_path / "salida"
    destino.mkdir()

    class _HostNuncaLlamado:
        def __getattr__(self, name):
            raise AssertionError(
                f"el pipeline real no debe arrancar sin estadillo (se pidió {name!r})")

    monkeypatch.setattr(organize, "HeadlessHost", _HostNuncaLlamado)

    eventos, emit = _emisor()
    organize.run_task(
        "split_images",
        {"origen": str(origen), "destino": str(destino)},
        emit,
    )

    tipos = [k for k, _ in eventos]
    assert "plan" in tipos, f"el checklist de fases debe pintarse, eventos={tipos}"
    assert "error" in tipos
    assert tipos.index("plan") < tipos.index("error"), (
        "el 'plan' tiene que emitirse ANTES del 'error', para que el modal "
        f"no se quede en un banner suelto sin checklist. eventos={tipos}")

    fases = _de_tipo(eventos, "phase")
    assert fases, "sin estadillo debe emitirse un 'phase' que marque dónde corta"
    assert fases[0]["index"] == 1
    assert fases[0]["name"] == "Índice", (
        "el corte debe marcar la fase 'Índice' (donde el modal enseña los "
        f"datos del estadillo), no otra: {fases[0]}")

    errores = _de_tipo(eventos, "error")
    assert any("estadillo" in str(e).lower() for e in errores)

    assert not _de_tipo(eventos, "done"), "sin estadillo el run no debe llegar a 'done'"
