"""Decide si la ventana de escritorio arranca con GPU o con rasterizado software.

Contexto: en Windows se forzaba `--disable-gpu` SIEMPRE porque sin GPU real
(máquina virtual, sesión RDP, drivers pobres) el compositing acelerado de
QtWebEngine deja la ventana EN NEGRO. El precio era pintar todo por CPU en
cualquier portátil, y con listas largas y animaciones eso se nota como lag.

Aquí se sustituye ese "software siempre" por un intento con GPU que se
auto-degrada solo: antes de abrir la ventana se marca el arranque como
`pendiente`; el frontend, en cuanto ha pintado su primer frame, llama a
`confirmar_render()` y el marcador se limpia. Si la app vuelve a arrancar con
el marcador puesto, el arranque anterior no llegó a pintar (pantalla negra).

Dos matices, ambos porque caer a software se paga en lag continuo desde el
primer segundo: hacen falta DOS arranques seguidos sin pintar (matar el
proceso, cerrar la ventana antes de que pinte o un crash del updater dejan el
marcador puesto sin culpa de la GPU), y la degradación NO es permanente —
cada `REINTENTO_CADA` arranques se vuelve a probar. Así funciona en cualquier
máquina sin que el usuario tenga que saber nada, y el ajuste manual (`modo`)
permite forzar cualquiera de los dos.

Estado en `render.json`, fichero APARTE de `Config.ini` por lo mismo que
`window_state`: `Api.write_config` reescribe ese `.ini` entero.
"""
from __future__ import annotations

import json
import os
import tempfile

__all__ = [
    "MODOS",
    "ESTADO_INICIAL",
    "ruta_estado",
    "leer",
    "guardar",
    "decidir",
    "confirmar_render",
    "set_modo",
]

MODOS = ("auto", "gpu", "software")

# Cuántos arranques seguidos sin pintar hacen falta para caer a software. Uno
# solo no basta: matar el proceso, cerrar la ventana antes de que pinte o un
# crash del updater dejan el marcador puesto sin que la GPU tenga la culpa.
UMBRAL_FALLOS = 2
# Cada cuántos arranques en software se vuelve a probar con GPU. Sin esto la
# degradación es para siempre y el usuario se queda con todo pintado por CPU.
REINTENTO_CADA = 10

ESTADO_INICIAL = {"modo": "auto", "pendiente": False, "fallos": 0, "arranques_sw": 0}


def ruta_estado() -> str:
    """Ruta del JSON, en el mismo directorio que `Config.ini`."""
    from external_tools import _user_config_path

    return os.path.join(os.path.dirname(_user_config_path()), "render.json")


def _es_sano(datos) -> bool:
    if not isinstance(datos, dict):
        return False
    if not {"modo", "pendiente", "fallos"}.issubset(datos.keys()):
        return False
    if datos["modo"] not in MODOS:
        return False
    if not isinstance(datos["pendiente"], bool):
        return False
    fallos = datos["fallos"]
    if isinstance(fallos, bool) or not isinstance(fallos, int) or fallos < 0:
        return False
    sw = datos.get("arranques_sw", 0)
    if isinstance(sw, bool) or not isinstance(sw, int) or sw < 0:
        return False
    return True


def leer() -> dict:
    """Lee y valida `render.json`. Nunca lanza: cualquier fallo → estado inicial."""
    try:
        with open(ruta_estado(), "r", encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        return dict(ESTADO_INICIAL)
    if not _es_sano(datos):
        return dict(ESTADO_INICIAL)
    return {
        "modo": datos["modo"],
        "pendiente": datos["pendiente"],
        "fallos": datos["fallos"],
        # Ausente en los render.json escritos antes del reintento periódico.
        "arranques_sw": datos.get("arranques_sw", 0),
    }


def guardar(estado: dict) -> bool:
    """Escritura atómica. Nunca lanza: devuelve False en error."""
    try:
        destino = ruta_estado()
        base = os.path.dirname(destino)
        os.makedirs(base, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix="render.", suffix=".json.tmp", dir=base)
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


def decidir(estado: dict) -> tuple[bool, dict, str]:
    """Lógica pura: ¿arrancamos con GPU? Devuelve (usar_gpu, estado_nuevo, motivo).

    `estado_nuevo` es lo que hay que persistir ANTES de abrir la ventana (marca
    el intento como pendiente de confirmación).
    """
    modo = estado.get("modo", "auto")
    nuevo = dict(estado)
    if modo == "gpu":
        # El usuario manda: no auto-degradar aunque el arranque anterior fallara.
        nuevo["pendiente"] = False
        nuevo["fallos"] = 0
        return True, nuevo, "forzado por ajuste"
    if modo == "software":
        nuevo["pendiente"] = False
        return False, nuevo, "forzado por ajuste"
    if estado.get("pendiente"):
        # Arrancamos y el marcador del intento anterior sigue puesto: aquella
        # ventana nunca confirmó su primer frame.
        nuevo["pendiente"] = False
        nuevo["fallos"] = estado.get("fallos", 0) + 1
        if nuevo["fallos"] < UMBRAL_FALLOS:
            # Un fallo suelto no condena: el siguiente arranque reintenta.
            return False, nuevo, "arranque anterior sin pintar, se reintentará con GPU"
        nuevo["arranques_sw"] = 0
        return False, nuevo, "degradado: dos arranques seguidos sin pintar"
    if estado.get("fallos", 0) >= UMBRAL_FALLOS:
        # Degradado, pero no para siempre: cada REINTENTO_CADA arranques se
        # vuelve a probar la GPU (drivers actualizados, fuera de RDP, etc.).
        sw = estado.get("arranques_sw", 0) + 1
        if sw >= REINTENTO_CADA:
            nuevo["arranques_sw"] = 0
            nuevo["fallos"] = 0
            nuevo["pendiente"] = True
            return True, nuevo, "reintento periódico con GPU"
        nuevo["arranques_sw"] = sw
        nuevo["pendiente"] = False
        return False, nuevo, "degradado: arranques anteriores no llegaron a pintar"
    nuevo["pendiente"] = True
    return True, nuevo, "intento con GPU"


def confirmar_render(estado: dict) -> dict:
    """El frontend ha pintado: el intento con GPU fue bueno."""
    nuevo = dict(estado)
    nuevo["pendiente"] = False
    nuevo["fallos"] = 0
    nuevo["arranques_sw"] = 0
    return nuevo


def set_modo(estado: dict, modo: str) -> dict:
    """Cambia el modo desde Ajustes. Un cambio manual limpia la degradación."""
    if modo not in MODOS:
        raise ValueError(f"modo no válido: {modo!r}")
    nuevo = dict(estado)
    nuevo["modo"] = modo
    nuevo["fallos"] = 0
    nuevo["arranques_sw"] = 0
    nuevo["pendiente"] = False
    return nuevo
