"""Lectura de ficheros de origen con verificación de integridad.

Por qué existe: en un montaje de Google Drive for Desktop (DriveFS) un fichero
aún sin hidratar puede leerse CORTO sin que el SO dé error (caso Willka, 2026-10-04:
689 de 2791 imágenes, PIL "image file is truncated" y DJI dirp -7). Aquí se compara
lo leído con el tamaño que dice el SO (y con `bytes_origen` del índice/manifiesto si
se conoce) y, si no cuadra, se espera, se fuerza la hidratación y se reintenta.
Nunca se devuelven datos parciales: tras los intentos se lanza `LecturaIncompleta`.
"""
from __future__ import annotations

import os
import shutil
import time

_BLOQUE = 1024 * 1024


class LecturaIncompleta(OSError):
    """El fichero se leyó con menos bytes de los esperados tras todos los reintentos."""

    def __init__(self, ruta, leidos, esperados, intentos=0):
        self.ruta = str(ruta)
        self.leidos = leidos
        self.esperados = esperados
        self.intentos = intentos
        super().__init__(
            "lectura incompleta: leídos {0} de {1} bytes tras {2} intentos ({3})".format(
                leidos, esperados, intentos, self.ruta))

    def __reduce__(self):  # picklable: el pool de procesos lo necesita
        return (type(self), (self.ruta, self.leidos, self.esperados, self.intentos))


# 64 KiB: los DJI dejan ~4097 B de relleno tras el EOI (con 4096 B el FFD9
# quedaba fuera de la ventana y se descartaban JPG buenos).
VENTANA_COLA_JPEG = 64 * 1024


def jpeg_eoi_en_cola_util(datos: bytes) -> bool:
    """Barato (sin decode): hay EOI (FFD9) en los últimos 64 KiB sin ceros finales."""
    return b"\xff\xd9" in datos.rstrip(b"\x00")[-VENTANA_COLA_JPEG:]


_RE_MARCADOR_EN_SCAN = None


def jpeg_eoi_por_segmentos(datos: bytes) -> bool:
    """Recorre los segmentos JPEG desde SOI hasta el EOI (FFD9) del scan principal.
    Sin decode ni escritura. Los datos tras ese EOI (payload R-JPEG de DJI, miniaturas,
    relleno) se ignoran, sea cual sea su tamaño. Falso si el fichero se corta antes
    del EOI (truncado a mitad de scan) o si el scan está relleno de ceros hasta el final.
    Un FFD9 dentro de una miniatura APP/EXIF NO cuenta: los segmentos se saltan por longitud."""
    import re
    global _RE_MARCADOR_EN_SCAN
    if _RE_MARCADOR_EN_SCAN is None:
        # Marcador real dentro del scan: FF (xFF)* seguido de byte que no es 00 (stuffing),
        # ni RSTn (D0-D7), ni otro FF.
        _RE_MARCADOR_EN_SCAN = re.compile(rb"\xff+[\x01-\xcf\xd8-\xfe]")
    n = len(datos)
    if n < 4 or datos[:2] != b"\xff\xd8":
        return False
    pos = 2
    while pos < n:
        if datos[pos] != 0xFF:
            return False  # basura entre segmentos: no es un JPEG íntegro
        while pos < n and datos[pos] == 0xFF:
            pos += 1
        if pos >= n:
            return False
        marcador = datos[pos]
        pos += 1
        if marcador == 0xD9:
            return True
        if marcador == 0x00:
            return False
        if marcador == 0x01 or 0xD0 <= marcador <= 0xD8:
            continue  # sin longitud
        if pos + 2 > n:
            return False
        longitud = (datos[pos] << 8) | datos[pos + 1]
        if longitud < 2 or pos + longitud > n:
            return False
        pos += longitud
        if marcador == 0xDA:  # tras la cabecera del SOS vienen los datos entrópicos
            m = _RE_MARCADOR_EN_SCAN.search(datos, pos)
            if m is None:
                return False  # el scan no termina: truncado / ceros hasta el final
            pos = m.end() - 1
            # `pos` apunta al byte de marcador; retrocede a su FF para el bucle
            pos -= 1
    return False


