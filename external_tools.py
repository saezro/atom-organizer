"""
Resolución de binarios externos y mapa de capacidades por sistema operativo.

FeatureUnavailableError se define como subclase local (no depende de utils.ExternalToolError
para evitar import circular entre general_functions.utils y general_functions.platform_tools).
"""
import configparser
import os
import sys


class FeatureUnavailableError(RuntimeError):
    """Se lanza cuando una capacidad no está disponible en el SO actual."""
    pass


FEATURE_MATRIX: dict[str, set[str]] = {
    "tmc_extraction": {"win"},
    "dji_irp_thermal": {"win", "linux"},
    "exif": {"win", "linux"},
    "ffmpeg_video": {"win", "linux"},
}


def _current_os() -> str:
    return "win" if sys.platform.startswith("win") else "linux"


def app_base_dir() -> str:
    """Directorio base de la app: _MEIPASS (PyInstaller) o dir del ejecutable/script."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def resource_path(*parts: str) -> str:
    """Construye una ruta a un recurso empaquetado bajo app_base_dir(), con el separador nativo del SO."""
    return os.path.join(app_base_dir(), *parts)


def is_feature_available(feature: str) -> bool:
    return _current_os() in FEATURE_MATRIX[feature]


def dji_bin_name() -> str:
    """Basename del binario DJI que debe existir en programas_externos/<dron>/ según el SO:
    el .exe en Windows, la librería nativa libdirp.so en Linux (la que usa dji_irp_linux.py)."""
    return "dji_irp.exe" if _current_os() == "win" else "libdirp.so"


def has_dron_binaries(selector: str) -> bool:
    """True si `selector` (M2EA/M4T) mapea a una carpeta programas_externos/<selector>/
    con el binario DJI correcto para el SO actual. Selector vacío o inexistente -> False."""
    if not selector:
        return False
    return os.path.isfile(os.path.join(app_base_dir(), "programas_externos", selector, dji_bin_name()))


def resolve_tool(name: str) -> str:
    """
    Devuelve la ruta/comando invocable para el binario `name` según el SO actual.

    Arguments:
    ---------
    - name - uno de: "exiftool", "ffmpeg", "dji_irp", "ThermoViewer".
    """
    base = app_base_dir()
    is_windows = _current_os() == "win"

    if name == "exiftool":
        if is_windows:
            return os.path.join(base, "programas_externos", "exiftool.exe")
        return "exiftool"

    if name == "ffmpeg":
        if is_windows:
            return os.path.join(base, "programas_externos", "ffmpeg.exe")
        return "ffmpeg"

    if name == "dji_irp":
        if is_windows:
            return os.path.join(base, "programas_externos", "dji_irp.exe")
        return os.path.join(base, "programas_externos", "libdirp.so")

    if name == "ThermoViewer":
        if not is_windows:
            raise FeatureUnavailableError(
                "Extracción de vídeo TMC requiere ThermoViewer, solo Windows; "
                "en Linux usa Wine o el pipeline M4T/ffmpeg para .MOV/.MP4"
            )
        for candidate in (
            r"C:/Program Files (x86)/ThermoViewer/ThermoViewer.exe",
            r"C:/Windows/ThermoViewer/ThermoViewer.exe",
        ):
            if os.path.exists(candidate):
                return candidate
        return r"C:/Program Files (x86)/ThermoViewer/ThermoViewer.exe"

    raise ValueError(f"Herramienta desconocida: {name}")


def load_config_or_default(file_name: str) -> dict:
    """
    Carga un archivo de configuración .ini y devuelve un dict con los valores encontrados,
    o valores por defecto si el archivo no existe, está vacío, o le faltan secciones/claves.

    Arguments:
    ---------
    - file_name - ruta del archivo .ini a cargar. Puede ser "" o None (p.ej. diálogo cancelado).
    """
    defaults = {"ruta_thermoviewer": "", "percentage_by_models": {}}

    if not file_name or not os.path.exists(file_name):
        return defaults

    configuracion = configparser.ConfigParser()
    configuracion.read(file_name)

    resultado = dict(defaults)
    if "paths" in configuracion and "ruta_thermoviewer" in configuracion["paths"]:
        resultado["ruta_thermoviewer"] = configuracion["paths"]["ruta_thermoviewer"]

    if "percentage_by_models" in configuracion:
        resultado["percentage_by_models"] = {
            key.upper(): int(value) for key, value in configuracion["percentage_by_models"].items()
        }

    return resultado


class ReadLoadConfig:

    def __init__(self) -> None:
        self.ruta_thermoviewer = ""
        self.percentage_by_models = {}

    def load_new_config(self, file_name: str):
        valores = load_config_or_default(file_name)
        self.ruta_thermoviewer = valores["ruta_thermoviewer"]
        self.percentage_by_models = valores["percentage_by_models"]
