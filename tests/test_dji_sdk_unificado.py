"""El DJI Thermal SDK vive en UNA sola carpeta.

Hasta la 3.3.2 se empaquetaban programas_externos/M2EA/ y /M4T/, elegidas en runtime
por autodetección del modelo de dron, siendo byte a byte idénticas (mismo md5 en los
19 ficheros de ambas). La elección no elegía nada — siempre acababa lanzando el mismo
binario — y a cambio ocupaba 6,8 MB extra en disco instalado y metía una ruta variable
en la única fase que falla en campo. Estos tests impiden que vuelva a colarse.

(El instalador apenas encogió al unificar —147.205.104 → 147.199.035 bytes, 6 KB—:
LZMA ya deduplicaba dos carpetas idénticas casi por completo. El ahorro está en el
disco del usuario, no en la descarga.)
"""
import hashlib
import os
from pathlib import Path

import pytest

import external_tools

REPO = Path(__file__).resolve().parents[1]
SDK_DIR = REPO / "programas_externos" / "DJI"


def test_solo_hay_una_carpeta_de_sdk():
    """Nada de carpetas por modelo de dron dentro de programas_externos/."""
    subcarpetas = sorted(p.name for p in (REPO / "programas_externos").iterdir() if p.is_dir())
    assert subcarpetas == ["DJI"], (
        f"programas_externos/ debe traer solo la carpeta DJI; encontradas: {subcarpetas}")


def test_el_sdk_trae_los_binarios_de_ambos_sistemas():
    """El mismo SDK sirve a Windows (dji_irp.exe + libdirp.dll) y Linux (libdirp.so)."""
    for nombre in ("dji_irp.exe", "libdirp.dll", "libdirp.so", "libv_list.ini"):
        assert (SDK_DIR / nombre).is_file(), f"falta {nombre} en programas_externos/DJI"


def test_la_ruta_del_conversor_apunta_a_la_carpeta_unica(monkeypatch):
    """dji_utility_path() es el ÚNICO constructor de la ruta del conversor.

    Si vuelve a haber más de un candidato posible, diagnosticar un fallo del conversor
    en la máquina de un usuario obliga otra vez a adivinar desde cuál se lanzó."""
    monkeypatch.setattr(external_tools, "app_base_dir", lambda: "/base")
    esperado = os.path.join("/base", "programas_externos", "DJI", "dji_irp.exe")
    assert external_tools.dji_utility_path() == esperado
    assert external_tools.dji_sdk_dir() == os.path.join("/base", "programas_externos", "DJI")


def test_has_dji_binaries_detecta_la_instalacion_incompleta(tmp_path, monkeypatch):
    """Sin binario del SO en la carpeta única -> False, para abortar upfront con un
    error claro en vez de fallar imagen por imagen aguas abajo."""
    monkeypatch.setattr(external_tools, "app_base_dir", lambda: str(tmp_path))
    assert external_tools.has_dji_binaries() is False

    sdk = tmp_path / "programas_externos" / "DJI"
    sdk.mkdir(parents=True)
    (sdk / external_tools.dji_bin_name()).write_bytes(b"binario")
    assert external_tools.has_dji_binaries() is True


def test_ningun_binario_del_sdk_esta_duplicado():
    """Dos ficheros con el mismo contenido dentro del SDK serían la duplicación
    volviendo por otra puerta (p. ej. una copia 'por si acaso' de dji_irp.exe)."""
    por_hash = {}
    for fichero in sorted(SDK_DIR.rglob("*")):
        if not fichero.is_file():
            continue
        digest = hashlib.md5(fichero.read_bytes()).hexdigest()
        por_hash.setdefault(digest, []).append(fichero.relative_to(SDK_DIR).as_posix())
    duplicados = {h: n for h, n in por_hash.items() if len(n) > 1}
    assert not duplicados, f"ficheros duplicados en el SDK: {duplicados}"


def _libs_declaradas(seccion: str) -> list:
    """Ficheros que libv_list.ini declara en una seccion, en orden."""
    import configparser

    ini = configparser.ConfigParser()
    ini.read(SDK_DIR / "libv_list.ini", encoding="utf-8")
    return [ini[seccion][k] for k in ini[seccion]]


@pytest.mark.parametrize("seccion", ["windows_release_dll_list", "linux_release_dll_list"])
def test_las_libs_que_declara_el_sdk_existen(seccion):
    """Cada libreria que libv_list.ini declara para una plataforma DEBE estar.

    Este test nace del bug que rompio la conversion termica en Windows entre el
    2026-07-23 y el 2026-08-05. El port a Linux (cd9b913) sobreescribio
    libv_list.ini con el de una version mas nueva del SDK, que declara
    `hirp=libv_hirp.dll`; los binarios Windows siguieron siendo los de 2022, que
    traen `libv_cirp.dll` y ningun libv_hirp.dll. libdirp.dll cargaba bien (el log
    imprimia "DIRP API version number : 0x13") pero dirp_create_from_rjpeg fallaba
    con -16 en TODAS las imagenes, en todas las maquinas Windows. En Linux no se
    noto: alli libv_hirp.so si esta.

    Un .ini que declara ficheros ausentes es exactamente eso, y aqui salta.
    """
    faltan = [n for n in _libs_declaradas(seccion) if not (SDK_DIR / n).is_file()]
    assert not faltan, (
        f"libv_list.ini [{seccion}] declara librerias que no estan en el paquete: {faltan}. "
        "El SDK fallara con -16 (create R-JPEG dirp handle failed) en esa plataforma.")


def _copias_del_dockerfile_job() -> list:
    """Patrones de origen de todas las instrucciones COPY de Dockerfile.job."""
    texto = (REPO / "Dockerfile.job").read_text(encoding="utf-8")
    texto = texto.replace("\\\n", " ")  # continuaciones de linea
    patrones = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea.upper().startswith("COPY "):
            continue
        tokens = [t for t in linea.split()[1:] if not t.startswith("--")]
        patrones.extend(tokens[:-1])  # el ultimo token es el destino
    return patrones


def test_la_imagen_del_job_se_lleva_el_sdk_linux_entero():
    """Dockerfile.job debe copiar TODO lo que libdirp necesita en runtime Linux.

    Hermano del test de arriba, y del mismo fallo por el otro lado: alli el .ini
    declaraba libs ausentes, aqui el .ini era el ausente. La v3.4.24 copiaba
    `programas_externos/DJI/*.so*`, que se deja fuera `libv_list.ini` — el indice con
    el que libdirp elige su plugin, y que busca en su propio directorio. La imagen
    arrancaba, cargaba libdirp y devolvia -15 en las 3.743 termicas de ANTOLIN: cero
    TIFF, y el Job marcado como EXITO.

    Se comprueba contra el contenido REAL de la carpeta del SDK (no una lista a mano)
    para que un fichero nuevo del SDK no se quede fuera en silencio.
    """
    import fnmatch

    patrones = _copias_del_dockerfile_job()
    requeridos = ["libv_list.ini"] + sorted(
        p.name for p in SDK_DIR.iterdir() if p.suffix == ".so" or ".so." in p.name)
    faltan = [n for n in requeridos
              if not any(fnmatch.fnmatch(f"programas_externos/DJI/{n}", pat)
                         for pat in patrones)]
    assert not faltan, (
        f"Dockerfile.job no copia {faltan} a la imagen del Cloud Run Job. "
        "Sin el SDK Linux completo, dirp_create_from_rjpeg falla en TODAS las "
        "imagenes termicas y el vuelo sale sin un solo TIFF.")
