"""Salir del bundle para hablar con el sistema: entorno limpio y navegador.

**El problema.** Un ejecutable congelado (PyInstaller, y más aún dentro de un
AppImage) arranca con el entorno reescrito para que el proceso encuentre *sus*
librerías: `LD_LIBRARY_PATH` apunta al bundle, y con él `PYTHONHOME`,
`QT_PLUGIN_PATH` y compañía. Eso está bien mientras todo se quede dentro. Deja
de estarlo en cuanto la app lanza un programa del sistema: el hijo hereda ese
`LD_LIBRARY_PATH` y enlaza contra las librerías del bundle, que son de otra
versión.

Lo que se vio en producción (Linux, v3.4.21), al pulsar «Iniciar sesión»:

    /bin/sh: symbol lookup error: /bin/sh: undefined symbol: rl_trim_arg_from_keyseq

`xdg-open` es un script de shell; el `/bin/sh` que lo ejecuta acabó enlazando la
`libreadline` del bundle, que no trae ese símbolo. Muere antes de abrir nada. La
app no se entera —ella cree que ha abierto el navegador— y se queda esperando un
callback de OAuth que no va a llegar nunca: el botón congelado en «Ejecutando…».

**La solución.** PyInstaller guarda el valor original de las variables que pisa
en `<VAR>_ORIG`. Al salir al sistema se restaura ese original (o se borra la
variable si no había nada antes), y el hijo enlaza contra las librerías del
sistema, que es lo que quiere.

Windows no tiene nada de esto: ni `LD_LIBRARY_PATH` ni el problema. Ahí se abre
el navegador como siempre, que es lo que corre en producción y no se toca.
"""
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser

__all__ = ["dentro_de_bundle", "entorno_del_sistema", "abrir_en_navegador"]

# Las que reescribe el arranque del bundle y envenenan a cualquier hijo.
_VARS_DEL_BUNDLE = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
)


def dentro_de_bundle() -> bool:
    """¿Corremos como ejecutable congelado (PyInstaller/AppImage)?"""
    return getattr(sys, "frozen", False)


def entorno_del_sistema(base: dict[str, str] | None = None) -> dict[str, str]:
    """Copia del entorno tal y como estaba ANTES de que el bundle lo reescribiera.

    Para cada variable contaminada se usa su `<VAR>_ORIG` —lo que PyInstaller
    apartó al arrancar—, y si no hay original es que la variable no existía: se
    quita, en vez de dejar el valor del bundle.
    """
    entorno = dict(os.environ if base is None else base)
    for var in _VARS_DEL_BUNDLE:
        original = entorno.pop(f"{var}_ORIG", None)
        if original:
            entorno[var] = original
        else:
            entorno.pop(var, None)
    return entorno


def abrir_en_navegador(url: str) -> bool:
    """Abre `url` en el navegador del usuario. `True` si se lanzó algo.

    Fuera de un bundle de Linux esto es `webbrowser.open` y nada más. Dentro,
    hay que lanzar el proceso a mano para poder darle el entorno del sistema:
    `webbrowser` usa `os.environ` sin dejar cambiarlo.
    """
    if not (dentro_de_bundle() and sys.platform.startswith("linux")):
        return webbrowser.open(url)

    entorno = entorno_del_sistema()
    # El navegador debe sobrevivir a la app: `start_new_session` lo saca del
    # grupo de procesos, para que no se lo lleve por delante al cerrar.
    for orden in _candidatos(entorno):
        try:
            subprocess.Popen(
                [*orden, url],
                env=entorno,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            continue
    # Sin nada que lanzar, mejor el intento de `webbrowser` que no hacer nada:
    # puede fallar por lo mismo, pero no perdemos ninguna opción por no probar.
    return webbrowser.open(url)


def _candidatos(entorno: dict[str, str]) -> list[list[str]]:
    """Qué intentar, en orden: lo que el usuario haya puesto en `$BROWSER`, y luego
    los abridores del escritorio."""
    ordenes: list[list[str]] = []
    elegido = (entorno.get("BROWSER") or "").strip()
    if elegido:
        # `$BROWSER` admite lista separada por dos puntos, y entradas con `%s`
        # donde va la URL; aquí la URL se añade al final, así que se descarta el
        # marcador para no pasarlo como argumento literal.
        for parte in elegido.split(":"):
            parte = parte.strip()
            if parte:
                ordenes.append([p for p in parte.split() if p != "%s"])
    ordenes.append(["xdg-open"])
    ordenes.append(["gio", "open"])
    return ordenes
