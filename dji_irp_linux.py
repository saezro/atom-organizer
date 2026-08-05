"""
Conversor térmico DJI para Linux — equivalente al ejecutable `dji_irp` de Windows.

El DJI Thermal SDK NO distribuye el ejecutable `dji_irp` para Linux, pero sí la
librería nativa `libdirp.so` (la misma que el .exe usa por dentro). Este script
replica, vía ctypes, exactamente lo que hace el CLI:

    dji_irp -s IMG -a measure --humidity H --emissivity E --measurefmt float32 -o RAW

y escribe el mismo buffer plano de float32 (°C, row-major, tamaño = resolución del
sensor térmico) a RAW, para que el pipeline de ATOM no cambie.

Se ejecuta como PROCESO EFÍMERO por imagen (igual que dji_irp.exe en Windows) por
dos motivos: (1) libdirp/libgomp segfaultean en el teardown del intérprete al
descargar la librería —benigno, los datos ya están escritos, pero ensucia el
proceso—; aislándolo en un subproceso que termina con os._exit(0) el crash nunca
llega a ocurrir y el proceso padre queda intacto. (2) procesos aislados eliminan
cualquier duda de thread-safety de la librería nativa y permiten paralelizar.

Uso:
    python dji_irp_linux.py <img_rjpeg> <raw_out> <humidity> <emissivity> <lib_dir>
Salida: 0 = OK (raw escrito); != 0 = error (mensaje en stderr).
"""
import ctypes
import os
import sys

_DIRP_SUCCESS = 0
# Defaults del CLI dji_irp para los parámetros que ATOM no pasa (solo humidity y
# emissivity); igualan a los documentados por el SDK.
_DEFAULT_DISTANCE = 5.0
_DEFAULT_REFLECTION = 23.0


class _resolution_t(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32)]


class _measurement_params_t(ctypes.Structure):
    # Orden de campos del SDK: distance, humidity, emissivity, reflection y un
    # QUINTO float que la cabecera pública no documenta (temperatura ambiente:
    # medido, devuelve 18,9-23,8 C en las térmicas de un vuelo real).
    #
    # Declararla con 4 campos (16 B) era un BUFFER OVERFLOW: dirp_get_measurement_params
    # escribe 20 B y pisaba 4 B del heap de Python en CADA térmica. Con proceso
    # efímero por imagen el daño casi nunca llegaba a manifestarse -- de ahí el
    # "segfault benigno en el teardown" que se atribuía a libdirp/libgomp. Encadenando
    # imágenes en un mismo proceso, revienta a la 6a de forma reproducible.
    # `_cola` deja holgura para que un SDK futuro con más campos no vuelva a pisar
    # nada; el SDK actual no escribe más allá del quinto (verificado).
    _fields_ = [
        ("distance", ctypes.c_float),
        ("humidity", ctypes.c_float),
        ("emissivity", ctypes.c_float),
        ("reflection", ctypes.c_float),
        ("ambient", ctypes.c_float),
        ("_cola", ctypes.c_ubyte * 64),
    ]


def _load(lib_dir):
    """Carga libdirp.so y sus plugins. libdirp localiza los libv_*.so y
    libv_list.ini en su propio directorio vía dladdr; precargamos los plugins
    con RTLD_GLOBAL como respaldo (sus SONAME coinciden con el basename)."""
    libdirp = os.path.join(lib_dir, "libdirp.so")
    if not os.path.exists(libdirp):
        raise FileNotFoundError("No se encuentra libdirp.so en " + lib_dir)
    for dep in ("libexif.so.12", "libMicroIA_Release_x64.so",
                "libMicroJPEG_Release_x64.so", "libMicroTA_Release_x64.so",
                "libv_hirp.so", "libv_girp.so", "libv_iirp.so", "libv_dirp.so"):
        p = os.path.join(lib_dir, dep)
        if os.path.exists(p):
            try:
                ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass
    dll = ctypes.CDLL(libdirp)
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
    return dll


