"""Estado de la credencial del dispositivo en la Raspberry Pi.

La Pi no tiene sesión de usuario: su identidad es el `device_token` del
pairing por QR, que no caduca. Lo único que la mata es que la Suite lo dé por
revocado (Google devolvió `invalid_grant`) o que nunca se emparejara.

Este módulo es deliberadamente puro: no hace red ni lanza hilos. Solo
clasifica el resultado de una comprobación y recuerda cuándo se hizo, para
que quien sí hace red (`app_webview.Api`) tenga un sitio único donde
preguntar "¿puedo trabajar?".
"""

from __future__ import annotations

import time

ESTADO_OK = "ok"
ESTADO_SIN_CREDENCIAL = "sin-credencial"
ESTADO_SIN_CONEXION = "sin-conexion"

# La Pi normalmente está apagada; encendida días seguidos es la excepción.
# Por eso no hay polling: se comprueba al arrancar, antes de cada acción, y
# como mucho una vez cada 6 h.
LATIDO_SEGUNDOS = 21600


def clasificar(valida: bool, mensaje: str = "", *, hubo_red: bool) -> str:
    """Traduce el resultado de una comprobación a uno de los tres estados.

    `hubo_red` es la distinción que importa: sin haber hablado con el backend
    no se puede afirmar que la credencial esté mal, y mandar al operario a
    re-emparejar por un corte de red es peor que no avisar.
    """
    if valida:
        return ESTADO_OK
    if not hubo_red:
        return ESTADO_SIN_CONEXION
    return ESTADO_SIN_CREDENCIAL


class EstadoCredencial:
    """Caché del último estado conocido, con su momento."""

    def __init__(self, *, latido: float = LATIDO_SEGUNDOS, reloj=time.time) -> None:
        self._latido = float(latido)
        self._reloj = reloj
        self._estado = ESTADO_SIN_CREDENCIAL
        self._mensaje = ""
        self._comprobado_en: float | None = None

    def registrar(self, estado: str, mensaje: str = "") -> None:
        self._estado = estado
        self._mensaje = mensaje
        self._comprobado_en = float(self._reloj())

    def invalidar(self, mensaje: str = "") -> None:
        """Un 401 en una llamada real: se sabe ya, sin esperar al latido."""
        self._estado = ESTADO_SIN_CREDENCIAL
        self._mensaje = mensaje
        self._comprobado_en = None

    def actual(self) -> dict:
        return {
            "estado": self._estado,
            "mensaje": self._mensaje,
            "comprobado_en": self._comprobado_en,
        }

    def necesita_comprobar(self) -> bool:
        if self._comprobado_en is None:
            return True
        return (float(self._reloj()) - self._comprobado_en) > self._latido
