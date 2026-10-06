"""Lock de destino: dos organizados al MISMO destino no deben correr a la vez.

Lock del sistema operativo sobre `<destino>/.organizado/run.lock`, con el
descriptor abierto durante TODO el run (Windows `msvcrt.locking`, POSIX
`fcntl.flock`). Lo libera el SO si el proceso muere, así que no hay locks
huérfanos ni hace falta decidir por pid. El fichero guarda pid/host/hora solo
como información para el mensaje de rechazo, nunca para decidir. Nunca se
borra el fichero de lock: borrarlo abriría una carrera entre procesos.

Disposición del fichero: en Windows se bloquea un byte lejano (offset
`_OFFSET_BLOQUEO`, mucho más allá del contenido; el bloqueo es obligatorio y
impediría escribir/leer esa región) y el JSON informativo va desde el byte 0.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import socket

from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO

NOMBRE_LOCK = "run.lock"
_OFFSET_BLOQUEO = 1 << 30

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


class DestinoOcupado(RuntimeError):
    """Otro organizado está usando el destino (o no se puede bloquear)."""


class LockDestino:
    def __init__(self, fd: int, ruta: str) -> None:
        self._fd = fd
        self.ruta = ruta

    def liberar(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                os.lseek(fd, _OFFSET_BLOQUEO, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def _ruta_lock(carpeta: str) -> str:
    return os.path.join(carpeta, NOMBRE_CARPETA_MANIFIESTO, NOMBRE_LOCK)


def _quien_lo_tiene(ruta: str) -> str:
    try:
        with open(ruta, "rb") as fh:
            d = json.loads(fh.read().rstrip(b"\0").decode("utf-8"))
        return f"pid {d['pid']}, equipo {d['host']}, desde {d['hora']}"
    except (OSError, ValueError, KeyError, TypeError):
        return "propietario desconocido"


def _bloquear(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        os.lseek(fd, _OFFSET_BLOQUEO, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def adquirir(carpeta: str) -> LockDestino:
    """Toma el lock y devuelve el manejador; `DestinoOcupado` si no se puede."""
    ruta = _ruta_lock(carpeta)
    try:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        fd = os.open(ruta, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    except OSError as exc:
        raise DestinoOcupado(
            f"No se puede crear el bloqueo del destino ({ruta}): {exc}. "
            "Comprueba que la carpeta de salida es escribible.") from exc
    try:
        _bloquear(fd)
    except OSError as exc:
        os.close(fd)
        raise DestinoOcupado(
            "Otro organizado está usando este destino "
            f"({_quien_lo_tiene(ruta)}; bloqueo: {ruta}). "
            "Espera a que termine y vuelve a lanzar este.") from exc
    try:  # información para el mensaje; si falla no importa
        info = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                           "hora": datetime.datetime.now().isoformat(timespec="seconds")})
        datos = info.encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, datos)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "No se pudo escribir el propietario en %s: %s", ruta, exc)
    return LockDestino(fd, ruta)


def liberar(lock: LockDestino | None) -> None:
    if lock is not None:
        lock.liberar()
