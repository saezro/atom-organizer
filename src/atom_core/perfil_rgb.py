"""Instrumentación OPT-IN de la fase RGB: mide dónde se va el tiempo por
imagen (lectura, decode, cada encode, thumbnail, escritura) sin cambiar
ningún comportamiento.

Apagada por defecto — activación por la variable de entorno
`ORGANIZER_PERFIL_RGB` (ruta al CSV de salida). Si no está definida, `ACTIVO`
es `False` (se calcula UNA vez al importar el módulo) y `medir`/`medir_imagen`
son context managers no-op: coste cero, ni siquiera un `time.perf_counter()`
de más.

Pensado para correr dentro de un worker de `ProcessPoolExecutor` (cada
proceso hijo reimporta este módulo y relee la env var, igual que hace
`apply.py` con `pipeline`): el acumulador `_acumulado` es una variable de
módulo por PROCESO, nunca compartida entre procesos, y se resetea al
entrar en `medir_imagen` para cada imagen — una imagen por submit (ver
`apply.py:aplicar_rgb`), así que no hay solapamiento.

La escritura a CSV es segura entre procesos: se abre en modo `"ab"`
(append) y se hace UNA sola llamada `write()` con la línea completa
(< 4 KB) seguida de `flush()` — en POSIX un `write()` de ese tamaño en un
fichero abierto en modo append es atómico (no se intercala con el de otro
proceso); en Windows no hay garantía POSIX estricta, pero al ser una única
llamada de escritura pequeña el intercalado de líneas completas es
suficiente para este uso (un CSV de diagnóstico, no un log transaccional).

La cabecera la escribe el proceso PADRE al arrancar la fase
(`escribir_cabecera`), nunca los workers — si cada worker la escribiera se
duplicaría una vez por proceso del pool.
"""
from __future__ import annotations

import contextlib
import csv
import os
import time
from typing import Iterator

#: Ruta al CSV de salida. `None` (env var sin definir) => instrumentación
#: apagada. Se calcula UNA vez al importar el módulo — exactamente lo que
#: pide la tarea: "comprobación de env var cacheada en un módulo, nada más".
_RUTA_CSV = os.environ.get("ORGANIZER_PERFIL_RGB") or None

#: `True` si la instrumentación está activa. Se consulta en caliente en cada
#: `medir`/`medir_imagen`, pero es un simple booleano ya resuelto — no vuelve
#: a tocar `os.environ`.
ACTIVO = _RUTA_CSV is not None

#: Orden de las columnas de "etapa" en el CSV — debe coincidir EXACTO con la
#: cabecera de `_CABECERA` y con los nombres que pasa `apply.py` a `medir()`.
_ETAPAS = ("lectura", "decode", "encode_original", "encode_crop", "thumbnail", "escritura")

_CABECERA = (
    "ts_inicio,ts_fin,pid,nombre,bytes_origen,t_lectura,t_decode,"
    "t_encode_original,t_encode_crop,t_thumbnail,t_escritura,t_total\n"
)

#: Tamaño máximo de una línea del CSV. Una sola `write()` por debajo de este
#: límite es atómica en POSIX (`PIPE_BUF`/página de FS de sobra) y en la
#: práctica también en Windows para una única línea de texto corta.
_MAX_LINEA_BYTES = 4096

#: Acumulador de tiempos por etapa del PROCESO actual — se resetea en cada
#: `medir_imagen()`. No-op (vacío) si `ACTIVO` es `False`.
_acumulado: dict[str, float] = {}


def _reset() -> None:
    _acumulado.clear()


@contextlib.contextmanager
def medir(etapa: str) -> Iterator[None]:
    """Acumula, en `_acumulado[etapa]`, el tiempo transcurrido dentro del
    bloque `with`. No-op de coste cero si la instrumentación está apagada.
    """
    if not ACTIVO:
        yield
        return
    inicio = time.perf_counter()
    try:
        yield
    finally:
        _acumulado[etapa] = _acumulado.get(etapa, 0.0) + (time.perf_counter() - inicio)


@contextlib.contextmanager
def medir_imagen(nombre: str, bytes_origen: int) -> Iterator[None]:
    """Mide el ciclo completo de UNA imagen y, al salir del `with`, emite su
    fila al CSV (`_RUTA_CSV`, modo append). No-op si la instrumentación está
    apagada — ni siquiera resetea el acumulador.
    """
    if not ACTIVO:
        yield
        return
    _reset()
    ts_inicio = time.time()
    t0 = time.perf_counter()
    try:
        yield
    finally:
        t_total = time.perf_counter() - t0
        ts_fin = time.time()
        _emitir_fila(ts_inicio, ts_fin, nombre, bytes_origen, t_total)
        _reset()


def _emitir_fila(ts_inicio: float, ts_fin: float, nombre: str, bytes_origen: int,
                  t_total: float) -> None:
    # El nombre viaja tal cual salvo que contenga el separador: una coma en
    # el nombre de fichero descuadraría las columnas siguientes.
    nombre_seguro = str(nombre).replace(",", "_")
    valores_etapa = ",".join(f"{_acumulado.get(etapa, 0.0):.4f}" for etapa in _ETAPAS)
    linea = (
        f"{ts_inicio:.4f},{ts_fin:.4f},{os.getpid()},{nombre_seguro},{bytes_origen},"
        f"{valores_etapa},{t_total:.4f}\n"
    )
    datos = linea.encode("utf-8", errors="replace")
    if len(datos) >= _MAX_LINEA_BYTES:
        # Defensivo: un nombre de fichero absurdamente largo no debe poder
        # romper la atomicidad de la escritura ni tirar la fila entera.
        exceso = len(datos) - _MAX_LINEA_BYTES + 1
        nombre_seguro = nombre_seguro[: max(0, len(nombre_seguro) - exceso)]
        linea = (
            f"{ts_inicio:.4f},{ts_fin:.4f},{os.getpid()},{nombre_seguro},{bytes_origen},"
            f"{valores_etapa},{t_total:.4f}\n"
        )
        datos = linea.encode("utf-8", errors="replace")
    with open(_RUTA_CSV, "ab") as f:
        f.write(datos)
        f.flush()


def escribir_cabecera() -> None:
    """Escribe (truncando) la cabecera del CSV. Debe llamarla SOLO el
    proceso padre, al arrancar la fase — nunca un worker."""
    if not ACTIVO:
        return
    with open(_RUTA_CSV, "w", encoding="utf-8", newline="") as f:
        f.write(_CABECERA)


def resumen() -> "dict | None":
    """Lee de vuelta el CSV ya cerrado (tras terminar la fase) y calcula la
    media de cada etapa y el nº de imágenes. `None` si la instrumentación
    está apagada o el fichero no tiene filas."""
    if not ACTIVO:
        return None
    try:
        with open(_RUTA_CSV, "r", encoding="utf-8", newline="") as f:
            filas = list(csv.DictReader(f))
    except OSError:
        return None
    if not filas:
        return None
    columnas = ("t_lectura", "t_decode", "t_encode_original", "t_encode_crop",
                "t_thumbnail", "t_escritura", "t_total")
    n = len(filas)
    medias = {
        columna: sum(float(fila[columna]) for fila in filas) / n
        for columna in columnas
    }
    return {"n_imagenes": n, "medias": medias}
