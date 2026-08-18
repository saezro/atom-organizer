"""
Resolución de binarios externos y mapa de capacidades por sistema operativo.

FeatureUnavailableError se define como subclase local (no depende de utils.ExternalToolError
para evitar import circular entre general_functions.utils y general_functions.platform_tools).
"""
import configparser
import os
import platform
import shutil
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


def _user_config_path() -> str:
    """Ruta EDITABLE y persistente del Config.ini de usuario.

    NO es resource_path()/_MEIPASS: bajo el onefile de Windows ese dir se extrae
    a un temporal efímero (%TEMP%\\_MEIxxxx) que se BORRA al cerrar el proceso, con
    lo que un Config.ini editado desde la UI se perdería. Persistimos en el perfil
    del usuario (%APPDATA%\\ATOM-Organizer en Windows, ~/.config/atom-organizer en
    Linux). En el primer arranque se siembra copiando el Config.ini empaquetado
    (resource_path) como valores por defecto."""
    if sys.platform.startswith("win"):
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ATOM-Organizer")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "atom-organizer")
    path = os.path.join(base, "Config.ini")
    if not os.path.exists(path):
        os.makedirs(base, exist_ok=True)
        bundled = resource_path("config", "Config.ini")  # el .spec lo empaqueta → seed
        if os.path.exists(bundled):
            shutil.copy(bundled, path)
    return path


def user_log_dir() -> str:
    """Carpeta de logs, en el perfil del usuario y SIEMPRE escribible.

    El default histórico era el literal relativo "Logs", o sea el CWD. Instalada,
    la app se lanza desde un acceso directo y el CWD NO es el directorio de la app:
    si el «Iniciar en» del .lnk viene vacío se hereda el del Explorer, típicamente
    C:\\Windows\\System32 → `mkdir("Logs")` revienta con
    `PermissionError: [WinError 5] Acceso denegado: 'Logs'`. Mismo criterio y misma
    base que _user_config_path()."""
    if sys.platform.startswith("win"):
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ATOM-Organizer")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "atom-organizer")
    return os.path.join(base, "Logs")


def is_feature_available(feature: str) -> bool:
    return _current_os() in FEATURE_MATRIX[feature]


# Carpeta ÚNICA del DJI Thermal SDK dentro de programas_externos/.
# Hasta la 3.3.2 había dos, M2EA/ y M4T/, elegidas por autodetección del modelo de
# dron... siendo byte a byte idénticas (mismo md5 en los 19 ficheros de ambas, no solo
# en dji_irp.exe y libdirp.dll). La separación no separaba nada: el SDK es uno solo y
# resuelve el modelo leyendo el propio R-JPEG. Duplicaba 6,7 MB de instalador y añadía
# una ruta variable —justo la pieza que hay que descartar cuando el conversor falla en
# la máquina de un usuario—. Ahora hay una sola carpeta y una sola ruta posible.
DJI_SDK_DIRNAME = "DJI"


def dji_bin_name() -> str:
    """Basename del binario DJI que debe existir en programas_externos/DJI/ según el SO:
    el .exe en Windows, la librería nativa libdirp.so en Linux (la que usa dji_irp_linux.py)."""
    return "dji_irp.exe" if _current_os() == "win" else "libdirp.so"


def dji_sdk_dir() -> str:
    """Ruta absoluta de la carpeta única del DJI Thermal SDK."""
    return os.path.join(app_base_dir(), "programas_externos", DJI_SDK_DIRNAME)


def dji_utility_path() -> str:
    """Ruta del ejecutable dji_irp.exe que se lanza por imagen (Windows).

    Es el ÚNICO sitio que construye esta ruta: si el conversor no aparece donde toca,
    hay un solo candidato que comprobar, no uno por modelo de dron."""
    return os.path.join(dji_sdk_dir(), "dji_irp.exe")


