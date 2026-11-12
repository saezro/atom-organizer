"""Cámara RGB que se recorta (`_CROP`) cuando la tanda trae RGB `_W` (wide) y
`_Z` (zoom) del mismo disparo (DJI H30T y similares).

Ajuste `camara_rgb`: `"Z"` (por defecto) o `"W"`. Solo tiene efecto si la
tanda contiene alguna `_Z`; sin `_Z` el recorte funciona como siempre (se
recorta toda RGB). Con `Z` solo se recorta la `_Z` y todo lo demás se copia
tal cual; con `W` ("no usar Z") se recorta todo salvo la `_Z`.

Origen del valor, de mayor a menor prioridad: argumento `--camara-rgb` del
CLI/job (`params["camara_rgb"]`) > `Config.ini` (`[cropping] camara_rgb`) > `Z`.
Un valor distinto de Z|W es un error, nunca se corrige en silencio.

% de recorte: `[percentage_by_models]` admite `MODELO` (wide / genérica) y
`MODELO_Z` (zoom). Ver `clave_porcentaje`.
"""
from __future__ import annotations

import os
import re

CAMARAS_RGB = ("Z", "W")
CAMARA_RGB_DEFECTO = "Z"
SECCION_CONFIG = "cropping"
CLAVE_CONFIG = "camara_rgb"

# `_Z` final del nombre (sin extensión), admitiendo el `_pointN` de la H30T.
_RE_ZOOM = re.compile(r"_Z(?:_point\d+)?$", re.IGNORECASE)


def normalizar_camara(valor) -> str:
    """`"Z"`/`"W"` (acepta minúsculas y espacios). Cualquier otra cosa -incluido
    vacío- lanza `ValueError`: no se adivina."""
    texto = str(valor if valor is not None else "").strip().upper()
    if texto not in CAMARAS_RGB:
        raise ValueError(
            f"camara_rgb inválida: {valor!r}. Valores permitidos: {' | '.join(CAMARAS_RGB)}.")
    return texto


def es_zoom(nombre: str) -> bool:
    """¿El nombre (con o sin carpeta/extensión) es una RGB `_Z`?"""
    base = os.path.splitext(os.path.basename(str(nombre)))[0]
    return bool(_RE_ZOOM.search(base))


def tanda_trae_zoom(nombres) -> bool:
    return any(es_zoom(n) for n in nombres)


def se_recorta(nombre: str, camara: str, hay_zoom: bool) -> bool:
    """¿Esta RGB lleva `_CROP` según la cámara elegida? Sin `_Z` en la tanda
    (`hay_zoom` False) se recorta todo, como siempre."""
    if not hay_zoom:
        return True
    zoom = es_zoom(nombre)
    return zoom if normalizar_camara(camara) == "Z" else not zoom


def clave_porcentaje(modelo: str, camara: str, hay_zoom: bool) -> str:
    """Clave de `[percentage_by_models]` que manda para recortar una RGB de
    `modelo`: `MODELO_Z` si se recorta la zoom, `MODELO` en cualquier otro caso
    (wide / genérica / tanda sin `_Z`). Normalizada a mayúsculas."""
    base = str(modelo).strip("\x00").strip().upper()
    if hay_zoom and normalizar_camara(camara) == "Z":
        return base + "_Z"
    return base


AJUSTE_MANIFIESTO = "camara_rgb"


def fijar_camara_en_manifiesto(manifiesto, cfg, fase_indice: bool, emit=None):
    """Coherencia entre fases: la cámara con la que se construyó el índice es la
    que mandan apply y cierre.

    - Fase con índice (`completo`/`indice`/`indice-reduce`): guarda `cfg.camara_rgb`
      en el manifiesto y devuelve `cfg`.
    - Resto de fases: lee el valor guardado. Si el usuario pidió una cámara
      explícita (`cfg.camara_rgb_explicita`, p. ej. `--camara-rgb`) y no coincide ->
      `ValueError` (nunca se corrige en silencio). Si no es explícita, manda lo
      guardado (se avisa si difiere del `Config.ini`). Manifiesto sin valor
      (anterior a este ajuste) -> se usa `cfg` y se avisa.
    Devuelve el `cfg` a usar."""
    import dataclasses  # noqa: PLC0415
    actual = normalizar_camara(getattr(cfg, "camara_rgb", CAMARA_RGB_DEFECTO))
    if fase_indice:
        manifiesto.guardar_ajuste(AJUSTE_MANIFIESTO, actual)
        return cfg
    guardado = manifiesto.leer_ajuste(AJUSTE_MANIFIESTO)
    if guardado is None:
        if emit:
            emit(f"\nAVISO: el manifiesto no guarda camara_rgb (índice de una versión anterior); "
                 f"se usa {actual}.\n")
        return cfg
    guardado = normalizar_camara(guardado)
    if guardado == actual:
        return cfg
    if getattr(cfg, "camara_rgb_explicita", False):
        raise ValueError(
            f"camara_rgb={actual} no coincide con la del índice de este destino ({guardado}). "
            f"Relanza con --camara-rgb {guardado} o rehaz el índice con la cámara deseada.")
    if emit:
        emit(f"\nAVISO: camara_rgb={guardado} (la del índice) manda sobre la de Config.ini ({actual}).\n")
    return dataclasses.replace(cfg, camara_rgb=guardado)
