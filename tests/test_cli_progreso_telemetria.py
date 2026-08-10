"""El snapshot de StatsTracker -> cuerpo de progreso del RunReporter.

Contexto del bug que cubre: `items_hechos` salía NULL en el 100% de los runs
históricos de `organizer_runs` porque `RunReporter.progreso()` era código muerto
(nadie lo llamaba) y, además, el dict que emite el pipeline por el canal
`stats` NO usa las claves que `progreso()` espera:

    StatsTracker.snapshot() -> {"done": .., "total": .., "phase_index": ..}
    RunReporter.progreso()  -> espera {"files_done": .., "files_total": ..}

Conectarlos a pelo tampoco valía: `done`/`total` son POR FASE (se reinician en
`StatsTracker.start_phase`), así que el mapeo tiene que ser explícito y estar
documentado como "progreso de la fase actual", no del run entero.
"""
from organize_cli import progreso_desde_stats


def test_mapea_done_y_total_de_la_fase():
    cuerpo = progreso_desde_stats({"done": 120, "total": 450, "phase_index": 3})
    assert cuerpo == {"files_done": 120, "files_total": 450}


def test_total_nunca_por_debajo_de_done():
    # El total lo deriva StatsTracker del texto del pipeline y puede quedarse
    # corto; un "120/0" en la UI es peor que un total igualado a lo ya hecho.
    cuerpo = progreso_desde_stats({"done": 120, "total": 0})
    assert cuerpo == {"files_done": 120, "files_total": 120}


def test_sin_claves_utiles_no_manda_nada():
    assert progreso_desde_stats({"phase_index": 2, "rgb": 10}) is None
    assert progreso_desde_stats({}) is None
    assert progreso_desde_stats(None) is None


def test_no_arrastra_claves_ajenas():
    # `eta` la calcula el CLI en el canal `phase`; un snapshot de stats no debe
    # pisarla, ni colar rgb/termica/rot* en el cuerpo del PATCH.
    cuerpo = progreso_desde_stats(
        {"done": 5, "total": 5, "rgb": 3, "termica": 2, "rot270": 1, "eta": 99}
    )
    assert cuerpo == {"files_done": 5, "files_total": 5}


def test_foto_final_marca_final():
    cuerpo = progreso_desde_stats({"done": 10, "total": 10}, final=True)
    assert cuerpo == {"files_done": 10, "files_total": 10, "final": True}
