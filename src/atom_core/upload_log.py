"""Log persistente de las subidas al bucket.

Existe porque el único rastro que dejaba una subida era el evento `atom:cloud`
hacia React: texto en memoria que se evapora al cerrar la app. Cuando un vuelo
de 8,7 GB tarda de más, o se corta a mitad, o el usuario cierra la ventana, no
quedaba **nada** que mirar — ni cuántos reintentos hubo, ni si la red se cayó,
ni por qué un fichero acabó en `failed`.

Va a un fichero **propio** (`subidas.log`), no al `app_*.log` del pipeline:

  - Una subida y una corrida del organizador son cosas distintas y se
    diagnostican por separado; mezclarlas obliga a filtrar miles de líneas del
    SDK para encontrar un corte de red.
  - `app_*.log` nace con timestamp en el nombre, uno por arranque. Para
    reconstruir qué pasó en una subida que sobrevivió a tres reinicios harían
    falta tres ficheros. `subidas.log` es uno solo y rota por tamaño.
  - Es el fichero que se le pide al usuario cuando reporta «va lento»: uno,
    con nombre evidente, en la carpeta de logs de siempre.

El logger se configura una única vez por proceso y es thread-safe (los 16
hilos de subida escriben en él): `logging` serializa con un lock interno por
handler, y aquí no se sustituyen handlers en caliente, así que no hace falta
el QueueListener que sí necesita `OrganizerLogger`.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "atom.subidas"
FILENAME = "subidas.log"

# 10 MB × 5 ficheros. Una subida entera con detalle por fichero son ~300 KB,
# así que esto guarda el histórico de bastantes vuelos sin vigilancia.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False
# La configuración se hace una vez, pero nada impide que dos hilos la pidan a
# la vez: sin el lock, ambos verían `_configured is False` y el fichero
# acabaría con dos handlers, o sea con cada línea escrita por duplicado.
_lock = threading.Lock()


def _log_dir() -> Path:
    """Carpeta de logs del usuario, con el mismo criterio que el resto de la app.

    Se importa `external_tools` de forma perezosa y tolerante: este módulo lo
    usan los tests de `cloud_upload` sin arrastrar el resto de la aplicación.
    """
    try:
        from external_tools import user_log_dir
        return Path(user_log_dir())
    except Exception:  # noqa: BLE001 - fallback deliberado, no puede tumbar la subida
        if sys.platform.startswith("win"):
            base = Path(os.environ.get("APPDATA", Path.home())) / "ATOM-Organizer"
        else:
            base = Path.home() / ".config" / "atom-organizer"
        return base / "Logs"


def get_logger(log_dir: str | os.PathLike | None = None) -> logging.Logger:
    """El logger de subidas, ya configurado. Idempotente.

    Si el fichero no se puede abrir (disco lleno, permisos) se devuelve el
    logger igualmente y sin handler de fichero: **un problema para escribir el
    log nunca puede impedir la subida**, que es el trabajo de verdad.
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    with _lock:
        if _configured:
            return logger
        _configurar(logger, log_dir)
        _configured = True
    return logger


def _configurar(logger: logging.Logger,
                log_dir: str | os.PathLike | None) -> None:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    directorio = Path(log_dir) if log_dir is not None else _log_dir()
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directorio / FILENAME,
            mode="a",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "{asctime} {levelname:<7} [{threadName}] {message}",
            style="{",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    except OSError:
        pass


def ruta() -> Path:
    """Dónde está el fichero, para poder enseñárselo al usuario."""
    return _log_dir() / FILENAME


def _reset_para_tests() -> None:
    """Deshace la configuración. Solo para tests."""
    global _configured
    with _lock:
        logger = logging.getLogger(LOGGER_NAME)
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
        _configured = False
