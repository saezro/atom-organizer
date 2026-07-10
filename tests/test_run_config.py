"""Tests de la Task 13 (RunConfig).

IMPORTANTE (entorno): `gui.py` importa PySide6 y los `qt_designer_files`
(módulos generados por pyuic/pyside con miles de llamadas `QtWidgets.XXX(...)`) a nivel de módulo.
PySide6 NO está instalado en este entorno de test (no hace falta instalarlo: es una app de
escritorio, no se ejecuta aquí) y `MainWindow` no se puede importar directamente sin arrastrar
todo Qt.

Por eso, en lugar de `from gui import MainWindow`, este módulo:
1. Verifica el módulo PURO `utils.py` (RunConfig y sub-configs, dataclasses sin Qt) directamente.
2. Extrae, vía `ast`, el código fuente de `call_to_compress_image` (una función concreta,
   representativa del patrón mecánico) y lo ejecuta de forma aislada (sin importar PySide6),
   comprobando que opera exclusivamente a partir de `cfg`, igual que describe el plan.
3. Verifica, también vía `ast` (sin ejecutar/importar nada), que las 14 funciones de negocio
   de la Task 13 aceptan su `cfg: <SubConfig>` y ya no leen ningún widget Qt (`self.le_*`,
   `self.sb_*`, `self.cb_*`, `self.rb_*`, `self.cob_*`) en su cuerpo.
"""
import ast
import os
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from utils import RunConfig, CompressRgbsConfig


MAIN_APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gui.py")

BUSINESS_FUNCTIONS_AND_CFG_TYPES = {
    "call_to_compress_image": "CompressRgbsConfig",
    "gen_meta_location": "GenMetaLocationConfig",
    "split_images": "SplitImagesConfig",
    "gen_struct_folder": "GenStructFolderConfig",
    "gen_thumbnails": "GenThumbnailsConfig",
    "gen_thumbnails_for_aerotools": "GenThumbnailsConfig",
    "rename_images": "RenameImagesConfig",
    "do_extraction": "ExtractionConfig",
    "do_rgb_aerotools_processing": "RgbAerotoolsConfig",
    "do_manual_geotagging": "ManualGeotaggingConfig",
    "do_convert_to_tif": "ConvertToTifConfig",
    "do_rgb_cropping": "RgbCroppingConfig",
    "do_tif_rotating": "TifRotatingConfig",
}

FORBIDDEN_WIDGET_PREFIXES = ("le_", "sb_", "cb_", "rb_", "cob_")


def _make_minimal_run_config(input_folder: str, output_folder: str, compress_level: int = 80, include_subfolders: bool = False) -> RunConfig:
    """Helper de test: construye un RunConfig válido con solo compress_rgbs poblado de forma realista
    y el resto de sub-configs con valores mínimos (no se usan en este test)."""
    return RunConfig(
        compress_rgbs=CompressRgbsConfig(
            input_folder=input_folder,
            output_folder=output_folder,
            compress_level=compress_level,
            include_subfolders=include_subfolders,
        ),
        gen_meta_location=None,
        split_images=None,
        gen_thumbnails=None,
        rename_images=None,
        extraction=None,
        gen_struct_folder=None,
        rgb_aerotools=None,
        manual_geotagging=None,
        convert_to_tif=None,
        rgb_cropping=None,
        tif_rotating=None,
    )