# --- SDK térmico DJI en arquitecturas no-x86 (Raspberry Pi) -------------------
# DJI publica el Thermal SDK SOLO para x86-64 (verificado hasta la v1.8, 2025-08:
# tsdk-core/lib/ trae linux/release_x64, linux/release_x86, windows/release_x64 y
# windows/release_x86, y ningún changelog menciona ARM). En un aarch64 las .so no
# cargan: no es un fallo de dependencias, es que el ELF es de otra arquitectura.
#
# La salida térmica NO es negociable —es la matriz de temperaturas de la que salen
# los informes—, así que no se reimplementa ni se aproxima: se ejecuta el MISMO SDK
# de siempre bajo box64 (recompilador dinámico x86-64 -> ARM). Como una .so x86 no
# puede cargarse en un proceso aarch64, lo que se emula es el proceso entero: un
# intérprete Python x86-64 mínimo que corre el mismo dji_irp_linux.py.
#
# Verificado sobre 24 R-JPEG reales de un vuelo: los 24 .raw salen byte a byte
# idénticos (sha256) a los de x86 nativo. Coste: ~2,2x en secuencial, que el
# paralelismo por imagen absorbe.
X86_RUNTIME_DIRNAME = "x86-runtime"


def is_x86_64() -> bool:
    """True si la máquina actual ejecuta binarios x86-64 de forma nativa."""
    return platform.machine().lower() in ("x86_64", "amd64")


def x86_runtime_dir() -> str:
    """Carpeta del intérprete Python x86-64 usado para emular el SDK DJI en ARM."""
    return os.path.join(app_base_dir(), "programas_externos", X86_RUNTIME_DIRNAME)


def x86_python_path() -> str:
    """Ruta del intérprete Python x86-64 embebido."""
    return os.path.join(x86_runtime_dir(), "bin", "python3")


def x86_support_libs_dir() -> str:
    """Librerías x86-64 de apoyo (libgomp real: la que box64 emula por dentro está
    incompleta y le faltan GOMP_critical_start/end, que libdirp.so sí usa)."""
    return os.path.join(x86_runtime_dir(), "lib-x86")


def box64_path() -> str | None:
    """Ruta del emulador box64, o None si no está instalado."""
    return shutil.which("box64")


def dji_linux_launcher(lib_dir: str) -> tuple[list[str], dict[str, str]]:
    """Prefijo de comando y variables de entorno para lanzar dji_irp_linux.py.

    En x86-64 no hay nada que emular: el intérprete actual y el entorno tal cual.
    En cualquier otra arquitectura devuelve el lanzador box64 + Python x86-64, de
    forma que el resto del pipeline no sepa en qué máquina está corriendo.
    """
    if is_x86_64():
        return [sys.executable], {}

    box64 = box64_path()
    py_x86 = x86_python_path()
    faltan = []
    if not box64:
        faltan.append("el emulador box64 (apt install box64)")
    if not os.path.isfile(py_x86):
        faltan.append("el intérprete x86-64 en {0}".format(x86_runtime_dir()))
    if faltan:
        raise FeatureUnavailableError(
            "El SDK térmico de DJI es solo x86-64 y esta máquina es {0}. Para convertir "
            "térmicas aquí falta {1}. Instálalo con scripts/instalar_runtime_x86.sh.".format(
                platform.machine(), " y ".join(faltan)))

    env = {
        # Fuerza a box64 a emular la libgomp x86 real en vez de su wrapper nativo.
        "BOX64_EMULATED_LIBS": "libgomp.so.1",
        "LD_LIBRARY_PATH": os.pathsep.join(
            p for p in (x86_support_libs_dir(), lib_dir, os.environ.get("LD_LIBRARY_PATH", "")) if p),
    }
    return [box64, py_x86], env


def has_dji_binaries() -> bool:
    """True si la carpeta única del SDK trae el binario DJI correcto para el SO actual."""
    return os.path.isfile(os.path.join(dji_sdk_dir(), dji_bin_name()))


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
        # Apuntaba a programas_externos/ a pelo, donde el binario nunca ha estado
        # (vivía en las subcarpetas por dron). Ahora la carpeta única lo hace correcto.
        return os.path.join(dji_sdk_dir(), dji_bin_name())

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
