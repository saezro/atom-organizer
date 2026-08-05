"""El DJI Thermal SDK vive en UNA sola carpeta.

Hasta la 3.3.2 se empaquetaban programas_externos/M2EA/ y /M4T/, elegidas en runtime
por autodetección del modelo de dron, siendo byte a byte idénticas (mismo md5 en los
19 ficheros de ambas). La elección no elegía nada — siempre acababa lanzando el mismo
binario — y a cambio duplicaba 6,7 MB de instalador y metía una ruta variable en la
única fase que falla en campo. Estos tests impiden que la duplicación vuelva a colarse.
"""
import hashlib
import os
from pathlib import Path

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
