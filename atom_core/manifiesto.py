"""Manifiesto del organizado: una fila por imagen, en SQLite (WAL en disco local).

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

import datetime
import os
import re
import sqlite3
import sys
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

# Sistemas de ficheros donde WAL corrompe la base: su índice `-shm` se comparte
# por mmap entre procesos, y fuse/red no garantizan que todos vean las mismas
# páginas. Caso real (2026-09-11): SSD NTFS por ntfs-3g en la Pi →
# "database disk image is malformed" en la fase RGB con 2+ workers.
_FS_SIN_WAL = ("fuse", "nfs", "cifs", "smb", "9p", "sshfs", "davfs")


def _tipo_fs_linux(ruta: str) -> str:
    """fstype del punto de montaje más largo que contiene `ruta` ('' si no se sabe)."""
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            montajes = [linea.split()[1:3] for linea in f if len(linea.split()) >= 3]
    except OSError:
        return ""
    ruta = os.path.realpath(ruta)
    mejor, tipo = "", ""
    for punto, fs in montajes:
        punto = punto.replace("\\040", " ")
        if (ruta == punto or ruta.startswith(punto.rstrip("/") + "/")) and len(punto) >= len(mejor):
            mejor, tipo = punto, fs
    return tipo


def _es_unidad_red_windows(ruta: str) -> bool:
    ruta = os.path.abspath(ruta)
    if ruta.startswith("\\\\"):
        return True
    try:
        import ctypes
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(ruta)[0] + "\\") == DRIVE_REMOTE
    except (AttributeError, OSError):
        return False


def modo_journal(ruta_db: str | Path) -> str:
    """'WAL' en disco local; 'DELETE' donde WAL no es seguro (fuse, red)."""
    carpeta = os.path.dirname(os.path.abspath(str(ruta_db)))
    if sys.platform == "win32":
        return "DELETE" if _es_unidad_red_windows(carpeta) else "WAL"
    tipo = _tipo_fs_linux(carpeta)
    return "DELETE" if any(tipo.startswith(p) for p in _FS_SIN_WAL) else "WAL"


_SENTENCIAS_ESQUEMA = (
    """
    CREATE TABLE IF NOT EXISTS imagenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Última ruta desde la que se vio la imagen. NO es única: la misma
        -- imagen puede llegar desde otra SD u otro punto de montaje en un
        -- cachito posterior; la identidad es `clave`.
        ruta_origen TEXT NOT NULL,
        nombre_original TEXT NOT NULL DEFAULT '',
        -- nombre|timestamp_exif|bytes_origen (ver `clave_imagen`).
        clave TEXT NOT NULL,
        tipo TEXT NOT NULL,
        timestamp_exif TEXT,
        modelo TEXT,
        -- EXIF `Image Make`. Con `modelo`, para comprobar el dron del estadillo.
        make TEXT,
        pb TEXT,
        vuelo TEXT,
        -- `Equipo_de_vuelo` del estadillo para ese vuelo, tal cual (texto libre).
        -- El Sí/No no se guarda: se deriva con `atom_core.equipo` al escribir el Excel.
        equipo_estadillo TEXT,
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
        verificacion TEXT,
        -- 1 si el índice leyó la posición de esta imagen (aunque viniera sin
        -- GPS). 0 en filas migradas o de `gs://…`: el cierre relee su salida.
        meta_leida INTEGER NOT NULL DEFAULT 0,
        lat REAL,
        lon REAL,
        -- Texto crudo del XMP DJI: es lo que entra tal cual en meta/location.
        altitud_abs TEXT,
        altura_relativa TEXT,
        gimbal_yaw TEXT,
        gimbal_pitch TEXT,
        gimbal_roll TEXT,
        flight_yaw TEXT,
        ancho_px INTEGER,
        alto_px INTEGER,
        ejecucion_id INTEGER
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_clave ON imagenes(clave)",
    "CREATE INDEX IF NOT EXISTS idx_estado ON imagenes(estado)",
    "CREATE INDEX IF NOT EXISTS idx_vuelo ON imagenes(pb, vuelo)",
    """
    CREATE TABLE IF NOT EXISTS ejecuciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inicio TEXT NOT NULL,
        fin TEXT,
        origen TEXT NOT NULL DEFAULT '',
        version_app TEXT NOT NULL DEFAULT '',
        n_nuevas INTEGER NOT NULL DEFAULT 0,
        n_saltadas INTEGER NOT NULL DEFAULT 0,
        n_reintentadas INTEGER NOT NULL DEFAULT 0
    )
    """,
)


