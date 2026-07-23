"""Core headless de ATOM Organizer: dispara el pipeline sin Qt.

Reutiliza la orquestación REAL de la GUI (`MainWindow.split_images`) mediante un
host duck-typed, para no duplicar ni divergir del pipeline. El único acoplamiento
Qt del pipeline son los callbacks de progreso (solo usan `.emit()`), que aquí se
sustituyen por shims Python que reenvían el progreso a quien llame (el bridge de
pywebview → eventos a React).
"""
