"""Tests de la Task 23 (tema oscuro global).

IMPORTANTE (entorno, ver tests/test_run_config.py para el mismo patrón): `gui.py`
importa PySide6 y los `qt_designer_files` a nivel de módulo. PySide6 NO está instalado en este entorno
de test (no hace falta: es una app de escritorio, no se ejecuta aquí) y `MainWindow` no se puede importar
directamente sin arrastrar todo Qt.

Por eso el segundo test NO hace `import gui`, sino que verifica vía `ast`
(sin ejecutar/importar nada) que `DARK_QSS` está definido a nivel de módulo en `gui.py` y que
`MainWindow.__init__` lo referencia (llamada a `self.setStyleSheet(DARK_QSS)`).
"""
import ast
import os

MAIN_APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gui.py")


def _load_dark_qss_from_source() -> str:
    """Extrae DARK_QSS (y sus constantes de color asociadas: BACKGROUND/ACCENT/ACCENT_SOFT/
    TEXT/MUTED/PANEL) del código fuente de gui.py vía ast, sin importar PySide6."""
    with open(MAIN_APP_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    color_names = {"BACKGROUND", "ACCENT", "ACCENT_SOFT", "TEXT", "MUTED", "PANEL", "DARK_QSS"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in color_names for t in node.targets
        )
    ]
    assert nodes, "DARK_QSS no está definido a nivel de módulo en gui.py"
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), MAIN_APP_PATH, "exec"), namespace)
    return namespace["DARK_QSS"]


DARK_QSS = _load_dark_qss_from_source()


def test_dark_qss_contiene_colores_clave():
    assert isinstance(DARK_QSS, str)
    assert len(DARK_QSS) > 0
    assert "#0a0a0a" in DARK_QSS
    assert "#EE763C" in DARK_QSS


def test_mainwindow_aplica_dark_qss():
    with open(MAIN_APP_PATH, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # 1. DARK_QSS definido a nivel de módulo (fusionado desde general_functions/dark_theme.py)
    defines_dark_qss = any(
        isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DARK_QSS" for t in node.targets
        )
        for node in tree.body
    )
    assert defines_dark_qss, "gui.py no define DARK_QSS a nivel de módulo"

    # 2. MainWindow.__init__ referencia DARK_QSS (self.setStyleSheet(DARK_QSS))
    main_window_cls = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    init_method = next(
        node for node in main_window_cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    init_source = ast.unparse(init_method)
    assert "DARK_QSS" in init_source
    assert "setStyleSheet" in init_source