def nombre_de_ruta(ruta: str) -> str:
    """Nombre de fichero de una ruta local (Windows o POSIX) o `gs://…`.
    `os.path.basename` en Linux no parte por `\\`, y el manifiesto puede
    venir de un run hecho en Windows."""
    return re.split(r"[\\/]", ruta or "")[-1]


def clave_imagen(ruta_origen: str, timestamp_exif: str | None, bytes_origen: int | None) -> str:
    """Identidad de una imagen independiente de dónde esté montada.

    Nombre + segundo EXIF no basta: dos drones volando a la vez pueden sacar
    `DJI_0001.JPG` en el mismo segundo. El tamaño lo desempata, y una copia
    de la misma imagen desde otra SD pesa exactamente lo mismo."""
    return f"{nombre_de_ruta(ruta_origen)}|{timestamp_exif or ''}|{int(bytes_origen or 0)}"


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
    # Posición leída en el índice (ver `indice.campos_posicion`). Con default
    # por lo mismo que `bytes_origen`: los llamadores viejos siguen valiendo.
    meta_leida: bool = False
    lat: float | None = None
    lon: float | None = None
    altitud_abs: str | None = None
    altura_relativa: str | None = None
    gimbal_yaw: str | None = None
    gimbal_pitch: str | None = None
    gimbal_roll: str | None = None
    flight_yaw: str | None = None
    ancho_px: int | None = None
    alto_px: int | None = None
    # Equipo: EXIF `Image Make` y `Equipo_de_vuelo` del estadillo (ver `atom_core.equipo`).
    make: str | None = None
    equipo_estadillo: str | None = None


@dataclass
class ResultadoInsercion:
    nuevas: int
    saltadas: int
    reintentadas: int
    # (pb, vuelo) de las imágenes saltadas, sin repetir y en orden de aparición.
    vuelos_saltados: list[tuple[str, str]]


