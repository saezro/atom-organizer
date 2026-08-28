"""Persistencia de tamaño/posición de la ventana de escritorio.

Guarda geometría en `ventana.json`, un fichero APARTE de `Config.ini`
(`external_tools._user_config_path()`), NUNCA dentro de él: `Api.write_config`
reescribe ese `.ini` ENTERO con las claves que conoce el formulario de
configuración, así que cualquier sección de geometría metida ahí se perdería
en el primer guardado de config que haga el usuario.

`EstadoVentana` es lógica pura (sin pywebview) para que sea testeable sin
levantar una ventana real: los eventos del backend Qt (`resized`, `moved`,
`maximized`, `restored`) se traducen a llamadas a sus métodos desde
`app_webview.py`.
"""
from __future__ import annotations

import json
import os
import tempfile

__all__ = ["ruta_estado", "EstadoVentana", "leer", "guardar"]


def ruta_estado() -> str:
    """Ruta del JSON de geometría, en el mismo directorio que `Config.ini`.

    Importa `_user_config_path` en local (no a nivel de módulo) para no crear
    un import circular con `external_tools` y para no tocar disco —esa función
    crea el directorio y siembra el `.ini`— con solo importar este módulo.
    """
    from external_tools import _user_config_path

    return os.path.join(os.path.dirname(_user_config_path()), "ventana.json")


class EstadoVentana:
    """Estado en memoria de la geometría de ventana, actualizado por eventos."""

    def __init__(self, ancho, alto, x=None, y=None, maximizada=True):
        self.ancho = ancho
        self.alto = alto
        self.x = x
        self.y = y
        self.maximizada = maximizada

    def on_resized(self, w, h):
        # Mientras está maximizada, w/h son el tamaño de la pantalla completa:
        # si los guardáramos como "tamaño restaurado", la ventana ya no podría
        # volver a su tamaño normal al des-maximizar en el siguiente arranque.
        if self.maximizada:
            return
        self.ancho = w
        self.alto = h

    def on_moved(self, x, y):
        if self.maximizada:
            return
        self.x = x
        self.y = y

    def on_maximized(self):
        self.maximizada = True

    def on_restored(self):
        self.maximizada = False

    def snapshot(self) -> dict:
        return {
            "ancho": self.ancho,
            "alto": self.alto,
            "x": self.x,
            "y": self.y,
            "maximizada": self.maximizada,
        }


def _es_sano(datos) -> bool:
    if not isinstance(datos, dict):
        return False
    claves = {"ancho", "alto", "x", "y", "maximizada"}
    if not claves.issubset(datos.keys()):
        return False
    ancho, alto = datos["ancho"], datos["alto"]
    x, y = datos["x"], datos["y"]
    maximizada = datos["maximizada"]
    if isinstance(ancho, bool) or isinstance(alto, bool):
        return False
    if not isinstance(ancho, int) or not isinstance(alto, int):
        return False
    if ancho < 400 or alto < 400:
        return False
    for coord in (x, y):
        if coord is not None and (isinstance(coord, bool) or not isinstance(coord, int)):
            return False
    if not isinstance(maximizada, bool):
        return False
    return True


def leer() -> dict | None:
    """Lee y valida `ventana.json`. Nunca lanza: cualquier fallo → None."""
    try:
        with open(ruta_estado(), "r", encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return None
    if not _es_sano(datos):
        return None
    return datos


def guardar(estado: dict) -> bool:
    """Escritura atómica de la geometría. Nunca lanza (se llama al cerrar la app;
    un fallo aquí no debe impedir el cierre): devuelve False en error."""
    try:
        destino = ruta_estado()
        base = os.path.dirname(destino)
        os.makedirs(base, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix="ventana.", suffix=".json.tmp", dir=base)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(estado, f)
            os.replace(tmp_path, destino)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return True
    except Exception:
        return False