def measure(img_path, raw_out, humidity, emissivity, lib_dir):
    dll = _load(lib_dir)
    with open(img_path, "rb") as fh:
        raw = fh.read()
    buf = (ctypes.c_uint8 * len(raw)).from_buffer_copy(raw)

    # Contexto que se adjunta a cualquier fallo del SDK: el nombre de la API, su
    # código de retorno REAL (no una conjetura), y el tamaño del rjpeg leído. Un rc
    # de error con parámetros en rango casi siempre significa un rjpeg corrupto o a
    # medio escribir (p. ej. otra sesión/proceso modificando el MISMO fichero a la
    # vez), NO un problema de humidity/emissivity.
    _ctx = "img={0} bytes={1} lib={2}".format(
        os.path.basename(img_path), len(raw), os.path.basename(lib_dir.rstrip("/")))

    handle = ctypes.c_void_p()
    ret = dll.dirp_create_from_rjpeg(buf, len(raw), ctypes.byref(handle))
    if ret != _DIRP_SUCCESS:
        raise RuntimeError("dirp_create_from_rjpeg rc={0} [{1}]".format(ret, _ctx))
    try:
        res = _resolution_t()
        ret = dll.dirp_get_rjpeg_resolution(handle, ctypes.byref(res))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("dirp_get_rjpeg_resolution rc={0} [{1}]".format(ret, _ctx))
        n = int(res.width) * int(res.height)

        params = _measurement_params_t()
        ret = dll.dirp_get_measurement_params(handle, ctypes.byref(params))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("dirp_get_measurement_params rc={0} [{1}]".format(ret, _ctx))
        # Igual que el CLI: solo se fijan humidity y emissivity; distance y
        # reflection quedan en los defaults del CLI.
        params.distance = _DEFAULT_DISTANCE
        params.humidity = float(humidity)
        params.emissivity = float(emissivity)
        params.reflection = _DEFAULT_REFLECTION
        ret = dll.dirp_set_measurement_params(handle, ctypes.byref(params))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError(
                "dirp_set_measurement_params rc={0} (params dist={1} hum={2} emis={3} refl={4}; "
                "en rango -> sospechar rjpeg corrupto o modificado por otro proceso) [{5}]".format(
                    ret, params.distance, params.humidity, params.emissivity,
                    params.reflection, _ctx))

        data = (ctypes.c_float * n)()
        ret = dll.dirp_measure_ex(handle, data, n * ctypes.sizeof(ctypes.c_float))
        if ret != _DIRP_SUCCESS:
            raise RuntimeError("dirp_measure_ex rc={0} [{1}]".format(ret, _ctx))
    finally:
        dll.dirp_destroy(handle)

    # Buffer plano float32 sin cabecera, idéntico al .raw del .exe. Escribimos a
    # temporal + rename atómico para que el pipeline nunca lea un .raw a medias.
    tmp = raw_out + ".part"
    with open(tmp, "wb") as out:
        out.write(bytes(data))
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, raw_out)


def _suppress_core_dumps():
    """libdirp/sus hilos nativos segfaultean SIEMPRE en el teardown del proceso
    (benigno: el .raw ya está escrito y sincronizado antes). Evitamos que el kernel
    genere core dumps / los capture systemd-coredump en cada imagen."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def main(argv):
    _suppress_core_dumps()
    if len(argv) != 6:
        sys.stderr.write("uso: dji_irp_linux.py <img> <raw_out> <humidity> <emissivity> <lib_dir>\n")
        return 2
    _, img, raw_out, humidity, emissivity, lib_dir = argv
    try:
        measure(img, raw_out, float(humidity), float(emissivity), lib_dir)
    except Exception as e:  # noqa: BLE001 — el padre solo necesita rc != 0 + stderr
        sys.stderr.write("{0}: {1}\n".format(type(e).__name__, e))
        sys.stderr.flush()
        os._exit(1)
    # os._exit evita el teardown del intérprete, donde libdirp/libgomp segfaultean
    # al descargarse (el .raw ya está escrito y sincronizado en disco).
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