class Manifiesto:
    """Acceso al manifiesto. Una instancia por proceso; internamente usa una
    conexión por hilo porque los objetos de sqlite3 no son compartibles entre
    hilos."""

    def __init__(self, ruta_db: str | Path) -> None:
        self.ruta_db = str(ruta_db)
        self._local = threading.local()
        # `cerrar()` puede llamarse desde un hilo distinto al de los workers
        # (apply.py escribe el manifiesto desde varios hilos de un
        # ThreadPoolExecutor). Como la conexión es thread-local, cerrar()
        # solo veía la suya propia y dejaba las de los workers abiertas hasta
        # que el GC las recogiera, lo que dejaba -wal/-shm huérfanos en el
        # destino. Aquí se registran TODAS las conexiones abiertas para
        # poder cerrarlas explícitamente desde donde sea.
        self._conexiones_abiertas: list[sqlite3.Connection] = []
        self._lock_conexiones = threading.Lock()
        # Generación: `cerrar()` la incrementa. Si un hilo guarda en su local
        # una conexión de una generación anterior (porque `cerrar()` se llamó
        # mientras ese hilo dormía, o el manifiesto se reutiliza tras
        # cerrarlo), `_conexion()` la descarta y reconecta en vez de devolver
        # una conexión ya cerrada.
        self._generacion = 0

    def _conexion(self) -> sqlite3.Connection:
        conexion = getattr(self._local, "conexion", None)
        if conexion is not None and getattr(self._local, "generacion", None) != self._generacion:
            conexion = None
        if conexion is None:
            # check_same_thread=False: cada conexión sigue usándose solo desde
            # su hilo salvo en `cerrar()`, que las cierra todas desde el hilo
            # que cierra el manifiesto (normalmente ya no coincide con el de
            # los workers, que han terminado para entonces).
            conexion = sqlite3.connect(self.ruta_db, timeout=30.0, check_same_thread=False)
            conexion.row_factory = sqlite3.Row
            # WAL: lectores y escritor conviven. busy_timeout evita que dos
            # workers que coinciden en el mismo instante aborten con
            # "database is locked" en vez de esperar su turno. En disco sin
            # memoria compartida fiable (fuse, red) cae a DELETE.
            conexion.execute(f"PRAGMA journal_mode={modo_journal(self.ruta_db)}")
            conexion.execute("PRAGMA busy_timeout=30000")
            conexion.execute("PRAGMA synchronous=NORMAL")
            self._local.conexion = conexion
            with self._lock_conexiones:
                self._local.generacion = self._generacion
                self._conexiones_abiertas.append(conexion)
        return conexion

    def crear_esquema(self) -> None:
        conexion = self._conexion()
        if self._existe_tabla(conexion, "imagenes"):
            self._migrar_columnas(conexion)
            conexion.commit()
            if "clave" not in self._columnas(conexion):
                self._recrear_con_clave(conexion)
        for sentencia in _SENTENCIAS_ESQUEMA:
            conexion.execute(sentencia)
        conexion.commit()

    @staticmethod
    def _existe_tabla(conexion: sqlite3.Connection, nombre: str) -> bool:
        return conexion.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (nombre,)
        ).fetchone() is not None

    @staticmethod
    def _columnas(conexion: sqlite3.Connection) -> list[str]:
        return [fila["name"] for fila in conexion.execute("PRAGMA table_info(imagenes)")]

    def _migrar_columnas(self, conexion: sqlite3.Connection) -> None:
        """Añade las columnas que un manifiesto de una versión anterior no
        tiene. `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe,
        así que reanudar un run empezado con una versión vieja del Organizer
        se quedaría sin las columnas nuevas y reventaría al insertar."""
        existentes = set(self._columnas(conexion))
        for nombre, definicion in (("bytes_origen", "INTEGER NOT NULL DEFAULT 0"),):
            if nombre not in existentes:
                conexion.execute(f"ALTER TABLE imagenes ADD COLUMN {nombre} {definicion}")

    def _recrear_con_clave(self, conexion: sqlite3.Connection) -> None:
        """Manifiesto anterior a la acumulación: `ruta_origen` era UNIQUE y no
        había `clave`. SQLite no quita un UNIQUE con ALTER, así que se recrea
        la tabla en UNA transacción: o migra entera o se queda como estaba."""
        viejas = [c for c in self._columnas(conexion) if c != "id"]
        lista = ", ".join(viejas)
        conexion.create_function("clave_imagen", 3, clave_imagen, deterministic=True)
        conexion.create_function("nombre_de_ruta", 1, nombre_de_ruta, deterministic=True)
        try:
            conexion.execute("BEGIN IMMEDIATE")
            conexion.execute("ALTER TABLE imagenes RENAME TO imagenes_v1")
            conexion.execute("DROP INDEX IF EXISTS idx_estado")
            conexion.execute("DROP INDEX IF EXISTS idx_vuelo")
            for sentencia in _SENTENCIAS_ESQUEMA:
                conexion.execute(sentencia)
            conexion.execute(
                f"INSERT OR IGNORE INTO imagenes (id, {lista}, nombre_original, clave) "
                f"SELECT id, {lista}, nombre_de_ruta(ruta_origen), "
                f"clave_imagen(ruta_origen, timestamp_exif, bytes_origen) "
                f"FROM imagenes_v1 ORDER BY id")
            conexion.execute("DROP TABLE imagenes_v1")
            conexion.commit()
        except Exception:
            conexion.rollback()
            raise

    def insertar_o_reabrir(self, filas: Iterable[FilaManifiesto],
                           ejecucion_id: int | None = None) -> ResultadoInsercion:
        """Inserta lo nuevo y decide qué hacer con lo que ya estaba.

        - clave nueva -> fila nueva.
        - clave `hecho` -> no se toca: ya está organizada en este destino.
        - clave `fallido`/`pendiente`/`en_curso` -> se reescribe con la decisión
          y la ruta de ESTE run y vuelve a `pendiente`.
        """
        nombres = [campo.name for campo in fields(FilaManifiesto)]
        columnas = nombres + ["nombre_original", "clave", "ejecucion_id"]
        lista = ", ".join(columnas)
        marcadores = ", ".join(f":{c}" for c in columnas)
        asignaciones = ", ".join(f"{c} = :{c}" for c in columnas if c != "clave")
        conexion = self._conexion()
        nuevas = saltadas = reintentadas = 0
        vuelos_saltados: list[tuple[str, str]] = []
        with conexion:
            for fila in filas:
                datos = {nombre: getattr(fila, nombre) for nombre in nombres}
                for booleano in ("comprime", "unassigned", "meta_leida"):
                    datos[booleano] = int(datos[booleano])
                datos["nombre_original"] = nombre_de_ruta(fila.ruta_origen)
                datos["clave"] = clave_imagen(fila.ruta_origen, fila.timestamp_exif, fila.bytes_origen)
                datos["ejecucion_id"] = ejecucion_id
                previa = conexion.execute(
                    "SELECT id, estado, pb, vuelo FROM imagenes WHERE clave = ?",
                    (datos["clave"],)).fetchone()
                if previa is None:
                    conexion.execute(f"INSERT INTO imagenes ({lista}) VALUES ({marcadores})", datos)
                    nuevas += 1
                elif previa["estado"] == "hecho":
                    saltadas += 1
                    vuelo = (previa["pb"], previa["vuelo"])
                    if previa["pb"] and previa["vuelo"] and vuelo not in vuelos_saltados:
                        vuelos_saltados.append(vuelo)
                else:
                    conexion.execute(
                        f"UPDATE imagenes SET {asignaciones}, estado = 'pendiente', "
                        f"motivo_fallo = NULL WHERE id = :id_previa",
                        dict(datos, id_previa=previa["id"]))
                    reintentadas += 1
        return ResultadoInsercion(nuevas, saltadas, reintentadas, vuelos_saltados)

    def insertar_muchas(self, filas: Iterable[FilaManifiesto]) -> int:
        """Compatibilidad: cuántas filas NUEVAS entraron."""
        return self.insertar_o_reabrir(filas).nuevas

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
        # El apply ya NO lo llama: la marca no protege nada al reanudar
        # (`reabrir_huerfanas` la devuelve a 'pendiente') y costaba un commit
        # por imagen (~99 ms en la Pi, bench KL19 2026-09-13). Se conserva por
        # manifiestos de runs anteriores que aún tengan filas 'en_curso'.
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

    def angulos_por_vuelo(self) -> dict[tuple[str, str], int]:
        """Ángulo ya decidido para cada vuelo del destino. Un cachito posterior
        del mismo vuelo DEBE reutilizarlo: si no, JPG y TIFF del mismo vuelo
        podrían salir con giros distintos entre cachitos."""
        return {
            (fila["pb"], fila["vuelo"]): fila["angulo_giro"]
            for fila in self._conexion().execute(
                "SELECT pb, vuelo, angulo_giro FROM imagenes WHERE id IN ("
                "SELECT MIN(id) FROM imagenes WHERE pb IS NOT NULL AND vuelo IS NOT NULL "
                "AND unassigned = 0 GROUP BY pb, vuelo)")
        }

    def abrir_ejecucion(self, origen: str, version_app: str) -> int:
        conexion = self._conexion()
        with conexion:
            cursor = conexion.execute(
                "INSERT INTO ejecuciones (inicio, origen, version_app) VALUES (?, ?, ?)",
                (datetime.datetime.now().isoformat(timespec="seconds"), origen or "", version_app or ""))
        return int(cursor.lastrowid)

    def cerrar_ejecucion(self, id_ejecucion: int, n_nuevas: int, n_saltadas: int,
                         n_reintentadas: int) -> None:
        conexion = self._conexion()
        with conexion:
            conexion.execute(
                "UPDATE ejecuciones SET fin = ?, n_nuevas = ?, n_saltadas = ?, n_reintentadas = ? "
                "WHERE id = ?",
                (datetime.datetime.now().isoformat(timespec="seconds"),
                 n_nuevas, n_saltadas, n_reintentadas, id_ejecucion))

    def ejecuciones(self) -> dict[int, sqlite3.Row]:
        return {fila["id"]: fila for fila in
                self._conexion().execute("SELECT * FROM ejecuciones ORDER BY id")}

    def todas(self) -> list[sqlite3.Row]:
        return list(self._conexion().execute("SELECT * FROM imagenes ORDER BY id"))

    def colisiones_ruta_salida_original(self) -> list[sqlite3.Row]:
        """Destinos (`ruta_salida_original`) en los que dos o más imágenes de
        ORIGEN distinto resuelven al MISMO fichero final.

        `clave` es UNIQUE (arriba), pero nada obliga a que
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
        """Cierra TODAS las conexiones abiertas por esta instancia (una por
        hilo), no solo la del hilo que llama. Antes de cerrar, vuelca el WAL
        a la base principal (`TRUNCATE`) para no dejar `-wal`/`-shm`
        colgando en el destino tras el último cierre del run."""
        with self._lock_conexiones:
            conexiones = self._conexiones_abiertas
            self._conexiones_abiertas = []
            self._generacion += 1
        for conexion in conexiones:
            try:
                conexion.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            conexion.close()
        self._local.conexion = None
