"""Manifiesto del organizado: una fila por imagen, en SQLite modo WAL.

Es la memoria del run. El índice lo llena decidiendo qué hacer con cada
imagen; el apply lo recorre escribiendo y marcando estado desde varios
workers a la vez; el cierre emite los CSV y verifica desde aquí, no desde
contadores acumulados fase a fase.

Por qué SQLite y no un JSONL: el apply actualiza filas concurrentemente y
necesita preguntar "qué queda pendiente" sin releer el fichero entero. WAL
permite que varios lectores y un escritor convivan sin bloquear la base
completa, que es lo que hace un journal clásico.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Iterable

ESTADOS = ("pendiente", "en_curso", "hecho", "fallido")

# Carpeta donde el organizado deja su manifiesto, colgando de `output_folder`.
# Vive dentro del destino a propósito (es lo que permite reanudar un run muerto
# sobre la MISMA salida), así que todo lo que recorra o cuente el árbol
# entregado tiene que ignorarla: no es una imagen, es fontanería del motor.
NOMBRE_CARPETA_MANIFIESTO = ".organizado"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS imagenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ruta_origen TEXT NOT NULL UNIQUE,
    tipo TEXT NOT NULL,
    timestamp_exif TEXT,
    modelo TEXT,
    pb TEXT,
    vuelo TEXT,
    nombre_nuevo TEXT NOT NULL DEFAULT '',
    angulo_giro INTEGER NOT NULL DEFAULT 0,
    pct_recorte REAL,
    comprime INTEGER NOT NULL DEFAULT 0,
    ruta_salida_original TEXT NOT NULL,
    ruta_salida_crop TEXT,
    ruta_salida_tiff TEXT,
    unassigned INTEGER NOT NULL DEFAULT 0,
    -- Tamaño del fichero de ORIGEN, en bytes, tal y como lo vio el índice.
    -- Se guarda al indexar y no al terminar a propósito: para cuando el run
    -- acaba, el original puede haberse movido o borrado, y entonces ya no hay
    -- forma de saber cuánto pesaba lo que entró.
    bytes_origen INTEGER NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    motivo_fallo TEXT,
    verificacion TEXT
);
CREATE INDEX IF NOT EXISTS idx_estado ON imagenes(estado);
CREATE INDEX IF NOT EXISTS idx_vuelo ON imagenes(pb, vuelo);
"""


def _sumar_verificacion(verificacion: str | None) -> int:
    """Suma los tamaños de `"ruta:tamaño; ruta:tamaño"`, el formato que deja
    el apply al marcar una fila hecha. Tolerante a propósito: una entrada
    ilegible se salta en vez de tumbar el resumen final de un run que por lo
    demás fue bien. OJO con `rsplit`: en Windows las rutas llevan `C:\\…`, y
    partir por el PRIMER `:` daría la letra de unidad como tamaño."""
    if not verificacion:
        return 0
    total = 0
    for entrada in verificacion.split("; "):
        ruta_y_tamano = entrada.rsplit(":", 1)
        if len(ruta_y_tamano) != 2:
            continue
        try:
            total += int(ruta_y_tamano[1])
        except ValueError:
            continue
    return total


@dataclass(frozen=True)
class FilaManifiesto:
    """Lo que el índice decide sobre una imagen. Inmutable a propósito: una vez
    decidido, el apply no improvisa."""

    ruta_origen: str
    tipo: str
    timestamp_exif: str | None
    modelo: str | None
    pb: str | None
    vuelo: str | None
    nombre_nuevo: str
    angulo_giro: int
    pct_recorte: float | None
    comprime: bool
    ruta_salida_original: str
    ruta_salida_crop: str | None
    ruta_salida_tiff: str | None
    unassigned: bool
    # Va con default porque se añadió después: los tests y llamadores que
    # construyen filas a mano siguen valiendo, y una fila sin tamaño suma 0
    # al balance en vez de reventarlo.
    bytes_origen: int = 0


