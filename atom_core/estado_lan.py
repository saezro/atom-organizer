"""Datos de sistema para la página de estado que ve la LAN en `/`
(`atom_core/webserver.py::_servir_lan_info`, `GET /api/estado`).

No es el modo espera del estadillo (eso lo sigue llevando
`Api.estadillo_espera_estado`, y solo lo ve el kiosco): esto es "qué máquina
es esta" para quien teclea la IP/host de la Pi desde otro equipo de la LAN —
versión, ID de la Raspberry, wifi, batería y disco.

Solo stdlib, tolerante a fallos: cada lectura va en su propio try/except y
nunca debe tumbar la página. Sin subprocess con timeouts largos: todo lo de
aquí es lectura directa de `/proc`, `/sys` o `shutil.disk_usage`.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from functools import lru_cache

_RUTA_CPUINFO = "/proc/cpuinfo"
_RUTA_SERIAL_DEVICETREE = "/sys/firmware/devicetree/base/serial-number"
_RUTAS_MODEL_DEVICETREE = (
    "/proc/device-tree/model",
    "/sys/firmware/devicetree/base/model",
)


@lru_cache(maxsize=1)
def es_raspberry() -> bool:
    """True si esto es una Raspberry Pi real (kiosco), False en cualquier
    otro Linux -dev, AppImage de escritorio- o sistema operativo.

    No basta con `sys.platform.startswith("linux")`: eso confinaria tambien
    en un portatil Linux de desarrollo. Se lee el modelo de la placa desde
    el device tree, que solo existe -y solo dice "Raspberry Pi"- en la Pi.
    Cacheado: el modelo de la maquina no cambia en caliente.
    """
    if not sys.platform.startswith("linux"):
        return False
    for ruta in _RUTAS_MODEL_DEVICETREE:
        try:
            with open(ruta, "rb") as f:
                modelo = f.read().split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        except OSError:
            continue
        if "Raspberry Pi" in modelo:
            return True
    return False


def serial_raspberry() -> str:
    """Número de serie de la Raspberry, o "" si no se puede leer (no es una
    Pi -Windows/PC de desarrollo-, o el fichero no existe)."""
    try:
        with open(_RUTA_SERIAL_DEVICETREE, "rb") as f:
            serie = f.read().split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
        if serie:
            return serie
    except OSError:
        pass
    try:
        with open(_RUTA_CPUINFO, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                if linea.lower().startswith("serial"):
                    return linea.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _leer(ruta: str) -> str | None:
    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def bateria() -> dict | None:
    """`{"porcentaje": int, "estado": str}` de la primera batería del sistema
    (`/sys/class/power_supply/*`), o `None` si la máquina no tiene ninguna
    -caso normal de la Pi sin UPS, que es cuando la página debe enseñar "sin
    batería"-."""
    try:
        for ruta in sorted(glob.glob("/sys/class/power_supply/*")):
            tipo = (_leer(os.path.join(ruta, "type")) or "").strip()
            if tipo != "Battery":
                continue
            capacidad = _leer(os.path.join(ruta, "capacity"))
            if capacidad is None:
                continue
            estado = (_leer(os.path.join(ruta, "status")) or "").strip()
            return {"porcentaje": int(capacidad.strip()), "estado": estado}
    except Exception:  # noqa: BLE001 — informativo, nunca debe romper la página
        pass
    return None


def _gb(num_bytes: int) -> float:
    return round(num_bytes / (1024 ** 3), 1)


def disco_sistema() -> dict | None:
    """Espacio del disco raíz (`/`): `{"libre_gb", "total_gb"}`, o `None` si
    `shutil.disk_usage` falla."""
    try:
        uso = shutil.disk_usage("/")
        return {"libre_gb": _gb(uso.free), "total_gb": _gb(uso.total)}
    except OSError:
        return None


def discos_externos() -> list[dict]:
    """Discos externos montados ahora mismo, con su espacio libre/total.

    Mismo criterio que `app_webview._disco_externo` (dispositivo distinto al
    de la raíz del sistema: robusto ante cualquier gestor de montaje), pero
    a diferencia de aquel -que solo necesita el PRIMERO para saber si hay
    sitio donde recibir fotos- aquí se listan TODOS, y no se descartan los
    vacíos: para la página de estado un disco vacío sigue siendo "un disco
    conectado".
    """
    candidatos: set[str] = set()
    for patron in ("/media/*/*", "/media/*", "/mnt/*"):
        candidatos.update(glob.glob(patron))

    try:
        raiz_dev = os.stat("/").st_dev
    except OSError:
        return []

    discos: list[dict] = []
    for cand in sorted(candidatos):
        try:
            if not os.path.isdir(cand) or os.stat(cand).st_dev == raiz_dev:
                continue
            if not os.access(cand, os.R_OK):
                continue
            uso = shutil.disk_usage(cand)
        except OSError:
            continue
        discos.append({
            "nombre": os.path.basename(cand.rstrip("/")) or cand,
            "punto_montaje": cand,
            "libre_gb": _gb(uso.free),
            "total_gb": _gb(uso.total),
        })
    return discos
