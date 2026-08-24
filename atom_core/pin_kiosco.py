"""PIN local del kiosco de la Raspberry Pi.

Es un secreto del DISPOSITIVO, no de la persona: la Pi vive en una sala
compartida y esto evita que cualquiera que pase opere con la credencial
emparejada. Se guarda derivado con scrypt en la tabla `meta` de
`session.db`, que ya tiene permisos 0600.

El hash nunca sale de aqui: la verificacion ocurre en Python y el
frontend solo recibe si o no.
"""

from __future__ import annotations

import base64
import hmac
import math
import os
import re
import time

from hashlib import scrypt

CLAVE_META = "pin_kiosco"
LONGITUD = 4

# Parametros scrypt: interactivos. La Pi es un ARM modesto y esto se
# ejecuta una vez por desbloqueo, no en un bucle.
N = 2 ** 14
R = 8
P = 1
SAL_BYTES = 16

_FORMATO = re.compile(r"^\d{4}$")


class PinInvalido(ValueError):
    """El PIN no tiene el formato exigido (4 digitos)."""


def _validar(pin) -> str:
    if not isinstance(pin, str) or not _FORMATO.match(pin):
        raise PinInvalido(f"El PIN son {LONGITUD} digitos.")
    return pin


def _derivar(pin: str, sal: bytes) -> bytes:
    return scrypt(pin.encode("utf-8"), salt=sal, n=N, r=R, p=P, dklen=32)


def _serializar(sal: bytes, hash_: bytes) -> str:
    return "scrypt${}${}${}${}${}".format(
        N, R, P,
        base64.b64encode(sal).decode("ascii"),
        base64.b64encode(hash_).decode("ascii"),
    )


def _deserializar(guardado: str):
    """Devuelve (n, r, p, sal, hash) o None si el valor no es utilizable."""
    try:
        etiqueta, n, r, p, sal_b64, hash_b64 = guardado.split("$")
        if etiqueta != "scrypt":
            return None
        return (
            int(n), int(r), int(p),
            base64.b64decode(sal_b64),
            base64.b64decode(hash_b64),
        )
    except Exception:  # noqa: BLE001 - un meta corrupto no tumba el kiosco
        return None


def hay_pin(store) -> bool:
    guardado = store.meta_get(CLAVE_META)
    return bool(guardado) and _deserializar(guardado) is not None


def fijar(store, pin) -> None:
    pin = _validar(pin)
    sal = os.urandom(SAL_BYTES)
    store.meta_set(CLAVE_META, _serializar(sal, _derivar(pin, sal)))


def verificar(store, pin) -> bool:
    guardado = store.meta_get(CLAVE_META)
    if not guardado:
        return False
    partes = _deserializar(guardado)
    if partes is None:
        return False
    n, r, p, sal, esperado = partes
    if not isinstance(pin, str) or not _FORMATO.match(pin):
        return False
    calculado = scrypt(pin.encode("utf-8"), salt=sal, n=n, r=r, p=p, dklen=len(esperado))
    return hmac.compare_digest(calculado, esperado)


def cambiar(store, actual, nuevo) -> bool:
    if not verificar(store, actual):
        return False
    fijar(store, nuevo)
    return True


def borrar(store) -> None:
    store.meta_set(CLAVE_META, None)


FALLOS_PARA_BLOQUEAR = 5
ESPERA_INICIAL = 30
ESPERA_MAXIMA = 600


class ControlIntentos:
    """Espera escalada tras varios PINs fallidos seguidos.

    Vive en memoria del proceso: no se persiste a proposito. Reiniciar el
    servidor de la Pi exige acceso al sistema operativo, que ya es un
    compromiso mayor que adivinar el PIN.
    """

    def __init__(self, reloj=time.monotonic) -> None:
        self._reloj = reloj
        self._fallos = 0
        self._tandas = 0
        self._hasta = 0.0

    def bloqueado(self) -> bool:
        return self.espera_segundos() > 0

    def espera_segundos(self) -> int:
        restante = self._hasta - self._reloj()
        return math.ceil(restante) if restante > 0 else 0

    def fallo(self) -> None:
        if self.bloqueado():
            return
        self._fallos += 1
        if self._fallos >= FALLOS_PARA_BLOQUEAR:
            self._fallos = 0
            espera = min(ESPERA_INICIAL * (2 ** self._tandas), ESPERA_MAXIMA)
            self._tandas += 1
            self._hasta = self._reloj() + espera

    def acierto(self) -> None:
        self._fallos = 0
        self._tandas = 0
        self._hasta = 0.0