class Manifiesto:
    """Acceso al manifiesto. Una instancia por proceso; internamente usa una
    conexión por hilo porque los objetos de sqlite3 no son compartibles entre
    hilos."""

    def __init__(self, ruta_db: str | Path) -> None:
        self.ruta_db = str(ruta_db)
        self._local = threading.local()

    def _conexion(self) -> sqlite3.Connection:
        conexion = getattr(self._local, "conexion", None)
        if conexion is None:
            conexion = sqlite3.connect(self.ruta_db, timeout=30.0)
            conexion.row_factory = sqlite3.Row
            # WAL: lectores y escritor conviven. busy_timeout evita que dos
            # workers que coinciden en el mismo instante aborten con
            # "database is locked" en vez de esperar su turno.
            conexion.execute("PRAGMA journal_mode=WAL")
            conexion.execute("PRAGMA busy_timeout=30000")
            conexion.execute("PRAGMA synchronous=NORMAL")
            self._local.conexion = conexion
        return conexion

    def crear_esquema(self) -> None:
        conexion = self._conexion()
        conexion.executescript(_ESQUEMA)
        self._migrar_columnas(conexion)
        conexion.commit()

    def _migrar_columnas(self, conexion: sqlite3.Connection) -> None:
        """Añade las columnas que un manifiesto de una versión anterior no
        tiene. `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe,
        así que reanudar un run empezado con una versión vieja del Organizer
        se quedaría sin las columnas nuevas y reventaría al insertar."""
        existentes = {fila["name"] for fila in
                      conexion.execute("PRAGMA table_info(imagenes)")}
        for nombre, definicion in (("bytes_origen", "INTEGER NOT NULL DEFAULT 0"),):
            if nombre not in existentes:
                conexion.execute(f"ALTER TABLE imagenes ADD COLUMN {nombre} {definicion}")

    def insertar_muchas(self, filas: Iterable[FilaManifiesto]) -> int:
        nombres = [campo.name for campo in fields(FilaManifiesto)]
        columnas = ", ".join(nombres)
        marcadores = ", ".join(f":{nombre}" for nombre in nombres)
        conexion = self._conexion()
        insertadas = 0
        with conexion:
            for fila in filas:
                datos = {nombre: getattr(fila, nombre) for nombre in nombres}
                datos["comprime"] = int(datos["comprime"])
                datos["unassigned"] = int(datos["unassigned"])
                cursor = conexion.execute(
                    f"INSERT OR IGNORE INTO imagenes ({columnas}) VALUES ({marcadores})",
                    datos,
                )
                insertadas += cursor.rowcount
        return insertadas

    def pendientes(self, limite: int | None = None) -> list[sqlite3.Row]:
        consulta = "SELECT * FROM imagenes WHERE estado = 'pendiente' ORDER BY id"
        if limite is not None:
            consulta += f" LIMIT {int(limite)}"
        return list(self._conexion().execute(consulta))

    def balance_bytes(self) -> dict:
        """Cuánto entró y cuánto se entregó, en bytes, de las filas `hecho`.

        NO es lo mismo que los `mb_leidos`/`mb_escritos` del medidor de
        recursos: aquellos son el I/O de disco de toda la máquina (un mover
        dentro del mismo disco no escribe un solo byte, y el mismo origen se
        lee varias veces). Esto es el dato que se pregunta de verdad —
        cuánto ocupa la entrega frente al material de partida—, y sale de
        tamaños ya grabados al escribir cada fichero: cero I/O extra, y en
        `gs://…` cero llamadas al bucket.
        """
        entrada = 0
        salida = 0
        imagenes = 0
        for fila in self._conexion().execute(
                "SELECT bytes_origen, verificacion FROM imagenes WHERE estado = 'hecho'"):
            imagenes += 1
            entrada += fila["bytes_origen"] or 0
            salida += _sumar_verificacion(fila["verificacion"])
        return {"entrada": entrada, "salida": salida, "imagenes": imagenes}

    def marcar_en_curso(self, id_fila: int) -> None:
        self._actualizar(id_fila, estado="en_curso")

    def marcar_hecha(self, id_fila: int, verificacion: str) -> None:
        self._actualizar(id_fila, estado="hecho", verificacion=verificacion,
                         motivo_fallo=None)

    def marcar_fallida(self, id_fila: int, motivo: str) -> None:
        self._actualizar(id_fila, estado="fallido", motivo_fallo=motivo)

    def _actualizar(self, id_fila: int, **campos) -> None:
        asignaciones = ", ".join(f"{nombre} = :{nombre}" for nombre in campos)
        parametros = dict(campos, id_fila=id_fila)
        conexion = self._conexion()
        with conexion:
            conexion.execute(
                f"UPDATE imagenes SET {asignaciones} WHERE id = :id_fila", parametros
            )

    def reabrir_huerfanas(self) -> int:
        """Devuelve a 'pendiente' las filas que quedaron 'en_curso' porque el
        proceso murió a mitad. Sin esto, un run interrumpido deja imágenes que
        nadie vuelve a mirar."""
        conexion = self._conexion()
        with conexion:
            cursor = conexion.execute(
                "UPDATE imagenes SET estado = 'pendiente' WHERE estado = 'en_curso'"
            )
        return cursor.rowcount

    def resumen(self) -> dict[str, int]:
        conteos = {estado: 0 for estado in ESTADOS}
        for fila in self._conexion().execute(
            "SELECT estado, COUNT(*) AS total FROM imagenes GROUP BY estado"
        ):
            conteos[fila["estado"]] = fila["total"]
        return conteos

    def filas_por_vuelo(self, pb: str, vuelo: str) -> list[sqlite3.Row]:
        return list(
            self._conexion().execute(
                "SELECT * FROM imagenes WHERE pb = ? AND vuelo = ? ORDER BY id",
                (pb, vuelo),
            )
        )

    def todas(self) -> list[sqlite3.Row]:
        return list(self._conexion().execute("SELECT * FROM imagenes ORDER BY id"))

    def colisiones_ruta_salida_original(self) -> list[sqlite3.Row]:
        """Destinos (`ruta_salida_original`) en los que dos o más imágenes de
        ORIGEN distinto resuelven al MISMO fichero final.

        `ruta_origen` es UNIQUE (arriba), pero nada obliga a que
        `ruta_salida_original` lo sea: dos imágenes distintas pueden acabar
        con el mismo nombre calculado. El `os.replace` final de `apply.py` es
        atómico pero silencioso -- la segunda escritura pisa a la primera sin
        error ni aviso -- así que esta es la única forma de que el run se dé
        cuenta y avise. Una sola consulta agregada al cerrar el organizado,
        no una comprobación por fila en el bucle caliente del apply.

        Devuelve filas `(ruta_salida_original, total)` para cada destino con
        más de una imagen apuntando a él; lista vacía si no hay colisiones.
        """
        return list(
            self._conexion().execute(
                "SELECT ruta_salida_original, COUNT(*) AS total FROM imagenes "
                "GROUP BY ruta_salida_original HAVING COUNT(*) > 1 "
                "ORDER BY ruta_salida_original"
            )
        )

    def cerrar(self) -> None:
        conexion = getattr(self._local, "conexion", None)
        if conexion is not None:
            conexion.close()
            self._local.conexion = None