def _find_class_and_methods(source: str, class_name: str) -> dict:
    """Devuelve {method_name: ast.FunctionDef} de todos los métodos definidos directamente en `class_name`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
    raise AssertionError(f"No se encontró la clase {class_name} en {MAIN_APP_PATH}")


@pytest.fixture(scope="module")
def main_app_source() -> str:
    with open(MAIN_APP_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def main_window_methods(main_app_source) -> dict:
    return _find_class_and_methods(main_app_source, "MainWindow")


def test_run_config_dataclasses_son_puras_y_frozen():
    """El módulo run_config.py no depende de Qt y sus dataclasses son inmutables."""
    cfg = CompressRgbsConfig(input_folder="in", output_folder="out", compress_level=80, include_subfolders=False)
    assert cfg.input_folder == "in"
    with pytest.raises(FrozenInstanceError):
        cfg.input_folder = "otro"


@pytest.mark.parametrize("function_name,cfg_type_name", list(BUSINESS_FUNCTIONS_AND_CFG_TYPES.items()))
def test_funcion_de_negocio_recibe_cfg_y_no_lee_widgets_qt(main_window_methods, function_name, cfg_type_name):
    """Cada una de las 14 funciones de negocio debe:
    - Aceptar `cfg: <SubConfig>` como primer argumento posicional tras `self`.
    - No contener en su cuerpo ninguna lectura de widget Qt (self.le_*/sb_*/cb_*/rb_*/cob_*).
    """
    assert function_name in main_window_methods, f"{function_name} no está definida en MainWindow"
    func_node = main_window_methods[function_name]

    args = func_node.args.args
    assert len(args) >= 2, f"{function_name} debe aceptar (self, cfg, ...)"
    assert args[0].arg == "self"
    cfg_arg = args[1]
    assert cfg_arg.arg == "cfg", f"{function_name}: el segundo argumento debe llamarse 'cfg', es '{cfg_arg.arg}'"
    assert cfg_arg.annotation is not None, f"{function_name}: 'cfg' debe llevar anotación de tipo"
    annotation_name = cfg_arg.annotation.id if isinstance(cfg_arg.annotation, ast.Name) else ast.dump(cfg_arg.annotation)
    assert annotation_name == cfg_type_name, f"{function_name}: cfg debe anotarse como {cfg_type_name}, es {annotation_name}"

    body_source = ast.get_source_segment(open(MAIN_APP_PATH, encoding="utf-8").read(), func_node)
    for node in ast.walk(func_node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            assert not node.attr.startswith(FORBIDDEN_WIDGET_PREFIXES), (
                f"{function_name} sigue leyendo el widget Qt 'self.{node.attr}' en el hilo worker "
                f"(debe venir de 'cfg' en su lugar). Fuente:\n{body_source}"
            )


def test_call_to_compress_image_usa_run_config_sin_leer_widgets_qt(organizer_logger_stub, tmp_path):
    """call_to_compress_image debe operar completamente a partir de cfg.compress_rgbs, sin acceder a self.le_*/self.sb_*/self.cb_*.

    Se extrae el código fuente REAL de la función (vía ast) desde gui.py y se ejecuta en un
    namespace aislado, sin necesidad de importar PySide6 (no instalado ni necesario en este entorno de test).
    """
    from pipeline import CompressImage
    import utils as utils_module

    input_folder = tmp_path / "in"
    output_folder = tmp_path / "out"
    input_folder.mkdir()
    output_folder.mkdir()

    cfg = _make_minimal_run_config(str(input_folder), str(output_folder), compress_level=80, include_subfolders=False)

    compress_image_obj = CompressImage(organizer_logger_stub)
    utils_obj = utils_module.Utils(organizer_logger_stub)

    # Objeto mínimo que reproduce la parte de MainWindow que usa call_to_compress_image, SIN heredar de QWidget
    # ni tocar Qt: así se prueba la lógica pura extraída, no la GUI.
    class _FakeMainWindowPart:
        def __init__(self):
            self.compress_image_obj = compress_image_obj
            self.utils_obj = utils_obj
            self.organizer_logger_obj = organizer_logger_stub
            self.new_log_gui = MagicMock()

        def show_summarize(self, summarize_dict, progress_summarize):
            pass

    source = open(MAIN_APP_PATH, encoding="utf-8").read()
    methods = _find_class_and_methods(source, "MainWindow")
    func_node = methods["call_to_compress_image"]
    func_source = ast.get_source_segment(source, func_node)
    # ast.get_source_segment devuelve el bloque con su indentación original (4 espacios); hay que "dedentarlo"
    # para poder compilarlo como función de módulo.
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in func_source.splitlines())

    namespace = {"CompressRgbsConfig": CompressRgbsConfig}
    exec(compile(dedented, MAIN_APP_PATH, "exec"), namespace)
    bound_call_to_compress_image = namespace["call_to_compress_image"].__get__(_FakeMainWindowPart(), _FakeMainWindowPart)

    fake = bound_call_to_compress_image.__self__

    progress_callback = MagicMock()
    progress_bar = MagicMock()
    progress_summarize = MagicMock()

    bound_call_to_compress_image(cfg.compress_rgbs, progress_callback=progress_callback, progress_bar=progress_bar, progress_summarize=progress_summarize)

    assert not hasattr(fake, "le_gb_compress_rgbs_input_folder")


def test_capture_run_config_existe_y_rellena_todos_los_subcampos(main_window_methods, main_app_source):
    """_capture_run_config debe existir y construir un RunConfig con TODOS los sub-configs poblados (ninguno en None)."""
    assert "_capture_run_config" in main_window_methods
    func_node = main_window_methods["_capture_run_config"]

    # Busca la llamada a RunConfig(...) dentro del cuerpo y comprueba que ningún keyword se pasa como None literal.
    run_config_calls = [
        node for node in ast.walk(func_node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "RunConfig"
    ]
    assert len(run_config_calls) == 1, "_capture_run_config debe construir exactamente un RunConfig"
    call = run_config_calls[0]
    for kw in call.keywords:
        assert not (isinstance(kw.value, ast.Constant) and kw.value.value is None), (
            f"_capture_run_config deja '{kw.arg}' en None: debe rellenar TODOS los sub-configs"
        )
    expected_fields = {f.name for f in RunConfig.__dataclass_fields__.values()}
    actual_fields = {kw.arg for kw in call.keywords}
    assert actual_fields == expected_fields, f"_capture_run_config no rellena todos los campos de RunConfig: faltan {expected_fields - actual_fields}"


def test_run_thread_captura_cfg_antes_de_arrancar_el_worker(main_app_source):
    """run_thread debe llamar a self._capture_run_config() y pasar el sub-config correspondiente a cada Worker,
    en vez de dejar que las funciones de negocio lean self.le_*/sb_*/cb_*/rb_* ya dentro del hilo del QThreadPool."""
    assert "self._capture_run_config()" in main_app_source
    assert "worker = Worker(self.call_to_compress_image, cfg.compress_rgbs)" in main_app_source
