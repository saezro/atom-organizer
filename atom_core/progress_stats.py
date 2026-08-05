"""Estadísticas en vivo del pipeline, derivadas del canal de log.

El pipeline ya cuenta todo lo que interesa ver en la UI (cuántas imágenes hay en
cada directorio, cuántas se van a rotar 270/90 y cuántas no), pero lo vuelca como
TEXTO al canal `progress_callback` — la app solo recibía porcentajes y una lluvia
de "." que además `organize._on_log` descartaba. Aquí se parsea ese texto y se
convierte en un snapshot estructurado que el modal de progreso puede pintar.

Se hace por parsing y NO tocando `pipeline.py` a propósito: son ~3.900 líneas con
20 puntos de emisión y el objetivo del bloque es ADITIVO (cero cambio de
comportamiento). El módulo es puro (sin Qt, sin importar `gui`) para poder
testearlo directamente.

Contadores por FASE (se reinician en cada `start_phase`):
  done / total  — imágenes procesadas vs anunciadas
  rgb / termica — reparto de las anunciadas según la carpeta (RGB* vs TERMICA)

Contadores del RUN (acumulados, nunca se reinician entre fases):
  rot270 / rot90 / rot_none — se anuncian una vez por directorio, así que sumar
  a lo largo del run es lo correcto.
"""
from __future__ import annotations

import re

# Un "." por imagen inunda el puente Python→JS (cada evento es un evaluate_js).
# Se emite un snapshot cada N imágenes, y siempre al completar el total.
IMAGE_EMIT_EVERY = 10

# "Procesando 120 imágenes en el directorio /ruta", "Procesando y girando 30
# imágenes TIFF", "Procesando 12 TMCs en el directorio ...". El directorio es
# opcional porque varios puntos del pipeline no lo incluyen.
_RE_PROCESANDO = re.compile(
    r"Procesando(?:\s+y\s+\w+)?\s+(\d+)\s+(?:im[áa]genes|TMCs)"
    r"(?:\s+TIFF)?"
    # "en el directorio X" y "en directorio X" conviven en el pipeline; exigir el
    # artículo dejaba la fase de meta/geolocalización con total=0 y el contador
    # mostrando cosas como "10/0 img".
    r"(?:\s+en\s+(?:el\s+)?directorio\s+(\S.*?))?\s*$"
)
# "Moviendo 5 imágenes Térmicas al directorio X" (fase de estructura de carpetas).
# Sin esto la fase entera avanzaba con total=0.
_RE_MOVIENDO = re.compile(
    r"Moviendo\s+(\d+)\s+im[áa]genes\s+\w+\s+al\s+directorio\s+(\S.*?)\s*$"
)
# El directorio se anuncia en su propia línea y el "Procesando N imágenes" que
# viene detrás no lo repite: sin recordarlo, la fase de conversión a TIFF contaba
# las térmicas como "RGB 0 / térmica 0".
_RE_ANALIZANDO = re.compile(r"Analizando\s+(?:el\s+)?directorio:?\s+(\S.*?)\s*$")
_RE_ROT_270 = re.compile(r"im[áa]genes rotadas 270:\s*(\d+)")
_RE_ROT_90 = re.compile(r"im[áa]genes rotadas 90:\s*(\d+)")
_RE_ROT_NONE = re.compile(r"im[áa]genes sin rotar:\s*(\d+)")


def classify_folder(path: str) -> str | None:
    """'rgb' | 'termica' | None a partir de la ruta de un directorio del pipeline.

    La separación crea `<destino>/RGB`, `<destino>/RGB_EXTRA` y
    `<destino>/TERMICA`, y las subcarpetas de vuelo cuelgan de ahí; se busca el
    segmento MÁS PROFUNDO que identifique el tipo, para que un destino que por
    casualidad se llame "RGB" no clasifique las térmicas que hay debajo.
    """
    if not path:
        return None
    for part in reversed(re.split(r"[\\/]+", path.strip())):
        upper = part.upper()
        if upper == "TERMICA":
            return "termica"
        if upper == "RGB" or upper.startswith("RGB_"):
            return "rgb"
    return None


class StatsTracker:
    """Acumula el estado y decide cuándo merece la pena re-emitirlo."""

    def __init__(self) -> None:
        self.phase_index = 0
        self.phase_name = ""
        self.rot270 = 0
        self.rot90 = 0
        self.rot_none = 0
        self._reset_phase()

    def _reset_phase(self) -> None:
        self.done = 0
        self.total = 0
        self.rgb = 0
        self.termica = 0
        # Último directorio anunciado en la fase, para clasificar los recuentos
        # que llegan sin ruta propia.
        self.last_dir = ""

    def start_phase(self, index: int, name: str) -> None:
        self.phase_index = index
        self.phase_name = name
        self._reset_phase()

    def on_image(self) -> bool:
        """Un '.' del pipeline == una imagen procesada. True si toca emitir."""
        self.done += 1
        return self.done % IMAGE_EMIT_EVERY == 0 or (
            self.total > 0 and self.done == self.total
        )

    def on_line(self, text: str) -> bool:
        """Parsea una línea (o bloque multilínea) del canal log/summary.

        Devuelve True si el snapshot cambió: los conteos son eventos escasos
        (uno por directorio), así que cada cambio se emite sin throttling.
        """
        changed = False
        for line in str(text).splitlines():
            m_dir = _RE_ANALIZANDO.search(line)
            if m_dir:
                self.last_dir = m_dir.group(1)
            m = _RE_PROCESANDO.search(line) or _RE_MOVIENDO.search(line)
            if m:
                n = int(m.group(1))
                self.total += n
                # Si el recuento no trae ruta, vale la del último directorio
                # anunciado: el pipeline los emite siempre en ese orden.
                kind = classify_folder(m.group(2) or self.last_dir)
                if kind == "rgb":
                    self.rgb += n
                elif kind == "termica":
                    self.termica += n
                changed = True
            for regex, attr in ((_RE_ROT_270, "rot270"), (_RE_ROT_90, "rot90"),
                                (_RE_ROT_NONE, "rot_none")):
                m = regex.search(line)
                if m:
                    setattr(self, attr, getattr(self, attr) + int(m.group(1)))
                    changed = True
        return changed

    def snapshot(self) -> dict:
        return {
            "phase_index": self.phase_index,
            "phase_name": self.phase_name,
            "done": self.done,
            # Nunca anunciar un total menor que lo ya hecho: si un punto del
            # pipeline emite un recuento con una redacción que aquí no se
            # reconoce, es preferible un total que se queda corto a un "10/0".
            "total": max(self.total, self.done),
            "rgb": self.rgb,
            "termica": self.termica,
            "rot270": self.rot270,
            "rot90": self.rot90,
            "rot_none": self.rot_none,
        }
