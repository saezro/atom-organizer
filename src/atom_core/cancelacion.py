"""Cancelación cooperativa del run en curso (botón «Cancelar» del modal).

Un único `threading.Event` de proceso (solo hay un run a la vez, ver
`AppApi._running`). El pipeline lo consulta SOLO en puntos seguros: entre
ficheros del índice, entre filas del apply y entre fases. Nunca interrumpe
una escritura a mitad: el fichero en curso termina (todas las escrituras al
destino son `.parcial` + `os.replace`) y solo entonces se mira la bandera.
El origen no se toca nunca.
"""
from __future__ import annotations

import threading

_EVENTO = threading.Event()


class RunCancelado(BaseException):
    """El usuario canceló el run. Hereda de BaseException para que ningún
    `except Exception` la trague; quien la gestiona la captura explícitamente. `hechas`/`total` los rellena quien captura
    con lo que el manifiesto dice que está completo."""

    def __init__(self, mensaje: str = "Cancelado por el usuario") -> None:
        super().__init__(mensaje)
        self.hechas: int | None = None
        self.total: int | None = None


def solicitar() -> None:
    _EVENTO.set()


def limpiar() -> None:
    _EVENTO.clear()


def cancelado() -> bool:
    return _EVENTO.is_set()


def comprobar() -> None:
    """Punto seguro: lanza `RunCancelado` si se pidió cancelar."""
    if _EVENTO.is_set():
        raise RunCancelado()
