"""Un LOTE es una pulsacion de "Subir": carpeta propia bajo SUBIDAS/ con sus
imagenes y SUS estadillos. Aislar fisicamente es lo que impide que dos pilotos
simultaneos se roben imagenes por ventana temporal.

Contrato replicado en Atom-suite/lib/organizer-lotes.js — si cambia la regla,
cambia en los DOS sitios (los tests de cada lado son el contrato).
"""
from __future__ import annotations

from datetime import datetime, timezone

from atom_core.inspecciones import _campo  # mismo saneado que el prefijo

__all__ = [
    "CARPETA_SUBIDAS",
    "VERSION_MANIFEST_LOTE",
    "nombre_lote",
    "manifest_lote",
]

CARPETA_SUBIDAS = "SUBIDAS"
VERSION_MANIFEST_LOTE = 2
SEP = "__"


def nombre_lote(ahora: datetime, usuario: str) -> str:
    """Timestamp UTC + usuario saneado: `2026-08-20T154210Z__rodrigo_saez`.

    Idéntico byte a byte al `nombreLote` de `Atom-suite/lib/organizer-lotes.js`
    (mismo sello sin dos puntos, mismo separador `__`, mismo saneado de
    `campoPrefijo`/`_campo` con `_` como fallback si el usuario queda vacío).
    """
    sello = ahora.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return f"{sello}{SEP}{_campo(usuario) or '_'}"


def manifest_lote(lote: str, subido_por: str | None, estadillos: list[str],
                  num_objetos: int) -> dict:
    """El marcador de "lote completo": se escribe EL ÚLTIMO, tras subir todo
    lo demás con éxito. Su sola presencia bajo `<lote>/manifest.json` es la
    señal de que la Suite puede organizar ese lote."""
    return {
        "version": VERSION_MANIFEST_LOTE,
        "lote": lote,
        "subido_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subido_por": subido_por or None,
        "estadillos": list(estadillos),
        "num_objetos": num_objetos,
    }