def jpeg_valido(datos: bytes) -> bool:
    """Criterio de validez DEFINITIVO. La cola de 64 KiB es solo la vía rápida:
    si no muestra EOI útil NO se concluye truncado (los R-JPEG DJI llevan payload
    > 64 KiB tras el EOI): se recorren los segmentos hasta el EOI del scan
    principal. En ambos casos se exige además decode completo con PIL, sin
    `LOAD_TRUNCATED_IMAGES` (un JPEG cortado y rellenado con ceros decodifica
    sin error, por eso el EOI es obligatorio). No gira ni recomprime nada."""
    import io
    if not (jpeg_eoi_en_cola_util(datos) or jpeg_eoi_por_segmentos(datos)):
        return False
    from PIL import Image
    try:
        with Image.open(io.BytesIO(datos)) as img:
            img.load()
        return True
    except Exception:  # noqa: BLE001 — truncado/corrupto = no válido
        return False


def jpeg_cola_valida(bytes_cola: bytes) -> bool:
    """Atajo rápido: la cola (últimos <=64 KiB) de un JPEG es plausible.
    Falso si está vacía, toda a ceros o no contiene el marcador FFD9 en
    ninguna posición (no se exige al final: los R-JPEG de DJI llevan datos
    tras EOI)."""
    return bool(bytes_cola) and any(bytes_cola) and b"\xff\xd9" in bytes_cola


def _hidratar(ruta) -> None:
    """Fuerza la hidratación: lee el fichero entero en secuencial hasta EOF."""
    try:
        with open(ruta, "rb") as f:
            while f.read(_BLOQUE):
                pass
    except OSError:
        pass


class FicheroCambiado(LecturaIncompleta):
    """El tamaño actual del fichero no coincide con el del índice: no es una
    lectura corta (no se espera ni se reintenta), el origen cambió."""

    def __init__(self, ruta, actual, indexado):
        self.ruta = str(ruta)
        self.leidos = 0
        self.esperados = actual
        self.intentos = 0
        self.actual = actual
        self.indexado = indexado
        OSError.__init__(
            self, "el fichero cambió desde el índice: {0} vs {1} bytes ({2})".format(
                indexado, actual, self.ruta))

    def __reduce__(self):
        return (type(self), (self.ruta, self.actual, self.indexado))


def _esperados(ruta, bytes_origen) -> int:
    """Tamaño esperado = `stat` del SO. Si `bytes_origen` (índice) existe y difiere,
    el fichero cambió desde el índice: falla al momento con `FicheroCambiado`."""
    esperados = os.stat(ruta).st_size
    if bytes_origen and int(bytes_origen) != esperados:
        raise FicheroCambiado(ruta, esperados, int(bytes_origen))
    return esperados


def leer_completo(ruta, intentos: int = 5, backoff: float = 0.5, backoff_max: float = 8.0,
                  bytes_origen: int | None = None) -> bytes:
    """Devuelve TODOS los bytes de `ruta` o lanza `LecturaIncompleta`.

    Espera entre intentos 0.5, 1, 2, 4, 8 s (tope `backoff_max`); antes de cada
    reintento hidrata el fichero leyéndolo entero en secuencial. Fallo rápido: dos
    lecturas consecutivas con los mismos bytes (< esperados) = EOF estable, sin
    más reintentos. El camino
    normal (lectura completa a la primera) no espera nada."""
    espera = backoff
    leidos = 0
    esperados = 0
    previo = None
    for n in range(1, max(1, intentos) + 1):
        esperados = _esperados(ruta, bytes_origen)
        with open(ruta, "rb") as f:
            datos = f.read()
        leidos = len(datos)
        if leidos >= esperados:
            return datos
        if n > 1 and leidos == previo:
            # EOF estable: dos lecturas seguidas devuelven los mismos bytes,
            # menos de los declarados. Reintentar no va a cambiarlo: falla ya.
            raise LecturaIncompleta(ruta, leidos, esperados, n)
        previo = leidos
        if n < intentos:
            time.sleep(espera)
            espera = min(espera * 2, backoff_max)
            _hidratar(ruta)
    raise LecturaIncompleta(ruta, leidos, esperados, intentos)


def verificar_completo(ruta, **kw) -> None:
    """Como `leer_completo` pero solo comprueba (para pasar la ruta a un conversor)."""
    leer_completo(ruta, **kw)


def copiar_completo(origen, destino, **kw) -> None:
    """`shutil.copy2` verificado: la copia sale de bytes leídos completos."""
    datos = leer_completo(origen, **kw)
    with open(destino, "wb") as f:
        f.write(datos)
    try:
        shutil.copystat(origen, destino)
    except OSError:
        pass
