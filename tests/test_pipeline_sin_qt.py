"""Red de seguridad: la ruta headless del pipeline NO debe arrastrar Qt.

Las fases (`split_images`, `do_convert_to_tif`, …) vivían dentro de la clase Qt
`gui.MainWindow` y `atom_core/organize.py` hacía `from gui import MainWindow`
para rebindarlas una a una. Eso metía PySide6 (~400 MB) en cualquier imagen de
servidor y dejaba fuera cualquier método que se olvidara en la lista de rebinds
(ya pasó con `_resolve_dron_selector`: AttributeError en runtime en cuanto se
activaba la conversión a TIF).

Ahora viven en `atom_core.phases.PipelinePhasesMixin`, del que heredan los dos
hosts. Estos tests fijan esa invariante para que no se reintroduzca en silencio:
un `import` de Qt de vuelta en la cadena headless rompe el build de servidor
meses después, no aquí.
"""
import importlib
import subprocess
import sys

import pytest

# Las 15 fases que el host headless necesita para cubrir los 13 tasks de run_task.
FASES = (
    "split_images",
    "gen_meta_location",
    "call_to_compress_image",
    "gen_thumbnails",
    "gen_thumbnails_for_aerotools",
    "rename_images",
    "do_extraction",
    "gen_struct_folder",
    "do_rgb_aerotools_processing",
    "do_manual_geotagging",
    "do_convert_to_tif",
    "_resolve_dron_selector",
    "do_rgb_cropping",
    "do_tif_rotating",
    "show_summarize",
)


def test_phases_no_importa_qt():
    """`atom_core.phases` debe ser importable con PySide6 fuera de escena."""
    codigo = (
        "import sys\n"
        "class Veto:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] == 'PySide6':\n"
        "            raise ImportError('PySide6 no debe entrar en la ruta headless')\n"
        "sys.meta_path.insert(0, Veto())\n"
        "import atom_core.phases\n"
    )
    proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert proc.returncode == 0, (
        "atom_core.phases arrastra Qt:\n" + proc.stderr)


def test_organize_no_importa_qt_ni_gui():
    """El dispatcher headless tampoco: ni PySide6 ni `gui` deben acabar en sys.modules."""
    codigo = (
        "import sys\n"
        "class Veto:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] == 'PySide6':\n"
        "            raise ImportError('PySide6 no debe entrar en la ruta headless')\n"
        "sys.meta_path.insert(0, Veto())\n"
        "import atom_core.organize\n"
        "assert 'gui' not in sys.modules, 'organize sigue importando gui.py'\n"
        "assert 'PySide6' not in sys.modules\n"
    )
    proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert proc.returncode == 0, (
        "atom_core.organize arrastra Qt o gui.py:\n" + proc.stderr)


@pytest.mark.parametrize("fase", FASES)
def test_headless_host_expone_todas_las_fases(fase):
    """Ninguna fase puede faltar en el host headless: la falta se manifiesta como
    AttributeError a mitad de una corrida real, no al arrancar."""
    organize = importlib.import_module("atom_core.organize")
    assert hasattr(organize.HeadlessHost, fase), (
        f"HeadlessHost no expone '{fase}'")


def test_los_tasks_declarados_apuntan_a_metodos_que_existen():
    """Cada entrada de `_TASKS` debe resolver a un método real del host."""
    organize = importlib.import_module("atom_core.organize")
    faltan = [
        (task, metodo)
        for task, (metodo, _cfg) in organize._TASKS.items()
        if not hasattr(organize.HeadlessHost, metodo)
    ]
    assert not faltan, f"tasks apuntando a métodos inexistentes: {faltan}"
