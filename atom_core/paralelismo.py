"""Controlador adaptativo de paralelismo del organizado.

PORQUÉ EXISTE
-------------
Hoy el número de trabajadores lo fija `utils.workers_para_lote` (`utils.py:1305`)
UNA sola vez al arrancar, a partir de núcleos y RAM. Eso ignora dos cosas: el
tipo de disco (en HDD, más hilos es thrashing, no más velocidad) y la carga
del resto de la máquina mientras el organizado corre. Rodrigo reportó el
2026-09-08 que un mismo organizado tardó 10 minutos más con el PC ocupado por
otros procesos: el dimensionado estático pidió los mismos trabajadores de
siempre y se peleó por CPU con lo demás.

Este controlador NO sustituye a `workers_para_lote`: lo usa como punto de
partida (y como techo por RAM, multiplicado por dos) y luego mide throughput,
CPU ociosa y RAM libre en ventanas de tiempo para subir o bajar el número de
trabajadores en caliente. No crea ni redimensiona ningún pool: solo dice el
número. Quien lo consume aforo un semáforo con ese número (un
`ProcessPoolExecutor` no se puede redimensionar).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import utils

#: Zona muerta para no oscilar por ruido de medida: un cambio de rendimiento
#: por debajo de este umbral no mueve el número de trabajadores.
_ZONA_MUERTA_PCT = 0.05

#: Por debajo de este umbral de CPU ociosa no merece la pena subir trabajadores
#: aunque el rendimiento esté plano: la máquina ya está ocupada.
_CPU_OCIOSA_PARA_SUBIR = 40.0


@dataclass(frozen=True)
class Medicion:
    """Una ventana de trabajo ya cerrada: cuántos trabajadores había, cuánto se
    completó y en qué estado quedaron CPU y RAM al cierre."""

    trabajadores: int
    completados: int
    segundos: float
    mb_procesados: float
    cpu_ociosa_pct: float
    ram_libre_mb: float


def decidir_trabajadores(
    historial: List[Medicion],
    minimo: int,
    maximo: int,
    mb_por_worker: float = utils.MB_POR_WORKER,
) -> int:
    """Decide cuántos trabajadores debe haber a partir del historial de
    mediciones. Reglas literales, en este orden (el orden importa: la RAM
    manda sobre todo lo demás):

    1. RAM libre por debajo de `2 * mb_por_worker` en la última medición →
       bajar 1 (límite duro, no preferencia).
    2. Con menos de 2 mediciones → mantener (no hay tendencia que comparar).
    3. Rendimiento mejora >5% respecto a la ventana anterior → subir 1.
    4. Rendimiento empeora >5% → bajar 1 (esto detecta el thrashing de HDD).
    5. Dentro del ±5% (zona muerta) y CPU ociosa >40% → subir 1 (hay margen).
    6. En cualquier otro caso → mantener.
    """
    ultima = historial[-1]

    # Regla 1: la RAM es un límite duro, se comprueba antes que nada.
    if ultima.ram_libre_mb < 2 * mb_por_worker:
        return max(minimo, ultima.trabajadores - 1)

    # Regla 2: sin dos ventanas no hay tendencia que valga.
    if len(historial) < 2:
        return ultima.trabajadores

    anterior = historial[-2]
    rendimiento_anterior = anterior.completados / anterior.segundos
    rendimiento_ultimo = ultima.completados / ultima.segundos

    if rendimiento_anterior == 0:
        cambio_pct = 0.0 if rendimiento_ultimo == 0 else float("inf")
    else:
        cambio_pct = (rendimiento_ultimo - rendimiento_anterior) / rendimiento_anterior

    # Regla 3: mejora clara → subir.
    if cambio_pct > _ZONA_MUERTA_PCT:
        return min(maximo, ultima.trabajadores + 1)

    # Regla 4: empeora claro → bajar (thrashing de HDD).
    if cambio_pct < -_ZONA_MUERTA_PCT:
        return max(minimo, ultima.trabajadores - 1)

    # Regla 5: zona muerta con margen de CPU → subir.
    if ultima.cpu_ociosa_pct > _CPU_OCIOSA_PARA_SUBIR:
        return min(maximo, ultima.trabajadores + 1)

    # Regla 6: nada de lo anterior → mantener.
    return ultima.trabajadores


def _lector_recursos_psutil():
    """Lector de recursos por defecto, vía `psutil`. Import perezoso: si el
    llamante inyecta su propio lector (siempre en tests), no hace falta cargar
    psutil para nada."""
    import psutil

    cpu_ociosa_pct = 100.0 - psutil.cpu_percent(interval=None)
    ram_libre_mb = psutil.virtual_memory().available / (1024 * 1024)
    return cpu_ociosa_pct, ram_libre_mb


class ControladorAdaptativo:
    """Mide throughput/latencia/RAM en ventanas de tiempo y decide cuántos
    trabajadores debe haber en cada momento. No toca ningún pool: solo
    calcula el número; quien lo consume aforo un semáforo con él."""

    def __init__(
        self,
        minimo: int = 1,
        maximo: Optional[int] = None,
        ventana_segundos: float = 5.0,
        mb_por_worker: float = utils.MB_POR_WORKER,
        reloj: Callable[[], float] = time.monotonic,
        lector_recursos: Optional[Callable[[], "tuple[float, float]"]] = None,
    ) -> None:
        self.minimo = minimo
        self.mb_por_worker = mb_por_worker
        self.ventana_segundos = ventana_segundos
        self.reloj = reloj
        self.lector_recursos = lector_recursos or _lector_recursos_psutil

        arranque = utils.workers_para_lote(mb_por_worker)
        # El techo por RAM que da workers_para_lote es el punto de partida;
        # el máximo del controlador es ese mismo valor multiplicado por 2,
        # nunca un número inventado.
        self.maximo = maximo if maximo is not None else arranque * 2

        self._trabajadores = max(self.minimo, min(self.maximo, arranque))
        self._historial: List[Medicion] = []

        self._lock = threading.Lock()
        self._inicio_ventana = self.reloj()
        self._completados_ventana = 0
        self._mb_ventana = 0.0

    @property
    def trabajadores(self) -> int:
        return self._trabajadores

    def registrar(self, mb: float) -> None:
        """Registra un item terminado dentro de la ventana en curso. Llamado
        desde varios hilos a la vez (uno por worker del apply): se protege
        con un lock."""
        with self._lock:
            self._completados_ventana += 1
            self._mb_ventana += mb

    def revisar(self) -> int:
        """Cierra la ventana en curso si ya ha pasado `ventana_segundos` y
        recalcula `trabajadores`. Si no ha pasado el tiempo, devuelve el
        valor actual sin tocar nada: la ventana se cierra por tiempo, no por
        número de items, o un lote de imágenes enormes nunca cerraría
        ninguna ventana."""
        ahora = self.reloj()
        with self._lock:
            transcurrido = ahora - self._inicio_ventana
            if transcurrido < self.ventana_segundos:
                return self._trabajadores

            cpu_ociosa_pct, ram_libre_mb = self.lector_recursos()
            medicion = Medicion(
                trabajadores=self._trabajadores,
                completados=self._completados_ventana,
                segundos=transcurrido,
                mb_procesados=self._mb_ventana,
                cpu_ociosa_pct=cpu_ociosa_pct,
                ram_libre_mb=ram_libre_mb,
            )
            self._historial.append(medicion)

            self._trabajadores = decidir_trabajadores(
                self._historial, self.minimo, self.maximo, self.mb_por_worker
            )

            self._inicio_ventana = ahora
            self._completados_ventana = 0
            self._mb_ventana = 0.0

            return self._trabajadores
