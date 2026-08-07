"""Core headless de ATOM Organizer: dispara el pipeline sin Qt.

Las fases del pipeline viven en `atom_core.phases` (`PipelinePhasesMixin`) y las
heredan por igual la GUI Qt (`gui.MainWindow`) y el host headless
(`atom_core.organize.HeadlessHost`): una sola definición, sin duplicar ni
divergir. El único acoplamiento Qt del pipeline son los callbacks de progreso
(solo usan `.emit()`), que en la ruta headless se sustituyen por shims Python
que reenvían el progreso a quien llame (el bridge de pywebview → eventos a React).
"""
