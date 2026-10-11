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
import logging
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

NOMBRE_FICHERO_MANIFIESTO = "manifiesto.db"


def destino_organizado(carpeta: str | Path) -> bool:
    """¿`carpeta` es un destino ya organizado por el Organizer (apto para
    añadir otra tanda)? Exige `.organizado/manifiesto.db` que SQLite abra en
    solo lectura y que (a) tenga la tabla `imagenes`, o (b) no tenga ninguna
    tabla / mida 0 bytes (crash entre crear el fichero y `crear_esquema`: se
    reanuda). Una DB bloqueada u ocupada por otro run es nuestra (el lock del
    destino decide). Corrupta o con tablas ajenas: NO. Regla única compartida
    por el guard de `organize.run_task` y por `folder_is_empty` de la GUI."""
    ruta = Path(carpeta) / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    if not ruta.is_file():
        return False
    try:
        if ruta.stat().st_size == 0:
            return True
        con = sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True, timeout=1.0)
        try:
            tablas = {f[0] for f in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            return "imagenes" in tablas or not tablas
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        mensaje = str(exc).lower()
        return "locked" in mensaje or "busy" in mensaje
    except (sqlite3.Error, OSError, ValueError):
        return False


def _clave_origen(ruta: str) -> str:
    return os.path.normcase(os.path.normpath(str(ruta or "")))


def _leer_fallidas_y_origenes(ruta: Path, con: sqlite3.Connection) -> tuple[int, set]:
    try:
        fallidas = con.execute(
            "SELECT COUNT(*) FROM imagenes WHERE estado = 'fallido'").fetchone()[0]
        origenes = {_clave_origen(f[0]) for f in con.execute(
            "SELECT origen FROM ejecuciones")}
    finally:
        con.close()
    return int(fallidas), origenes


def fallidas_reintentables(carpeta: str | Path, origen: str) -> dict:
    """Solo lectura: `{"fallidas": N, "mismo_origen": bool}` del destino
    `carpeta`. `mismo_origen` = alguna ejecución del manifiesto se lanzó con
    ese `origen` (comparado con la ruta normalizada). Sin manifiesto: `{"fallidas": 0,
    "mismo_origen": False}`. Si el manifiesto existe pero NO se puede leer (ni con
    `mode=ro` ni con una conexión normal de solo SELECT, p. ej. WAL sin `-shm`),
    se añade `"error"` con el motivo: la UI lo muestra, el botón no desaparece
    en silencio. Base de "Reintentar fallidas" (GUI y guard de `organize.run_task`)."""
    nada = {"fallidas": 0, "mismo_origen": False}
    if not origen or not destino_organizado(carpeta):
        return nada
    ruta = Path(carpeta) / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    try:
        fallidas, origenes = _leer_fallidas_y_origenes(
            ruta, sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True, timeout=1.0))
    except (sqlite3.Error, OSError, ValueError):
        try:
            # WAL sin -shm: `mode=ro` no puede crearlo. Conexión normal, solo SELECT.
            con = sqlite3.connect(str(ruta), timeout=5.0)
            con.execute("PRAGMA query_only=ON")
            fallidas, origenes = _leer_fallidas_y_origenes(ruta, con)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return dict(nada, error=f"no se pudo leer el manifiesto: {type(exc).__name__}: {exc}")
    return {"fallidas": fallidas, "mismo_origen": _clave_origen(origen) in origenes}


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
        ejecucion_id INTEGER,
        -- Rutas (original/crop/tiff, separadas por salto de línea) que esta imagen tenía en
        -- SIN_ORDENAR cuando se reabrió porque ahora sí tiene vuelo. Se borran
        -- SOLO cuando la escritura nueva acaba bien (`marcar_hecha`).
        previas_sin_ordenar TEXT,
        -- VERSION_ALGORITMO_GIRO con la que el índice calculó `angulo_giro`. NULL
        -- en manifiestos antiguos: ese ángulo no se respeta al reprocesar.
        angulo_version TEXT,
        -- Cola del JPG vista por el índice: 1 = sin EOI útil (posible truncado en
        -- origen), 0 = con EOI, NULL = no comprobada (no JPG, gs://, manifiesto
        -- antiguo). Solo marca: el descarte lo decide el decode del apply.
        jpeg_sin_eoi INTEGER,
        -- Voto de giro de ESTA imagen según su yaw (90 / 270 / 0). Es el voto
        -- individual; el ángulo aplicado es el del consenso del vuelo (`vuelos`).
        giro_voto INTEGER
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
    # Consenso de giro por vuelo (reparto de votos). Fuente de verdad del criterio:
    # el CSV `_criterio/*_Videofiles.csv` es solo artefacto de compatibilidad.
    # `sufijo` = '' cuando el vuelo no colisiona.
    """
    CREATE TABLE IF NOT EXISTS vuelos (
        pb TEXT NOT NULL,
        vuelo TEXT NOT NULL,
        sufijo TEXT NOT NULL DEFAULT '',
        angulo INTEGER NOT NULL DEFAULT 0,
        n_total INTEGER NOT NULL DEFAULT 0,
        n_90 INTEGER NOT NULL DEFAULT 0,
        n_270 INTEGER NOT NULL DEFAULT 0,
        n_0 INTEGER NOT NULL DEFAULT 0,
        pct_90 REAL,
        pct_270 REAL,
        pct_0 REAL,
        pct_ganadora REAL,
        estado TEXT NOT NULL DEFAULT 'ok',
        motivo TEXT,
        version_algoritmo TEXT,
        PRIMARY KEY (pb, vuelo, sufijo)
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


_COLUMNAS_VUELOS = ("pb", "vuelo", "sufijo", "angulo", "n_total", "n_90", "n_270", "n_0",
                    "pct_90", "pct_270", "pct_0", "pct_ganadora", "estado", "motivo",
                    "version_algoritmo")


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
    # Versión del algoritmo de giro que calculó `angulo_giro` (ver indice.VERSION_ALGORITMO_GIRO).
    angulo_version: str | None = None
    # El índice vio la cola del JPG sin EOI útil (ver `indice._leer_cabecera`).
    # True = sin EOI útil, False = con EOI, None = no comprobada.
    jpeg_sin_eoi: bool | None = None
    # Voto de giro individual de la imagen (90 / 270 / 0); None = sin voto (sin yaw,
    # planta Horizontal, modo manual, fotos generales o sin asignar).
    giro_voto: int | None = None


@dataclass
class ResultadoInsercion:
    nuevas: int
    saltadas: int
    reintentadas: int
    # (pb, vuelo) de las imágenes saltadas, sin repetir y en orden de aparición.
    vuelos_saltados: list[tuple[str, str]]
    # Fotos `hecho` que estaban sin asignar (SIN_ORDENAR) y que esta tanda sí
    # asigna: se reabren con su nueva ruta. `siguen_sin_asignar`: hechas sin
    # asignar que siguen sin ventana en esta tanda.
    reasignadas: int = 0
    siguen_sin_asignar: int = 0
    # Fotos reabiertas por (nombre + EXIF + bytes) porque su ruta de origen
    # cambió entre tandas (p. ej. Drive -> bucket).
    reabiertas_por_nombre: int = 0
    # Fotos con >1 candidato por nombre+EXIF+bytes: no se emparejaron (se
    # insertaron como nuevas) y se avisó. Una línea de texto por foto.
    ambiguas: tuple = ()
    # Borrados pendientes de tandas anteriores que se reintentaron / siguen sin
    # poder borrarse.
    borrados_reintentados: int = 0
    borrados_pendientes: int = 0


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
        for nombre, definicion in (("bytes_origen", "INTEGER NOT NULL DEFAULT 0"),
                                  ("previas_sin_ordenar", "TEXT"),
                                  ("angulo_version", "TEXT"),
                                  ("jpeg_sin_eoi", "INTEGER"),
                                  ("giro_voto", "INTEGER")):
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
                           ejecucion_id: int | None = None,
                           vuelos: Iterable[dict] | None = None) -> ResultadoInsercion:
        """Inserta lo nuevo y decide qué hacer con lo que ya estaba.

        - clave nueva -> fila nueva.
        - clave `hecho` -> no se toca: ya está organizada en este destino.
          Excepción: estaba `unassigned` (SIN_ORDENAR) y ahora sí tiene
          pb/vuelo -> se reabre con su ruta nueva y se apuntan las rutas
          viejas para borrarlas cuando la escritura nueva vaya bien.
        - clave `fallido`/`pendiente`/`en_curso` -> se reescribe con la decisión
          y la ruta de ESTE run y vuelve a `pendiente`.

        `vuelos` (reparto de giro, ver `guardar_vuelos`) se escribe en la MISMA
        transacción que las filas.
        """
        nombres = [campo.name for campo in fields(FilaManifiesto)]
        columnas = nombres + ["nombre_original", "clave", "ejecucion_id"]
        lista = ", ".join(columnas)
        marcadores = ", ".join(f":{c}" for c in columnas)
        asignaciones = ", ".join(f"{c} = :{c}" for c in columnas if c != "clave")
        conexion = self._conexion()
        nuevas = saltadas = reintentadas = reasignadas = siguen_sin_asignar = 0
        reabiertas_por_nombre = 0
        reclamadas: set[int] = set()   # filas ya emparejadas por nombre en ESTA llamada
        ambiguas: list[str] = []
        vuelos_saltados: list[tuple[str, str]] = []
        # Borrados que fallaron en una tanda anterior: se reintentan ahora.
        borrados_reintentados, borrados_pendientes = self.reintentar_borrados_pendientes()
        with conexion:
            for fila in filas:
                datos = {nombre: getattr(fila, nombre) for nombre in nombres}
                for booleano in ("comprime", "unassigned", "meta_leida"):
                    datos[booleano] = int(datos[booleano])
                if datos["jpeg_sin_eoi"] is not None:
                    datos["jpeg_sin_eoi"] = int(datos["jpeg_sin_eoi"])
                datos["nombre_original"] = nombre_de_ruta(fila.ruta_origen)
                datos["clave"] = clave_imagen(fila.ruta_origen, fila.timestamp_exif, fila.bytes_origen)
                datos["ejecucion_id"] = ejecucion_id
                previa = conexion.execute(
                    "SELECT id, estado, pb, vuelo, unassigned, ruta_salida_original, "
                    "ruta_salida_crop, ruta_salida_tiff FROM imagenes WHERE clave = ?",
                    (datos["clave"],)).fetchone()
                por_nombre = False
                if previa is None and datos["timestamp_exif"]:
                    # Misma foto con otra ruta de origen (Drive -> bucket...):
                    # nombre + EXIF + bytes, ignorando la ruta, y solo contra
                    # filas de tandas ANTERIORES (dentro de una tanda dos
                    # ficheros iguales de dos SD siguen siendo dos filas).
                    candidatas = conexion.execute(
                        "SELECT id, estado, pb, vuelo, unassigned, ruta_salida_original, "
                        "ruta_salida_crop, ruta_salida_tiff, ruta_origen FROM imagenes "
                        "WHERE nombre_original = ? AND timestamp_exif = ? AND bytes_origen = ? "
                        "AND (? IS NULL OR ejecucion_id IS NULL OR ejecucion_id != ?)",
                        (datos["nombre_original"], datos["timestamp_exif"],
                         int(datos["bytes_origen"] or 0), ejecucion_id, ejecucion_id)).fetchall()
                    candidatas = [c for c in candidatas if c["id"] not in reclamadas]
                    if len(candidatas) == 1:
                        previa = candidatas[0]
                        por_nombre = True
                        reclamadas.add(previa["id"])
                    elif len(candidatas) > 1:
                        aviso = (f"{fila.ruta_origen}: {len(candidatas)} filas del manifiesto "
                                 "coinciden por nombre+EXIF+bytes con otra ruta de origen; "
                                 "no se empareja ninguna (se trata como foto nueva)")
                        ambiguas.append(aviso)
                        logging.getLogger(__name__).warning(
                            "Emparejado ambiguo, posible duplicado: %s", aviso)
                if previa is None:
                    conexion.execute(f"INSERT INTO imagenes ({lista}) VALUES ({marcadores})", datos)
                    nuevas += 1
                    continue
                if por_nombre:
                    # La fila adopta la identidad nueva (ruta y clave) antes de
                    # decidir qué hacer con ella; así la próxima tanda casa exacto.
                    reabiertas_por_nombre += 1
                    conexion.execute(
                        "UPDATE imagenes SET ruta_origen = :ruta_origen, clave = :clave "
                        "WHERE id = :id_previa", dict(datos, id_previa=previa["id"]))
                if (previa["estado"] == "hecho" and previa["unassigned"]
                      and not datos["unassigned"]):
                    previas = "\n".join(
                        r for r in (previa["ruta_salida_original"], previa["ruta_salida_crop"],
                                    previa["ruta_salida_tiff"]) if r)
                    conexion.execute(
                        f"UPDATE imagenes SET {asignaciones}, estado = 'pendiente', "
                        f"motivo_fallo = NULL, verificacion = NULL, "
                        f"previas_sin_ordenar = :previas WHERE id = :id_previa",
                        dict(datos, id_previa=previa["id"], previas=previas))
                    reasignadas += 1
                elif previa["estado"] == "hecho":
                    saltadas += 1
                    if previa["unassigned"]:
                        siguen_sin_asignar += 1
                    vuelo = (previa["pb"], previa["vuelo"])
                    if previa["pb"] and previa["vuelo"] and vuelo not in vuelos_saltados:
                        vuelos_saltados.append(vuelo)
                else:
                    conexion.execute(
                        f"UPDATE imagenes SET {asignaciones}, estado = 'pendiente', "
                        f"motivo_fallo = NULL WHERE id = :id_previa",
                        dict(datos, id_previa=previa["id"]))
                    reintentadas += 1
            self._escribir_vuelos(conexion, vuelos or ())
        return ResultadoInsercion(nuevas, saltadas, reintentadas, vuelos_saltados,
                                  reasignadas, siguen_sin_asignar, reabiertas_por_nombre,
                                  tuple(ambiguas), borrados_reintentados, borrados_pendientes)

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
        # por imagen (~99 ms en la Pi, bench PLANTA_C 2026-09-13). Se conserva por
        # manifiestos de runs anteriores que aún tengan filas 'en_curso'.
        self._actualizar(id_fila, estado="en_curso")

    def marcar_hecha(self, id_fila: int, verificacion: str) -> None:
        # Las rutas viejas de SIN_ORDENAR se leen ANTES de marcar y se borran
        # DESPUÉS: si la escritura nueva falló, nunca se llega aquí y el
        # fichero viejo se queda donde estaba.
        previas = self._conexion().execute(
            "SELECT previas_sin_ordenar, ruta_salida_original, ruta_salida_crop, "
            "ruta_salida_tiff FROM imagenes WHERE id = ?", (id_fila,)).fetchone()
        self._actualizar(id_fila, estado="hecho", verificacion=verificacion,
                         motivo_fallo=None, previas_sin_ordenar=None)
        if previas is not None and previas["previas_sin_ordenar"]:
            nuevas = {r.casefold() for r in (previas["ruta_salida_original"],
                                             previas["ruta_salida_crop"],
                                             previas["ruta_salida_tiff"]) if r}
            pendientes = []
            for ruta in previas["previas_sin_ordenar"].split("\n"):
                if ruta and ruta.casefold() not in nuevas:
                    if not self._borrar_ruta_previa(ruta, id_fila):
                        pendientes.append(ruta)
            if pendientes:
                # Sin columna nueva: `previas_sin_ordenar` en una fila `hecho`
                # significa "borrado pendiente". `insertar_o_reabrir` lo
                # reintenta en la tanda siguiente y el cierre lo reporta.
                self._actualizar(id_fila, previas_sin_ordenar="\n".join(pendientes))

    _COLUMNAS_SALIDA = ("ruta_salida_original", "ruta_salida_crop", "ruta_salida_tiff")

    def _todas_las_salidas(self) -> set[str]:
        """Set casefold de las salidas actuales de TODAS las filas (una pasada)."""
        conexion = self._conexion()
        salidas: set[str] = set()
        for fila in conexion.execute(
                "SELECT ruta_salida_original, ruta_salida_crop, ruta_salida_tiff "
                "FROM imagenes"):
            salidas.update(r.casefold() for r in fila if r)
        return salidas

    def _salidas_coincidentes(self, ruta: str) -> set[str]:
        """Salidas casefold de las filas que PUEDEN coincidir con `ruta`, sin
        recorrer la tabla en Python. Consulta acotada (NOCASE en C, sin caché
        entre llamadas que invalidar). NOCASE solo pliega ASCII: rutas con
        no-ASCII caen al recorrido completo para conservar el casefold exacto."""
        if not ruta.isascii():
            return self._todas_las_salidas()
        condicion = " OR ".join(f"{c} = ? COLLATE NOCASE" for c in self._COLUMNAS_SALIDA)
        salidas: set[str] = set()
        for fila in self._conexion().execute(
                f"SELECT {', '.join(self._COLUMNAS_SALIDA)} FROM imagenes WHERE {condicion}",
                (ruta,) * len(self._COLUMNAS_SALIDA)):
            salidas.update(r.casefold() for r in fila if r)
        return salidas

    def _borrar_ruta_previa(self, ruta: str, id_fila: int,
                            salidas: set[str] | None = None) -> bool:
        """Borra un fichero viejo de SIN_ORDENAR y VERIFICA que ya no existe.
        Un fallo no tumba la fila ya escrita, pero NO es silencioso: ERROR en
        el log y devuelve False para que el llamante deje el borrado pendiente.
        No borra si otra fila lo tiene como salida (nunca se quita un fichero
        ajeno). True = ya no existe (borrado o ajeno/ausente)."""
        # Comparación con casefold (igual que marcar_hecha) contra la salida
        # actual de CUALQUIER fila, incluida la propia: si coincide, el fichero
        # es una foto buena en uso y NO se borra (el pendiente se da por resuelto).
        if salidas is None:
            salidas = self._salidas_coincidentes(ruta)
        if ruta.casefold() in salidas:
            return True
        log = logging.getLogger(__name__)
        try:
            from . import almacen
            if almacen.es_uri_gcs(ruta):
                almacen_obj, prefijo = almacen.abrir_almacen(ruta)
                almacen_obj.borrar(prefijo)
                sigue = almacen_obj.existe(prefijo)
            else:
                if os.path.exists(ruta):
                    os.remove(ruta)
                sigue = os.path.exists(ruta)
        except Exception as exc:
            log.error("No se pudo borrar la ruta previa de SIN_ORDENAR %s: %s "
                      "(queda pendiente de borrado en la próxima tanda)", ruta, exc)
            return False
        if sigue:
            log.error("La ruta previa de SIN_ORDENAR %s SIGUE existiendo tras borrarla "
                      "(queda pendiente de borrado en la próxima tanda)", ruta)
            return False
        return True

    def reintentar_borrados_pendientes(self) -> tuple[int, int]:
        """Reintenta los borrados de SIN_ORDENAR que fallaron en una tanda
        anterior (filas `hecho` con `previas_sin_ordenar`). Devuelve
        (reintentados_ok, siguen_pendientes)."""
        ok = pendientes = 0
        filas = self._conexion().execute(
            "SELECT id, previas_sin_ordenar FROM imagenes "
            "WHERE estado = 'hecho' AND previas_sin_ordenar IS NOT NULL "
            "AND previas_sin_ordenar != ''").fetchall()
        if not filas:
            return 0, 0
        # Una sola lectura de salidas: borrar ficheros viejos no cambia las
        # salidas de ninguna fila, así que el set vale durante todo el bucle.
        salidas = self._todas_las_salidas()
        cambios = []
        for fila in filas:
            quedan = []
            rutas = [r for r in fila["previas_sin_ordenar"].split("\n") if r]
            for ruta in rutas:
                if self._borrar_ruta_previa(ruta, fila["id"], salidas):
                    ok += 1
                else:
                    quedan.append(ruta)
            pendientes += len(quedan)
            nuevo = "\n".join(quedan) or None
            if nuevo != fila["previas_sin_ordenar"]:
                cambios.append((nuevo, fila["id"]))
        if cambios:
            conexion = self._conexion()
            with conexion:  # un único commit
                conexion.executemany(
                    "UPDATE imagenes SET previas_sin_ordenar = ? WHERE id = ?", cambios)
        return ok, pendientes

    def borrados_sin_ordenar_pendientes(self) -> list[str]:
        """Rutas viejas de SIN_ORDENAR que siguen sin borrarse (filas `hecho`)."""
        rutas: list[str] = []
        for fila in self._conexion().execute(
                "SELECT previas_sin_ordenar FROM imagenes WHERE estado = 'hecho' "
                "AND previas_sin_ordenar IS NOT NULL AND previas_sin_ordenar != ''"):
            rutas.extend(r for r in fila["previas_sin_ordenar"].split("\n") if r)
        return rutas

    def duplicados_en_sin_ordenar(self) -> list[tuple[str, str]]:
        """Fotos ya ordenadas (hecho, con vuelo) que además tienen OTRA fila
        `hecho` sin asignar con el mismo nombre + EXIF y cuyo fichero sigue en
        SIN_ORDENAR. Devuelve (ruta en SIN_ORDENAR, ruta ordenada). Solo
        reporta: nunca borra."""
        from . import almacen
        ordenadas: dict[tuple[str, str], str] = {}
        sin_ordenar: list[tuple[tuple[str, str], str]] = []
        for fila in self._conexion().execute(
                "SELECT nombre_original, timestamp_exif, unassigned, ruta_salida_original "
                "FROM imagenes WHERE estado = 'hecho' AND timestamp_exif IS NOT NULL "
                "AND timestamp_exif != '' ORDER BY id"):
            clave = (fila["nombre_original"], fila["timestamp_exif"])
            if fila["unassigned"]:
                if fila["ruta_salida_original"]:
                    sin_ordenar.append((clave, fila["ruta_salida_original"]))
            elif fila["ruta_salida_original"]:
                ordenadas.setdefault(clave, fila["ruta_salida_original"])
        duplicados = []
        for clave, ruta in sin_ordenar:
            if clave in ordenadas:
                try:
                    existe = almacen.existe_ruta(ruta)
                except Exception:  # noqa: BLE001 — duda = se reporta
                    existe = True
                if existe:
                    duplicados.append((ruta, ordenadas[clave]))
        return duplicados

    def rutas_salida_por_clave(self) -> dict[str, set[str]]:
        """`ruta de salida (original, crop o tiff)` -> claves de las imágenes
        del manifiesto que la usan. Comparación sin distinguir mayúsculas
        (destinos Windows). Base de la desambiguación entre tandas y de la
        red final del apply."""
        mapa: dict[str, set[str]] = {}
        for fila in self._conexion().execute(
                "SELECT clave, ruta_salida_original, ruta_salida_crop, ruta_salida_tiff, "
                "previas_sin_ordenar FROM imagenes"):
            # Las previas SIN_ORDENAR de una fila reabierta siguen en disco
            # hasta que la escritura nueva acabe: están ocupadas por su clave.
            previas = (fila["previas_sin_ordenar"] or "").split("\n")
            for ruta in (fila["ruta_salida_original"], fila["ruta_salida_crop"],
                         fila["ruta_salida_tiff"], *previas):
                if ruta:
                    mapa.setdefault(ruta.casefold(), set()).add(fila["clave"])
        return mapa

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

    def reabrir_fallidas(self, ejecucion_id: int | None = None) -> int:
        """Devuelve a 'pendiente' TODAS las filas 'fallido' (reintento dirigido,
        sin reescanear el origen). No toca las 'hecho'. Con `ejecucion_id` las
        adopta para ESTE run (así el cierre no las cuenta dos veces). Devuelve cuántas."""
        conexion = self._conexion()
        with conexion:
            cursor = conexion.execute(
                "UPDATE imagenes SET estado = 'pendiente', motivo_fallo = NULL, "
                "ejecucion_id = COALESCE(?, ejecucion_id) WHERE estado = 'fallido'",
                (ejecucion_id,))
        return cursor.rowcount

    def resumen(self) -> dict[str, int]:
        conteos = {estado: 0 for estado in ESTADOS}
        for fila in self._conexion().execute(
            "SELECT estado, COUNT(*) AS total FROM imagenes GROUP BY estado"
        ):
            conteos[fila["estado"]] = fila["total"]
        return conteos

    @staticmethod
    def _filtro_run(tipos, ejecucion_id) -> tuple[str, list]:
        tipos = sorted(tipos) if tipos is not None else None
        partes, params = [], []
        if tipos is not None:
            partes.append("tipo IN ({0})".format(", ".join("?" for _ in tipos)))
            params += tipos
        if ejecucion_id is not None:
            partes.append("ejecucion_id = ?")
            params.append(int(ejecucion_id))
        return (" AND ".join(partes) or "1=1"), params

    def conteo_por_tipos(self, tipos, ejecucion_id: int | None = None) -> dict[str, int]:
        """Filas del manifiesto por estado, restringido a `tipos` (p. ej. las
        RGB; `None` = todos) y, con `ejecucion_id`, a las filas de ESTE run
        (las pendientes de otro shard o de una tanda previa no cuentan: darían
        un falso 'errors'). Es la verdad que usa el marcador de errores de fase."""
        donde, params = self._filtro_run(tipos, ejecucion_id)
        conteos = {estado: 0 for estado in ESTADOS}
        for fila in self._conexion().execute(
            f"SELECT estado, COUNT(*) AS total FROM imagenes WHERE {donde} "
            "GROUP BY estado", params
        ):
            conteos[fila["estado"]] = fila["total"]
        return conteos

    def fallidas_por_tipos(self, tipos, ejecucion_id: int | None = None) -> list[tuple[str, str]]:
        """`(ruta_origen, motivo_fallo)` de cada fila 'fallido' de `tipos`
        (y de `ejecucion_id` si se da)."""
        donde, params = self._filtro_run(tipos, ejecucion_id)
        return [(fila["ruta_origen"], fila["motivo_fallo"] or "")
                for fila in self._conexion().execute(
                    f"SELECT ruta_origen, motivo_fallo FROM imagenes WHERE estado = 'fallido' "
                    f"AND {donde} ORDER BY id", params)]

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

    @staticmethod
    def _escribir_vuelos(conexion: sqlite3.Connection, vuelos: Iterable[dict]) -> None:
        for v in vuelos:
            datos = {c: v.get(c) for c in _COLUMNAS_VUELOS}
            datos["sufijo"] = datos["sufijo"] or ""
            conexion.execute(
                f"INSERT OR REPLACE INTO vuelos ({', '.join(_COLUMNAS_VUELOS)}) "
                f"VALUES ({', '.join(':' + c for c in _COLUMNAS_VUELOS)})", datos)

    def guardar_vuelos(self, vuelos: Iterable[dict]) -> None:
        """Escribe/reemplaza filas de `vuelos` (clave `pb, vuelo, sufijo`)."""
        conexion = self._conexion()
        with conexion:
            self._escribir_vuelos(conexion, vuelos)

    def vuelos(self) -> dict[tuple[str, str, str | None], dict]:
        """`{(pb, vuelo, sufijo|None): fila}` del consenso de giro por vuelo."""
        return {
            (f["pb"], f["vuelo"], f["sufijo"] or None): dict(f)
            for f in self._conexion().execute("SELECT * FROM vuelos ORDER BY pb, vuelo, sufijo")
        }

    def angulos_versionados_por_vuelo(self) -> dict[tuple[str, str], tuple[int, str | None]]:
        """`{(pb, vuelo): (angulo, version_algoritmo)}` del destino. Sale de `vuelos`
        (sufijo vacío); los vuelos sin fila ahí (manifiesto antiguo, planta Horizontal,
        modo manual) caen a `imagenes.angulo_giro/angulo_version` (None = antiguo)."""
        conexion = self._conexion()
        resultado = {
            (fila["pb"], fila["vuelo"]): (fila["angulo_giro"], fila["angulo_version"])
            for fila in conexion.execute(
                "SELECT pb, vuelo, angulo_giro, angulo_version FROM imagenes WHERE id IN ("
                "SELECT MIN(id) FROM imagenes WHERE pb IS NOT NULL AND vuelo IS NOT NULL "
                "AND unassigned = 0 GROUP BY pb, vuelo)")
        }
        for fila in conexion.execute(
                "SELECT pb, vuelo, angulo, version_algoritmo FROM vuelos WHERE sufijo = ''"):
            clave = (fila["pb"], fila["vuelo"])
            if clave in resultado:
                resultado[clave] = (fila["angulo"], fila["version_algoritmo"])
        return resultado

    def unificar_angulo_vuelo(self, pb: str, vuelo: str, angulo: int, version: str,
                              reabrir: bool) -> int:
        """Deja TODAS las filas asignadas de `(pb, vuelo)` con un solo ángulo y una sola
        versión de algoritmo (invariante: un ángulo por vuelo). Con `reabrir=True`
        (el ángulo cambió) las filas pasan a `pendiente` (incluidas las `hecho`), para
        que el apply reescriba su salida con el ángulo nuevo (misma clave: sobrescribe).
        Con `reabrir=False` (mismo ángulo) solo se actualiza `angulo_version`.
        Devuelve cuántas filas se reabrieron (las que no estaban ya `pendiente`)."""
        conexion = self._conexion()
        with conexion:
            if not reabrir:
                conexion.execute(
                    "UPDATE imagenes SET angulo_version = ? WHERE pb = ? AND vuelo = ? "
                    "AND unassigned = 0 AND angulo_version IS NOT ?",
                    (version, pb, vuelo, version))
                return 0
            reabiertas = conexion.execute(
                "SELECT COUNT(*) FROM imagenes WHERE pb = ? AND vuelo = ? AND unassigned = 0 "
                "AND estado != 'pendiente'", (pb, vuelo)).fetchone()[0]
            conexion.execute(
                "UPDATE imagenes SET angulo_giro = ?, angulo_version = ?, estado = 'pendiente', "
                "motivo_fallo = NULL, verificacion = NULL "
                "WHERE pb = ? AND vuelo = ? AND unassigned = 0",
                (angulo, version, pb, vuelo))
        return int(reabiertas)

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
