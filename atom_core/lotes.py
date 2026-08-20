"""Un LOTE es una pulsacion de "Subir": carpeta propia bajo SUBIDAS/ con sus
imagenes y SUS estadillos. Aislar fisicamente es lo que impide que dos pilotos
simultaneos se roben imagenes por ventana temporal.

Contrato replicado en Atom-suite/lib/organizer-lotes.js — si cambia la regla,
cambia en los DOS sitios (los tests de cada lado son el contrato).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from atom_core.inspecciones import _campo  # mismo saneado que el prefijo

__all__ = [
    "CARPETA_SUBIDAS",
    "VERSION_MANIFEST_LOTE",
    "ESTADO_LOTES",
    "nombre_lote",
    "manifest_lote",
    "estado_lote_carpeta",
    "registrar_lote",
    "marcar_lote_completo",
]

CARPETA_SUBIDAS = "SUBIDAS"
VERSION_MANIFEST_LOTE = 2
SEP = "__"

# Fichero donde se recuerda, por CARPETA local, qué lote le corresponde. Vive
# junto a `session.db` (mismo `user_data_dir` que `session_store.py`), así que
# sobrevive a cerrar la app y a reinicios: es justo lo que hace falta para que
# reintentar una subida cortada reutilice el prefijo de antes en vez de abrir
# uno nuevo (ver `lote_de_carpeta`/`registrar_lote`).
ESTADO_LOTES = "lotes_carpetas.json"


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


# --------------------------------------------------------------------------
# Estado local: qué lote le toca a cada carpeta
# --------------------------------------------------------------------------
#
# Antes de esto, `cloud_upload` llamaba a `nombre_lote()` en CADA pulsación
# de "Subir": un reintento tras perder la conexión, o simplemente volver a
# subir la misma carpeta, abría un lote NUEVO cada vez. Consecuencia doble:
# el lote a medias anterior quedaba huérfano en el bucket para siempre (no
# hay manifest.json, la Suite nunca lo recoge), y una carpeta ya completada
# que se resubía generaba un segundo lote completo con las mismas fotos, que
# la Suite procesaba dos veces.
#
# La regla ahora distingue por qué se reintenta:
#   * Lote INCOMPLETO (no llegó a escribirse su `manifest.json`: se cortó la
#     red, se canceló, falló a medias) → se REUSA sin preguntar. Es la
#     definición de "reanudar", y `upload_plan` reconcilia contra lo que ya
#     haya en `prefix_lote` en vez de volver a mandarlo todo.
#   * Lote COMPLETO (su `manifest.json` sí se escribió: la Suite ya lo dio
#     por bueno) → NO se reusa en silencio. Volver a subir esa carpeta solo
#     es correcto si es una subida EXTRA de OTRO estadillo sobre la misma
#     carpeta local; eso lo decide el operario, no la app (ver
#     `Api.cloud_upload`/`confirmar_subida_extra`).
def _ruta_estado_lotes() -> Path:
    from atom_core.google_auth import user_data_dir
    return user_data_dir() / ESTADO_LOTES


def _clave_carpeta(root) -> str:
    """La carpeta local se identifica por su ruta absoluta resuelta: dos
    formas de escribir la misma ruta (relativa, con `..`, symlink) tienen
    que apuntar al mismo lote."""
    return str(Path(root).resolve())


def _leer_estado_lotes(ruta: Path | None = None) -> dict[str, dict]:
    ruta = ruta or _ruta_estado_lotes()
    try:
        crudo = ruta.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        datos = json.loads(crudo)
    except ValueError:
        # Fichero corrupto (escritura a medias, disco lleno...): se trata como
        # "sin estado" en vez de reventar la subida. Se sobreescribirá limpio
        # en el próximo `registrar_lote`.
        return {}
    if not isinstance(datos, dict):
        return {}
    # Compatibilidad hacia atrás: una entrada guardada como string suelto
    # (versión previa de este fichero, sin `completo`) se trata como lote
    # completo — es el lado prudente: si no se sabe, se pide confirmación
    # antes de reusar, en vez de resumir a ciegas un lote que a lo mejor ya
    # terminó hace tiempo.
    out: dict[str, dict] = {}
    for clave, valor in datos.items():
        if isinstance(valor, dict) and "lote" in valor:
            out[clave] = {"lote": valor["lote"], "completo": bool(valor.get("completo"))}
        elif isinstance(valor, str):
            out[clave] = {"lote": valor, "completo": True}
    return out


def _escribir_estado_lotes(estado: dict[str, dict]) -> None:
    """Escritura atómica (fichero temporal + `os.replace`): un corte a medias
    no debe dejar `lotes_carpetas.json` truncado y sin poder leerse."""
    ruta = _ruta_estado_lotes()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, ruta)


def estado_lote_carpeta(root) -> dict | None:
    """`{"lote": ..., "completo": bool}` del lote ya registrado para esta
    carpeta, o `None` si nunca se subió desde aquí."""
    return _leer_estado_lotes().get(_clave_carpeta(root))


def registrar_lote(root, lote: str) -> None:
    """Recuerda que `root` sube al lote `lote`, todavía INCOMPLETO.

    Se llama al abrir un lote nuevo (no había estado, o el anterior estaba
    completo y el operario confirmó la subida extra). `marcar_lote_completo`
    es lo que lo pasa a completo, tras escribir `manifest.json` con éxito.
    """
    estado = _leer_estado_lotes()
    estado[_clave_carpeta(root)] = {"lote": lote, "completo": False}
    _escribir_estado_lotes(estado)


def marcar_lote_completo(root, lote: str) -> None:
    """Marca `lote` como completo para `root`, tras subir su `manifest.json`
    con éxito. Si el estado ya no coincide (se registró otro lote entre
    medias), no toca nada: no hay nada que reconciliar aquí, es una carrera
    rarísima y perder el marcado es preferible a pisar un lote distinto."""
    estado = _leer_estado_lotes()
    actual = estado.get(_clave_carpeta(root))
    if actual is None or actual.get("lote") != lote:
        return
    actual["completo"] = True
    _escribir_estado_lotes(estado)
