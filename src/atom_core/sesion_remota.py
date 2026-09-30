"""Detección de sesión remota activa (benchmark/proceso por SSH usando esta máquina).

Cuando un proceso remoto está usando la máquina, la app debe poder detectarlo
y avisar al usuario para que no se solapen dos trabajos. El mecanismo es un
lock en disco: `sesion_remota.json`, junto a `Config.ini`
(`external_tools._user_config_path()`).

`marcar()` es el lado que ESCRIBE el lock (lo usa el proceso remoto): un
context manager que lo crea al entrar y lo borra al salir, refrescando su
mtime periódicamente con un hilo daemon (latido) para que siga vivo mientras
el bloque se ejecuta. `activa()` es el lado que LEE: la app de escritorio lo
consulta para saber si hay una sesión remota en curso.

Un lock cuyo mtime es más antiguo que `CADUCIDAD_S` se considera muerto (el
proceso remoto murió sin limpiar): así nunca bloquea la app para siempre.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import tempfile
import threading
from contextlib import contextmanager

from external_tools import _user_config_path

__all__ = ["CADUCIDAD_S", "LATIDO_S", "ruta_lock", "activa", "marcar"]

# Tiempo tras el cual, sin refresco, se considera muerta una sesión remota.
CADUCIDAD_S = 90

# Periodo del latido que refresca el mtime del lock mientras está activo.
LATIDO_S = 30


def ruta_lock() -> str:
    """Ruta del JSON del lock, en el mismo directorio que `Config.ini`."""
    return os.path.join(os.path.dirname(_user_config_path()), "sesion_remota.json")


def activa() -> dict | None:
    """Devuelve el dict de la sesión remota activa, o None si no hay ninguna.

    Totalmente tolerante a fallo: fichero ausente, JSON corrupto, permisos o
    campos que faltan → None, nunca lanza."""
    ruta = ruta_lock()
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        return None
    if (dt.datetime.now().timestamp() - mtime) > CADUCIDAD_S:
        return None
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return None
    if not isinstance(datos, dict):
        return None
    if not {"motivo", "host", "pid", "desde"}.issubset(datos.keys()):
        return None
    return datos


def _escribir_lock(ruta: str, datos: dict) -> None:
    """Escritura atómica del lock: fichero temporal en el mismo dir + `os.replace`."""
    base = os.path.dirname(ruta)
    os.makedirs(base, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="sesion_remota.", suffix=".json.tmp", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        os.replace(tmp_path, ruta)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@contextmanager
def marcar(motivo: str):
    """Context manager: marca esta máquina como ocupada por una sesión remota.

    Al entrar escribe el lock y arranca un hilo daemon que refresca su mtime
    cada `LATIDO_S` segundos (para que `activa()` no lo dé por caducado
    mientras el bloque sigue en marcha). Al salir —incluso si el bloque lanza
    una excepción— para el hilo y borra el fichero; el borrado nunca lanza.
    """
    ruta = ruta_lock()
    datos = {
        "motivo": motivo,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "desde": dt.datetime.now().isoformat(),
    }
    _escribir_lock(ruta, datos)

    parar = threading.Event()

    def _latido():
        while not parar.wait(LATIDO_S):
            try:
                os.utime(ruta, None)
            except OSError:
                pass

    hilo = threading.Thread(target=_latido, daemon=True)
    hilo.start()

    try:
        yield
    finally:
        parar.set()
        hilo.join(timeout=LATIDO_S)
        try:
            os.remove(ruta)
        except OSError:
            pass
