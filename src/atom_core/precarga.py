"""Precarga determinista de las librerías pesadas con C-extensions (pandas).

PORQUÉ EXISTE ESTE MÓDULO
-------------------------
El fallo que persigue es el que rompió `Organizar completo` en las v3.4.75 a
v3.4.77 del .exe de Windows:

    AttributeError: partially initialized module 'pandas' has no attribute
    '_pandas_datetime_CAPI' (most likely due to a circular import)

Ese mensaje NUNCA habla del sitio donde está el problema: es lo que sale cuando
un `import pandas` deja el módulo a medias en `sys.modules` y alguien lo vuelve
a importar después. Es el síntoma; la causa está en el PRIMER import, el que
falló y cuyo traceback se perdía porque en el camino con ventana el logger raíz
no tenía handler.

CAUSA RAÍZ CONFIRMADA (log del .exe v3.4.77)
--------------------------------------------
    ImportError: Can't determine version for pytz

pandas 3 no depende de pytz, pero `pandas._libs.tslibs.timezones` lo pide con
`import_optional_dependency("pytz")` y esa función llama a `get_version(module)`
FUERA del try/except del import. O sea: que la dependencia sea "opcional" solo
cubre el caso de que NO esté. Si `import pytz` funciona pero el módulo no expone
`__version__`, pandas revienta. En el bundle de Windows pasaba exactamente eso:
`import pytz` resolvía sin el módulo real detrás. En Linux no se ve porque allí
pytz no está instalado, el import falla limpio y pandas tira de `zoneinfo`.

El arreglo de empaquetado (pytz pineado en requirements.txt y `collect_all('pytz')`
en el .spec) va aparte. Aquí se ataca lo mismo desde el runtime, para que la app
no vuelva a quedar inservible si el bundle se degrada:

  - `_neutralizar_pytz_sin_version()`: si pytz está pero está mutilado, se pone
    `sys.modules['pytz'] = None`, que hace que el siguiente `import pytz` lance
    ImportError. Es justo el caso que pandas SÍ sabe manejar.
  - Si aun así el import de pandas falla, se purga `pandas*` de `sys.modules`
    para que el siguiente intento vuelva a dar el error REAL en vez del
    `_pandas_datetime_CAPI` que despista.
  - El import de pandas corre en un hilo de fondo (importarlo retrasaba más de
    un segundo la aparición de la ventana), pero la neutralización de pytz se
    hace ANTES, en el hilo principal, vía `neutralizar_pytz()`: así queda hecha
    aunque otro `import pandas` gane la carrera al hilo de precarga.

El `Lock` es el cinturón además de los tirantes: si alguna ruta de código llega
antes que la precarga (tests, modo servidor, un entry point futuro), los imports
diferidos siguen serializados entre sí.
"""
from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

# Un único candado para TODAS las precargas: lo que se serializa es "el primer
# import pesado", no un módulo concreto.
_candado = threading.Lock()
_pandas_listo = False


def _neutralizar_pytz_sin_version() -> None:
    """Deja fuera de juego un pytz importable pero sin `__version__`.

    `sys.modules['pytz'] = None` hace que el import siguiente lance ImportError,
    que es el único desenlace que `import_optional_dependency` sabe tragarse.
    """
    try:
        import pytz  # noqa: PLC0415 — comprobación deliberadamente perezosa
    except Exception:  # noqa: BLE001 — ausente o roto: pandas lo trata como opcional
        return
    if getattr(pytz, "__version__", None) is None:
        sys.modules["pytz"] = None  # type: ignore[assignment]
        logger.warning(
            "pytz está presente pero sin `__version__` (bundle degradado): se "
            "neutraliza para que pandas lo trate como ausente y use zoneinfo."
        )


def neutralizar_pytz() -> None:
    """Punto de entrada público de `_neutralizar_pytz_sin_version`, para poder
    dejar pytz saneado en el hilo principal antes de lanzar la precarga."""
    _neutralizar_pytz_sin_version()


def _purgar_pandas_de_sys_modules() -> None:
    """Borra los `pandas*` a medias tras un import fallido.

    Sin esto, el segundo intento no ve el error real: ve el módulo incompleto ya
    registrado y lanza el `_pandas_datetime_CAPI`.
    """
    for nombre in [m for m in sys.modules if m == "pandas" or m.startswith("pandas.")]:
        sys.modules.pop(nombre, None)


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
        _neutralizar_pytz_sin_version()
        try:
            import pandas  # noqa: F401  — el efecto buscado es el import en sí
        except BaseException:
            _purgar_pandas_de_sys_modules()
            raise
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
