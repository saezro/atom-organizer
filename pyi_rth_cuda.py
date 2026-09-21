"""Runtime hook PyInstaller — camino GPU opt-in (ORGANIZER_RGB_GPU=1).

Se inyecta en el arranque del binario (onedir Windows y AppImage Linux) ANTES
de cualquier import de la app — ver `runtime_hooks=` en
atom_organizer_webview.spec / atom_organizer_webview_linux.spec. TODO va en
try/except: un fallo aquí NUNCA debe impedir arrancar la app, con o sin GPU
en la máquina y con o sin `requirements-gpu.txt` empaquetado —
`atom_core.rgb_gpu.activo()` ya cae a CPU si cupy/nvimgcodec no cargan.

Windows: los paquetes nvidia-*-cu12 (cuda_runtime, cuda_nvrtc, nvjpeg,
nvimgcodec) llevan sus DLL bajo su propio directorio de paquete
(`nvidia/<paquete>/bin/*.dll` o, en nvimgcodec, `nvidia/nvimgcodec/*.dll` y
`nvidia/nvimgcodec/extensions/*.dll`), no junto al .exe, así que el buscador
de DLL de Windows no las ve por defecto (el .exe vive en la raíz del onedir,
esas carpetas cuelgan de subdirectorios). Se registran con
`os.add_dll_directory` (Python >= 3.8) todas las carpetas bajo <base>/nvidia/**
que contengan al menos un .dll.

Precarga de cudart/nvrtc/nvjpeg (ambas plataformas): `cuda-pathfinder`
1.3.4 (dependencia de cupy-cuda12x, ver `cupy_backends/cuda/libs/__init__.py`
y el símbolo `__pyx_v_pathfinder` en `cupy_backends/cuda/api/runtime.*.so`,
que resuelve cudart y — vía `SoftLink` diferido — nvrtc) busca las libs en
`site.getsitepackages()` (`cuda/pathfinder/_dynamic_libs/load_nvidia_dynamic_lib.py`
línea 53, vía `find_sub_dirs_all_sitepackages` en
`cuda/pathfinder/_utils/find_sub_dirs.py` línea 52) y, si falla, en el buscador
del sistema (Linux: `dlopen(soname)` sin ruta; Windows: `LoadLibraryExW` con
`flags=0`) — ninguno de los dos ve un onedir/AppImage empaquetado, que no es
site-packages ni está en el PATH de CUDA del sistema.
Pero ANTES de esas dos rutas, `load_nvidia_dynamic_lib` (misma función,
línea 39) siempre comprueba primero si la lib ya está cargada en el proceso
vía `check_if_already_loaded_from_elsewhere`: en Linux con
`ctypes.CDLL(soname, mode=os.RTLD_NOLOAD)` (`load_dl_linux.py` línea 137) y en
Windows con `kernel32.GetModuleHandleW(dll_name)` (`load_dl_windows.py` línea
104) — matchean por SONAME/nombre de DLL, no por ruta. Por eso aquí se
precargan cudart y nvrtc con `ctypes.CDLL`/`ctypes.WinDLL` sobre su ruta
ABSOLUTA dentro de `_MEIPASS` (RTLD_GLOBAL en Linux) antes de que la app
importe cupy: cuda-pathfinder las encuentra ya cargadas y no llega a tocar
site-packages ni el buscador del sistema. Orden: cudart antes que nvrtc
(nvrtc puede necesitar el contexto CUDA que cudart inicializa) y nvjpeg antes
que nvimgcodec (aunque nvimgcodec no depende de esto en la práctica, ver
abajo). Los handles se guardan en `_PRELOADED_HANDLES` (global de este
módulo) para que el GC no los cierre en Windows (`FreeLibrary` implícito) — en
Linux `ctypes.CDLL` no hace `dlclose` automático al recolectarse, pero se
guardan igual por consistencia y para poder depurar.

nvimgcodec/nvjpeg: NO usan cuda-pathfinder. `libnvimgcodec.so.0` (y sus
`extensions/libnvjpeg_ext.so.*`) llevan RUNPATH `$ORIGIN`-relativo hacia
`nvidia/nvjpeg/lib` (confirmado con `readelf -d`: p.ej.
`$ORIGIN/../nvjpeg/lib` desde `nvidia/nvimgcodec/`), así que el propio loader
de Linux los resuelve solo con que `collect_all()` conserve el árbol relativo
`nvidia/<paquete>/...` dentro de `_MEIPASS` (ya lo hace). No hay variable tipo
`NVIMGCODEC_EXTENSIONS_PATH` en 0.9.0.20 (no aparece en los símbolos de
`nvimgcodec_impl*.so` ni en su `__init__.py`): el directorio `extensions/` se
localiza en runtime relativo a la propia ubicación del módulo compilado, no
por variable de entorno. La precarga de nvjpeg de abajo es un cinturón y
tirantes tolerante a fallos, no imprescindible.

Linux: los mismos paquetes llevan .so bajo nvidia/<paquete>/lib/*.so.* con el
SONAME correcto. A propósito NO se toca LD_LIBRARY_PATH aquí: es global al
proceso y podría interferir con las .so de Qt/QtWebEngine que ya trae el
AppImage (bug mucho peor que "el GPU opt-in no arranca").

CUPY_CACHE_DIR: cupy compila sus kernels (incl. los que usa `cp.rot90` en
rgb_gpu.py) con NVRTC en el primer uso y cachea el resultado en disco; si no
está definida, usa `~/.cupy/kernel_cache`, que en un onedir "portable" puede
no ser escribible según cómo lo despliegue el usuario. Si el usuario no la
fijó ya por su cuenta, se apunta a un directorio de datos propio de la app.
"""
import ctypes
import glob
import os
import sys

