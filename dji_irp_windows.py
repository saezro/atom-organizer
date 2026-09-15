"""
Conversor térmico DJI para Windows vía `libdirp.dll` EN PROCESO (ctypes), en
vez del ejecutable `dji_irp.exe` por imagen.

Medido en el banco de la oficina (KL23, 1988 térmicas): 129 s con la DLL cargada
una vez y usada desde 16 hilos a la vez, frente a 267 s lanzando `dji_irp.exe`
por imagen; el .raw (float32 plano, °C, row-major) sale byte a byte idéntico
al del .exe (paridad 300/300 verificada). El ahorro es el arranque del proceso
del SDK, no el cómputo: por eso aquí la DLL se carga UNA SOLA VEZ por proceso
y se reutiliza entre imágenes y entre hilos.

`libdirp.dll` localiza `libv_list.ini` por nombre relativo, es decir contra el
CWD, así que el CWD hay que ponerlo en la carpeta del SDK SOLO durante la
carga (`ctypes.CDLL`) y restaurarlo justo después; verificado que el CWD queda
intacto para el resto del proceso. `os.add_dll_directory` es el mecanismo
moderno para que la propia DLL encuentre sus dependencias (libv_*.dll) sin
tocar el PATH del proceso.

Si la carga falla (DLL ausente, arquitectura equivocada, dependencia rota) se
marca el módulo como ROTO y no se reintenta: `enabled()` devolverá False de
ahí en adelante y `pipeline.py` sigue usando `dji_irp.exe` como hasta ahora.

Uso desde `pipeline.py`:
    if dji_irp_windows.enabled():
        try:
            dji_irp_windows.measure(img, raw_out, humidity, emissivity, sdk_dir)
        except Exception:
            ...  # fallback a dji_irp.exe, igual que aquí no hay .raw válido

IMPORTANTE — no fijar distance/reflection: `dji_irp.exe` tampoco los pasa
(solo `--humidity`/`--emissivity`), así que el resto de parámetros son los que
ya trae el rjpeg vía `dirp_get_measurement_params`. Fijarlos rompería la
paridad byte a byte con el .exe.
"""
import ctypes
import os
import sys
import threading

_DIRP_SUCCESS = 0

# Carga perezosa, UNA vez por proceso: `_dll` cacheado y `_lock` para que dos
# hilos que lleguen a la vez a la primera imagen no carguen la DLL dos veces.
# La MEDIDA en sí (measure()) no usa este lock: es segura desde varios hilos a
# la vez (verificado con 16 hilos concurrentes en el banco).
_lock = threading.Lock()
_dll = None
_roto = False
# Handle de os.add_dll_directory: se guarda para que el directorio siga
# registrado toda la vida del proceso (close() lo quitaría).
_dll_dir = None


class _resolution_t(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32)]


class _measurement_params_t(ctypes.Structure):
    # Mismo layout que `dji_irp_linux._measurement_params_t` (misma DLL, misma
    # ABI): distance, humidity, emissivity, reflection y un quinto float sin
    # documentar (ambient). `_cola` deja holgura para que un SDK futuro con más
    # campos no pise memoria de Python; ver el comentario largo en
    # `dji_irp_linux.py` para el porqué (buffer overflow ya sufrido allí).
    _fields_ = [
        ("distance", ctypes.c_float),
        ("humidity", ctypes.c_float),
        ("emissivity", ctypes.c_float),
        ("reflection", ctypes.c_float),
        ("ambient", ctypes.c_float),
        ("_cola", ctypes.c_ubyte * 64),
    ]


def enabled() -> bool:
    """True si esta ejecución debe intentar la DLL en vez de `dji_irp.exe`.

    Desactivable con `ATOM_DJI_DLL=0` (vuelve al .exe de siempre). Si una carga
    previa de la DLL falló, `_roto` queda fijo a True y esto ya no vuelve a
    intentarlo en el resto del proceso."""
    if _roto:
        return False
    if not sys.platform.startswith("win"):
        return False
    return os.environ.get("ATOM_DJI_DLL", "1") != "0"


