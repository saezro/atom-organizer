"""Traducción de los marcadores de texto a `emit("stats", ...)` y duración
por fase en `payload_done["fases"]` (`atom_core.organize.run_task`).

`construir_indice`/`aplicar_rgb`/`aplicar_termicas` no tienen acceso al
`emit` real de `run_task` — solo a las tres señales de siempre
(`progress_callback`/`progress_bar`/`progress_summarize`) — así que viajan
como una línea de texto con un prefijo reconocible (`STATS_INDICE_PREFIX` /
`STATS_APPLY_PREFIX`) que `run_task` intercepta y reenvía como
`emit("stats", payload)`. Estos tests sujetan esa traducción, y que
`payload_done["fases"]` acumula la duración de CADA fase en orden (ver
LEDGER-metricas-progreso.md).

No se ejecuta el pipeline real (evita `HeadlessHost`, que carga config y
loggers reales): se sustituye `atom_core.organize.HeadlessHost` por un doble
mínimo cuyo único "método de tarea" emite las fases y los marcadores que
cada test necesita. Doble a mano, nunca `unittest.mock` — estilo de la casa.
"""
import json

from atom_core import organize
from atom_core.apply import STATS_APPLY_PREFIX
from atom_core.indice import STATS_INDICE_PREFIX
from utils import RenameImagesConfig


def _emisor():
    eventos = []

    def emit(kind, payload=None):
        eventos.append((kind, payload))
    return eventos, emit


def _de_tipo(eventos, kind):
    return [p for k, p in eventos if k == kind]


class _HostFalso:
    """Doble de `HeadlessHost`: no construye NINGÚN objeto de negocio real
    (logger, config, pipeline de dron...), solo expone el método que
    `_TASKS["stats_test"]` apunte, con la firma `(cfg, pcb, pbar, psum)` que
    exige `run_task`."""

    def __init__(self, acciones):
        self._acciones = acciones

    def correr(self, cfg, pcb, pbar, psum):
        for accion in self._acciones:
            accion(pcb, pbar, psum)


def _run_con_acciones(monkeypatch, acciones):
    monkeypatch.setitem(organize._TASKS, "stats_test", ("correr", RenameImagesConfig))
    monkeypatch.setattr(organize, "HeadlessHost", lambda: _HostFalso(acciones))
    eventos, emit = _emisor()
    organize.run_task("stats_test", {}, emit)
    return eventos


def test_marcador_de_indice_se_traduce_a_stats_y_no_ensucia_el_summary(monkeypatch):
    payload_indice = {
        "fase": "Índice", "total": 10, "rgb": 6, "termica": 2, "rgb_extra": 1,
        "sin_asignar": 1, "sin_timestamp": 0, "vuelos": 2,
    }

    def _emitir(pcb, pbar, psum):
        psum.emit(STATS_INDICE_PREFIX + json.dumps(payload_indice))

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    stats = _de_tipo(eventos, "stats")
    assert payload_indice in stats, "el marcador del índice debe llegar tal cual como evento stats"
    # El marcador es fontanería interna: no debe colarse en el panel de
    # summary que ve el usuario (sería un JSON crudo en la UI).
    assert not any(STATS_INDICE_PREFIX in str(p) for p in _de_tipo(eventos, "summary"))


def test_marcador_de_apply_se_traduce_a_stats_y_no_ensucia_el_log(monkeypatch):
    payload_apply = {
        "fase": "Imágenes RGB", "done": 5, "total": 20, "rgb": 5, "termica": 0,
        "img_por_segundo": 2.5, "eta_segundos": 6,
    }

    def _emitir(pcb, pbar, psum):
        pcb.emit(STATS_APPLY_PREFIX + json.dumps(payload_apply))

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    stats = _de_tipo(eventos, "stats")
    assert payload_apply in stats
    assert not any(STATS_APPLY_PREFIX in str(p) for p in _de_tipo(eventos, "log"))


def test_payload_done_trae_las_fases_en_orden_con_su_duracion(monkeypatch):
    """`payload_done["fases"]` debe listar CADA fase que arrancó, en el
    orden en que arrancaron, con su duración — es lo que el modal usa para
    destacar cuál fue la más lenta."""

    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Fase A")
        psum.emit("---> SUBPROCESO: Fase B")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    dones = _de_tipo(eventos, "done")
    assert len(dones) == 1
    fases = dones[0]["fases"]
    nombres = [f["nombre"] for f in fases]
    assert nombres == ["Fase A", "Fase B"]
    for fase in fases:
        assert isinstance(fase["segundos"], float)
        assert fase["segundos"] >= 0.0


def test_task_sin_fases_deja_la_lista_vacia(monkeypatch):
    """Una task sin ningún prefijo `---> SUBPROCESO:` (no pasa por el
    checklist de fases) no debe reventar: `fases` queda vacía en vez de
    faltar la clave."""
    eventos = _run_con_acciones(monkeypatch, [lambda pcb, pbar, psum: None])

    dones = _de_tipo(eventos, "done")
    assert len(dones) == 1
    assert dones[0]["fases"] == []