# Handles de las libs precargadas (cudart/nvrtc/nvjpeg) — vivos mientras dure
# el proceso, ver docstring. Nunca se cierran a mano (ni dlclose ni
# FreeLibrary): otros componentes (cupy, nvimgcodec) los siguen usando.
_PRELOADED_HANDLES = []

# (nombre_pathfinder, subpaquete nvidia-*-cu12, subdir Linux, subdir Windows,
#  patrón Linux, patrón Windows)
# Orden intencional: cudart -> nvrtc -> nvjpeg (ver docstring). En el onedir
# Windows las DLL cuelgan de "bin" (nvidia/<paquete>/bin/*.dll), no de "lib"
# (que solo tiene __init__.py) — en Linux sí es "lib" (nvidia/<paquete>/lib/*.so*).
_GPU_LIBS_TO_PRELOAD = (
    ("cudart", "cuda_runtime", "lib", "bin", "libcudart.so*", "cudart64_*.dll"),
    ("nvrtc", "cuda_nvrtc", "lib", "bin", "libnvrtc.so*", "nvrtc64_*.dll"),
    ("nvjpeg", "nvjpeg", "lib", "bin", "libnvjpeg.so*", "nvjpeg64_*.dll"),
)


def _preload_gpu_dynamic_libs(base):
    """Precarga cudart/nvrtc/nvjpeg por ruta absoluta antes de importar cupy.

    Tolerante lib a lib: si una no está (build sin GPU) o falla al cargar, se
    salta y se sigue con las demás — nunca debe impedir el arranque.
    """
    is_windows = sys.platform == "win32"
    for _pathfinder_name, _subpkg, _subdir_linux, _subdir_win, _pattern_linux, _pattern_win in _GPU_LIBS_TO_PRELOAD:
        try:
            _pattern = _pattern_win if is_windows else _pattern_linux
            _subdir = _subdir_win if is_windows else _subdir_linux
            _search_dir = os.path.join(base, "nvidia", _subpkg, _subdir)
            _matches = sorted(glob.glob(os.path.join(_search_dir, _pattern)))
            if not _matches:
                continue
            _lib_path = _matches[-1]
            if is_windows:
                _PRELOADED_HANDLES.append(ctypes.WinDLL(_lib_path))
            else:
                _PRELOADED_HANDLES.append(ctypes.CDLL(_lib_path, mode=os.RTLD_NOW | os.RTLD_GLOBAL))
        except Exception:
            continue


try:
    _base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.executable))

    _preload_gpu_dynamic_libs(_base)

    if sys.platform == "win32":
        _nvidia_root = os.path.join(_base, "nvidia")
        if os.path.isdir(_nvidia_root):
            for _dirpath, _dirnames, _filenames in os.walk(_nvidia_root):
                if any(_f.lower().endswith(".dll") for _f in _filenames):
                    try:
                        _PRELOADED_HANDLES.append(os.add_dll_directory(_dirpath))
                    except (OSError, AttributeError):
                        pass
        # Por si algún día cupy empaqueta binarios propios ahí (hoy no lo hace:
        # cupy solo lleva .pyd que cargan sus DLL nvidia-*-cu12 en runtime).
        _cupy_data = os.path.join(_base, "cupy", ".data")
        if os.path.isdir(_cupy_data):
            try:
                _PRELOADED_HANDLES.append(os.add_dll_directory(_cupy_data))
            except (OSError, AttributeError):
                pass

    if not os.environ.get("CUPY_CACHE_DIR"):
        if sys.platform == "win32":
            _cache_root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            _cache_dir = os.path.join(_cache_root, "ATOM Organizer", "cupy_cache")
        else:
            _cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "atom-organizer", "cupy")
        try:
            os.makedirs(_cache_dir, exist_ok=True)
            os.environ["CUPY_CACHE_DIR"] = _cache_dir
        except OSError:
            pass
except Exception:
    pass
