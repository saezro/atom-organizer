"""`split_images` debe avisar ANTES de contar el origen y confirmar DESPUÉS.

`contar_imagenes_or_tmc` recorre el árbol entero del origen (segundos a
minutos en orígenes grandes) sin emitir nada por `progress_callback`: sin el
aviso, el modal de la UI parecía colgado durante ese tramo. El fix mete dos
líneas de `progress_callback.emit(...)` alrededor de la llamada, en
`atom_core/phases.py::split_images`, rama `if mis_imagenes_origen is None`
(modo NO repartido, el que usa la GUI de escritorio).

Este test verifica el ORDEN de emisión con mocks: un `progress_callback` falso
que acumula líneas, y un `contar_imagenes_or_tmc` falso que anota cuántas
líneas llevaba emitidas el callback en el momento en que se le llama, para
poder aseverar que el aviso salió ANTES y el total DESPUÉS.
"""
from utils import SplitImagesConfig, MODO_SOBRESCRIBIR

from atom_core.phases import PipelinePhasesMixin


class _FakeLogger:
    """Sustituto de `organizer_logger_obj`: solo necesita `.logger.info(...)`."""

    class _Log:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    def __init__(self):
        self.logger = self._Log()


class _FakeCallback:
    """Sustituto Qt-free de un Signal: acumula cada `.emit(...)`."""

    def __init__(self):
        self.lineas = []

    def emit(self, value):
        self.lineas.append(value)


class _FakeSplitImagesObj:
    """Sustituto de `split_images_obj`: no toca disco, solo registra la llamada."""

    def __init__(self):
        self.total_images_number = 0
        self.iterate_folders_calls = []
        self.stop = False

    def iterate_folders(self, *args, **kwargs):
        self.iterate_folders_calls.append((args, kwargs))

    def get_summarize(self):
        return {}


class _FakeUtilsObj:
    """Sustituto de `utils_obj`: `contar_imagenes_or_tmc` anota, en el momento
    en que se le llama, cuántas líneas llevaba emitidas el `progress_callback`
    — así se puede comprobar el ORDEN sin depender de temporizadores."""

    def __init__(self, progress_callback):
        self._progress_callback = progress_callback
        self.llamadas = []  # snapshots de `len(progress_callback.lineas)` en cada llamada
        self.prepare_output_folder_calls = []

    def contar_imagenes_or_tmc(self, *args, **kwargs):
        self.llamadas.append(len(self._progress_callback.lineas))
        return 42

    def prepare_output_folder(self, *args, **kwargs):
        self.prepare_output_folder_calls.append((args, kwargs))

    def logging_time(self, *args, **kwargs):
        return 0


class _FakeGenStructFolderObj:
    """Solo necesita aceptar la asignación de `modo_destino` que hace
    `split_images` antes de cualquier `if hacer_*`."""

    def __init__(self):
        self.modo_destino = None


class _FakeHost(PipelinePhasesMixin):
    """Host mínimo: solo los objetos que toca la RAMA de `split_images` que
    interesa a este test (split, modo no repartido). El resto de subprocesos
    (struct/crop/meta/thumbnails/tif) se desactivan por config, así que sus
    objetos de negocio ni se necesitan."""

    def __init__(self, progress_callback):
        self.organizer_logger_obj = _FakeLogger()
        self.utils_obj = _FakeUtilsObj(progress_callback)
        self.split_images_obj = _FakeSplitImagesObj()
        self.gen_struct_folder_obj = _FakeGenStructFolderObj()


def _cfg(origen: str, destino: str) -> SplitImagesConfig:
    return SplitImagesConfig(
        input_folder=origen, output_folder=destino,
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0,
        # `organize_images=False` corta `hacer_struct`; el resto de flags en
        # False corta crop/meta/thumbnails/tif: lo único que corre es el
        # bloque de SEPARACIÓN que este test necesita.
        organize_images=False,
        cropping_rgb=False, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=False, gen_thumbnails=False, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=0,
        gen_thumbnails_max_error=0, gen_thumbnails_subs_to_angle=0,
        choose_mode_auto=True, gen_thumbnails_rgb=False, gen_thumbnails_termica=False,
        convert_to_tif=False, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
        modo_destino=MODO_SOBRESCRIBIR,
    )


def test_avisa_antes_de_contar_y_confirma_el_total_despues(tmp_path):
    origen = tmp_path / "origen"
    destino = tmp_path / "destino"
    origen.mkdir()
    destino.mkdir()

    progress_callback = _FakeCallback()
    progress_bar = _FakeCallback()
    progress_summarize = _FakeCallback()

    host = _FakeHost(progress_callback)
    cfg = _cfg(str(origen), str(destino))

    host.split_images(cfg, progress_callback, progress_bar, progress_summarize)

    # `contar_imagenes_or_tmc` se llamó exactamente una vez (modo no repartido,
    # una sola separación normal, sin sufijo extra).
    assert len(host.utils_obj.llamadas) == 1, (
        "contar_imagenes_or_tmc debe llamarse exactamente una vez")

    lineas = progress_callback.lineas
    idx_aviso = next(
        i for i, l in enumerate(lineas) if "Contando imágenes del origen" in l)
    idx_total = next(
        i for i, l in enumerate(lineas) if "Total de imágenes a procesar" in l)

    # El snapshot tomado DENTRO de contar_imagenes_or_tmc dice que, en el
    # momento de la llamada, ya se había emitido el aviso (1 línea) y todavía
    # NO el total: así se prueba el orden real de ejecución, no solo el orden
    # final de la lista.
    assert host.utils_obj.llamadas[0] == idx_aviso + 1
    assert idx_aviso < idx_total, (
        "el aviso de 'Contando imágenes...' debe emitirse ANTES del total")
    assert idx_total > idx_aviso
    assert "42" in lineas[idx_total], "el total emitido debe ser el que devolvió contar_imagenes_or_tmc"