def _load(sdk_dir: str):
    """Carga `libdirp.dll` desde `sdk_dir` (perezosa, una vez por proceso).

    El CWD solo se cambia durante `ctypes.CDLL` (la DLL lee `libv_list.ini`
    relativo al CWD) y se restaura en el `finally`, tanto si la carga tiene
    éxito como si no. Si la carga lanza, marca el módulo como roto (no se
    reintenta) y relanza la excepción tal cual, para que quien llame vea el
    motivo real (DLL ausente, arquitectura equivocada, dependencia rota)."""
    global _dll, _roto, _dll_dir
    if _dll is not None:
        return _dll
    with _lock:
        if _dll is not None:
            return _dll
        try:
            _dll_dir = os.add_dll_directory(sdk_dir)
        except (AttributeError, OSError):
            # AttributeError: Python < 3.8, o plataforma sin este API (tests en
            # Linux). OSError: el directorio ya está registrado o no existe;
            # el chdir de abajo es el mecanismo que de verdad hace falta para
            # libv_list.ini, así que no es fatal.
            pass
        prev_cwd = os.getcwd()
        try:
            os.chdir(sdk_dir)
            dll = ctypes.CDLL(os.path.join(sdk_dir, "libdirp.dll"))
        except Exception:
            _roto = True
            raise
        finally:
            os.chdir(prev_cwd)

        H = ctypes.c_void_p
        dll.dirp_create_from_rjpeg.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_int32, ctypes.POINTER(H)]
        dll.dirp_create_from_rjpeg.restype = ctypes.c_int32
        dll.dirp_destroy.argtypes = [H]
        dll.dirp_destroy.restype = ctypes.c_int32
        dll.dirp_get_rjpeg_resolution.argtypes = [H, ctypes.POINTER(_resolution_t)]
        dll.dirp_get_rjpeg_resolution.restype = ctypes.c_int32
        dll.dirp_get_measurement_params.argtypes = [H, ctypes.POINTER(_measurement_params_t)]
        dll.dirp_get_measurement_params.restype = ctypes.c_int32
        dll.dirp_set_measurement_params.argtypes = [H, ctypes.POINTER(_measurement_params_t)]
        dll.dirp_set_measurement_params.restype = ctypes.c_int32
        dll.dirp_measure_ex.argtypes = [H, ctypes.POINTER(ctypes.c_float), ctypes.c_int32]
        dll.dirp_measure_ex.restype = ctypes.c_int32
        _dll = dll
        return _dll


def measure(image_path: str, raw_out: str, humidity: float, emissivity: float, sdk_dir: str):
    """Equivalente a `dji_irp -s IMG -a measure --humidity H --emissivity E
    --measurefmt float32 -o RAW`, vía `libdirp.dll` en proceso. Escribe en
    `raw_out` el mismo buffer plano float32 que el .exe (verificado byte a
    byte). Segura para llamarse desde varios hilos a la vez.

    Cualquier rc != 0 del SDK lanza `RuntimeError` con la fase (`create`,
    `resolution`, `get_params`, `set_params`, `measure`) y el rc con signo
    (`restype = c_int32`, ctypes ya lo devuelve con signo: no hace falta
    convertirlo a mano como sí exige el returncode sin signo del .exe)."""
    dll = _load(sdk_dir)
    with open(image_path, "rb") as fh:
        raw = fh.read()
    buf = (ctypes.c_uint8 * len(raw)).from_buffer_copy(raw)

    _ctx = "img={0} bytes={1}".format(os.path.basename(image_path), len(raw))

    handle = ctypes.c_void_p()
    ret = dll.dirp_create_from_rjpeg(buf, len(raw), ctypes.byref(handle))
    if ret != _DIRP_SUCCESS:
        raise RuntimeError("create rc={0} [{1}]".format(ret, _ctx))
    try:
        res = _resolution_t()
        ret = dll.dirp_get_rjpeg_resolution(handle, ctypes.byref(res))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("resolution rc={0} [{1}]".format(ret, _ctx))
        n = int(res.width) * int(res.height)

        params = _measurement_params_t()
        ret = dll.dirp_get_measurement_params(handle, ctypes.byref(params))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("get_params rc={0} [{1}]".format(ret, _ctx))
        # Igual que el .exe: solo se fijan humidity y emissivity; distance y
        # reflection quedan en lo que ya traía el rjpeg (no pasarlos, como
        # tampoco los pasa `dji_irp.exe`).
        params.humidity = float(humidity)
        params.emissivity = float(emissivity)
        ret = dll.dirp_set_measurement_params(handle, ctypes.byref(params))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("set_params rc={0} [{1}]".format(ret, _ctx))

        data = (ctypes.c_float * n)()
        ret = dll.dirp_measure_ex(handle, data, n * ctypes.sizeof(ctypes.c_float))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("measure rc={0} [{1}]".format(ret, _ctx))
    finally:
        dll.dirp_destroy(handle)

    # Buffer plano float32 sin cabecera, idéntico al .raw del .exe. Escritura
    # atómica (.part + rename) para que el pipeline nunca lea un .raw a medias.
    tmp = raw_out + ".part"
    with open(tmp, "wb") as out:
        out.write(bytes(data))
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, raw_out)
