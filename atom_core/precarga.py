"""Precarga determinista de las librerías pesadas con C-extensions (pandas).

PORQUÉ EXISTE ESTE MÓDULO
-------------------------
El fallo que persigue es el que rompió `Organizar completo` en las v3.4.75 y
v3.4.76 del .exe de Windows:

    AttributeError: partially initialized module 'pandas' has no attribute
    '_pandas_datetime_CAPI' (most likely due to a circular import)

Ese mensaje NO habla del sitio donde está el problema. Sale siempre que un
`import pandas` deja el módulo a medias en `sys.modules` y alguien lo vuelve a
importar después. Puede quedar a medias por dos motivos distintos:

  1. Una C-extension que el empaquetado no trajo (lo que se atacó en la
     v3.4.76 con `collect_all('pandas')` en el .spec). El error REAL —un
     `ImportError: DLL load failed`— se pierde: lo tapa el import siguiente.
  2. Dos hilos disparando el PRIMER import de pandas a la vez. En la app pasa:
     el hilo de `_run_task_worker` importa `atom_core.organize` (→ pipeline →
     pandas) mientras el hilo de `estadillos_detectar_start` importa
     `atom_core.estadillo` (→ pandas). CPython serializa por módulo, pero al
     detectar una posible espera cruzada devuelve el módulo A MEDIAS en vez de
     bloquear, que es exactamente este AttributeError.

La precarga ataca las dos a la vez, y por eso se hace en el hilo principal
NADA MÁS ARRANCAR, antes de que exista ningún hilo:

  - Elimina el caso 2 por construcción: cuando los hilos corren, pandas ya
    está entero en `sys.modules` y ninguno dispara un primer import.
  - Desenmascara el caso 1: si al empaquetado le falta una DLL, el fallo
    ocurre al arrancar, en un import limpio y sin nadie que lo tape, así que
    el traceback que queda en el log es el de verdad (`DLL load failed while
    importing ...`) y no el `_pandas_datetime_CAPI` que despista.

El `Lock` es el cinturón además de los tirantes: si alguna ruta de código
llega antes que la precarga (tests, modo servidor, un entry point futuro), los
imports diferidos siguen serializados entre sí.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Un único candado para TODAS las precargas: lo que se serializa es "el primer
# import pesado", no un módulo concreto.
_candado = threading.Lock()
_pandas_listo = False


def precargar_pandas() -> None:
    """Importa pandas una sola vez, de forma serializada. Idempotente.

    Deja escapar la excepción a propósito: quien la llama decide si es fatal
    (el arranque solo la registra y sigue) o si debe llegar al usuario (el
    worker del pipeline la convierte en evento de error para el front).
    """
    global _pandas_listo
    with _candado:
        if _pandas_listo:
            return
        import pandas  # noqa: F401  — el efecto buscado es el import en sí
        _pandas_listo = True


def precargar_en_arranque() -> None:
    """Precarga desde `main()`, en el hilo principal, sin poder tumbar la app.

    Si pandas está roto en el bundle, la app debe seguir abriendo: el usuario
    puede subir ficheros, ver logs y actualizar. Lo que NO puede pasar es que
    el fallo sea invisible, así que aquí queda el traceback completo.
    """
    try:
        precargar_pandas()
    except Exception:  # noqa: BLE001 — cualquier fallo de import debe quedar escrito
        logger.exception(
            "La precarga de pandas falló al arrancar: organizar y leer estadillos "
            "van a fallar. Este traceback es el error REAL del empaquetado."
        )
    else:
        logger.info("pandas precargado en el hilo principal")
