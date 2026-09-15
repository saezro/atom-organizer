# Índice Excel acumulativo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organizar por cachitos hacia el mismo destino de planta, con un `INDICE_<PLANTA>.xlsx` (1 fila por imagen) que acumula, y un cierre que genera meta/location sin releer imágenes.

**Architecture:** El manifiesto SQLite `<destino>/.organizado/manifiesto.db` pasa a ser memoria permanente del destino: dedupe por `clave`, metadatos de posición guardados en el índice, tabla `ejecuciones`. El cierre construye los CSV meta/location reutilizando el mismo código de `exif.MetaLocation` pero alimentado desde el manifiesto, y regenera el Excel entero.

**Tech Stack:** Python 3, sqlite3, PIL, openpyxl 3.1 (ya en requirements), pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-indice-excel-acumulativo-design.md`

## Global Constraints

- Repo: `~/atom-organizer-work`, rama `dev`. Tests: `.venv/bin/python -m pytest -q <ruta>`.
- **NO commits, NO `git add`** en subagentes: el hilo base commitea.
- `.organizado/` se conserva siempre (decisión Cas 2026-09-15).
- `clave = nombre_original + "|" + (timestamp_exif or "") + "|" + str(bytes_origen)`.
- Imagen `hecho` ya en manifiesto → se salta y se avisa; `fallido`/`pendiente` → vuelve a `pendiente`.
- Equipo (Cas 2026-09-15): manifiesto guarda `make` (EXIF `Image Make`) y `equipo_estadillo` (`Equipo_de_vuelo`); `Sí/No` se DERIVA con `atom_core/equipo.py` al escribir el Excel. Discrepancia = aviso por vuelo, nunca bloquea.
- CSV meta/location: mismos nombres, carpetas, columnas, orden, sin cabecera. Equivalencia byte a byte con el camino actual.
- Excel: `<output_folder>/INDICE_<basename(output_folder)>.xlsx`, hoja `Imagenes`; bloqueado → `INDICE_<PLANTA>_<AAAAMMDD_HHMMSS>.xlsx` + aviso; nunca tumba el cierre.
- Texto visible con tildes y ñ. Comentarios en español, estilo del fichero.
- No tocar `apply.py` ni `atom_core/rgb_gpu.py` (cambios GPU sin commitear de otra línea de trabajo).

---

### Task 1: Manifiesto con clave, metadatos y ejecuciones

**Files:**
- Modify: `atom_core/manifiesto.py`
- Test: `tests/test_manifiesto.py` (añadir al final)

**Interfaces:**
- Produces:
  - `nombre_de_ruta(ruta: str) -> str`
  - `clave_imagen(ruta_origen: str, timestamp_exif: str | None, bytes_origen: int | None) -> str`
  - `FilaManifiesto` gana campos con default: `meta_leida: bool = False`, `lat: float | None = None`, `lon: float | None = None`, `altitud_abs: str | None = None`, `altura_relativa: str | None = None`, `gimbal_yaw: str | None = None`, `gimbal_pitch: str | None = None`, `gimbal_roll: str | None = None`, `flight_yaw: str | None = None`, `ancho_px: int | None = None`, `alto_px: int | None = None`, `make: str | None = None`, `equipo_estadillo: str | None = None`
  - `@dataclass ResultadoInsercion(nuevas: int, saltadas: int, reintentadas: int, vuelos_saltados: list[tuple[str, str]])`
  - `Manifiesto.insertar_o_reabrir(filas, ejecucion_id: int | None = None) -> ResultadoInsercion`
  - `Manifiesto.insertar_muchas(filas) -> int` (se mantiene: devuelve `.nuevas`)
  - `Manifiesto.angulos_por_vuelo() -> dict[tuple[str, str], int]`
  - `Manifiesto.abrir_ejecucion(origen: str, version_app: str) -> int`
  - `Manifiesto.cerrar_ejecucion(id_ejecucion: int, n_nuevas: int, n_saltadas: int, n_reintentadas: int) -> None`
  - `Manifiesto.ejecuciones() -> dict[int, sqlite3.Row]`
  - Columnas nuevas en `imagenes`: `nombre_original`, `clave` (UNIQUE), `meta_leida`, `lat`, `lon`, `altitud_abs`, `altura_relativa`, `gimbal_yaw`, `gimbal_pitch`, `gimbal_roll`, `flight_yaw`, `ancho_px`, `alto_px`, `ejecucion_id`, `make`, `equipo_estadillo`

- [ ] **Step 1: Tests que fallan** — añadir a `tests/test_manifiesto.py`:

```python
def test_misma_imagen_desde_dos_rutas_es_una_sola_fila(tmp_path):
    """Otra SD u otro punto de montaje: la ruta cambia, la imagen no."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    r1 = manifiesto.insertar_o_reabrir([_fila("/media/sd1/DCIM/DJI_0001.JPG", bytes_origen=500)])
    r2 = manifiesto.insertar_o_reabrir([_fila("/media/sd2/100MEDIA/DJI_0001.JPG", bytes_origen=500)])
    assert (r1.nuevas, r2.nuevas) == (1, 0)
    assert len(manifiesto.todas()) == 1


def test_mismo_nombre_y_segundo_con_distinto_tamano_son_dos_filas(tmp_path):
    """Dos drones pueden sacar DJI_0001.JPG en el mismo segundo."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_o_reabrir([_fila("/a/DJI_0001.JPG", bytes_origen=500),
                                   _fila("/b/DJI_0001.JPG", bytes_origen=501)])
    assert len(manifiesto.todas()) == 2


def test_hecha_se_salta_y_se_informa_su_vuelo(tmp_path):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_o_reabrir([_fila("/sd1/DJI_0001.JPG")])
    manifiesto.marcar_hecha(manifiesto.todas()[0]["id"], "/destino/x.JPG:10")

    resultado = manifiesto.insertar_o_reabrir([_fila("/sd2/DJI_0001.JPG")])

    assert (resultado.nuevas, resultado.saltadas, resultado.reintentadas) == (0, 1, 0)
    assert resultado.vuelos_saltados == [("PB1", "V01")]
    fila = manifiesto.todas()[0]
    assert fila["estado"] == "hecho"
    assert fila["ruta_origen"] == "/sd1/DJI_0001.JPG"


def test_fallida_se_reabre_con_la_ruta_nueva(tmp_path):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_o_reabrir([_fila("/sd1/DJI_0001.JPG")], ejecucion_id=1)
    manifiesto.marcar_fallida(manifiesto.todas()[0]["id"], "exiftool no encontrado")

    resultado = manifiesto.insertar_o_reabrir([_fila("/sd2/DJI_0001.JPG")], ejecucion_id=2)

    assert resultado.reintentadas == 1
    fila = manifiesto.todas()[0]
    assert fila["estado"] == "pendiente"
    assert fila["motivo_fallo"] is None
    assert fila["ruta_origen"] == "/sd2/DJI_0001.JPG"
    assert fila["ejecucion_id"] == 2


def test_guarda_metadatos_de_posicion(tmp_path):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_o_reabrir([_fila(meta_leida=True, lat=40.5, lon=-3.25,
                                         gimbal_yaw="+12.50", gimbal_pitch="-90.00",
                                         altura_relativa="+50.000", ancho_px=640, alto_px=512,
                                         make="DJI", equipo_estadillo="DJI M300")])
    fila = manifiesto.todas()[0]
    assert (fila["make"], fila["equipo_estadillo"]) == ("DJI", "DJI M300")
    assert (fila["meta_leida"], fila["lat"], fila["lon"]) == (1, 40.5, -3.25)
    assert (fila["gimbal_yaw"], fila["gimbal_pitch"], fila["altura_relativa"]) == ("+12.50", "-90.00", "+50.000")
    assert fila["nombre_original"] == "DJI_0001.JPG"


def test_angulos_por_vuelo(tmp_path):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_o_reabrir([_fila("/o/A.JPG", angulo_giro=270),
                                   _fila("/o/B.JPG", pb="PB2", vuelo="V03", angulo_giro=0),
                                   _fila("/o/C.JPG", pb=None, vuelo=None, unassigned=True)])
    assert manifiesto.angulos_por_vuelo() == {("PB1", "V01"): 270, ("PB2", "V03"): 0}


def test_ejecuciones(tmp_path):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    id_ej = manifiesto.abrir_ejecucion("/media/sd1", "3.4.93")
    manifiesto.cerrar_ejecucion(id_ej, n_nuevas=5, n_saltadas=2, n_reintentadas=1)
    ej = manifiesto.ejecuciones()[id_ej]
    assert (ej["origen"], ej["version_app"]) == ("/media/sd1", "3.4.93")
    assert (ej["n_nuevas"], ej["n_saltadas"], ej["n_reintentadas"]) == (5, 2, 1)
    assert ej["inicio"] and ej["fin"]


def test_manifiesto_v1_con_ruta_unique_migra_a_clave(tmp_path):
    """Manifiesto de la versión anterior (UNIQUE en ruta_origen, sin clave):
    se recrea la tabla conservando filas y estados."""
    ruta = tmp_path / "m.db"
    antiguo = sqlite3.connect(ruta)
    antiguo.executescript("""
        CREATE TABLE imagenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ruta_origen TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL,
            timestamp_exif TEXT, modelo TEXT, pb TEXT, vuelo TEXT,
            nombre_nuevo TEXT NOT NULL DEFAULT '',
            angulo_giro INTEGER NOT NULL DEFAULT 0, pct_recorte REAL,
            comprime INTEGER NOT NULL DEFAULT 0,
            ruta_salida_original TEXT NOT NULL, ruta_salida_crop TEXT,
            ruta_salida_tiff TEXT, unassigned INTEGER NOT NULL DEFAULT 0,
            bytes_origen INTEGER NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'pendiente', motivo_fallo TEXT,
            verificacion TEXT);
        CREATE INDEX idx_estado ON imagenes(estado);
        CREATE INDEX idx_vuelo ON imagenes(pb, vuelo);
        INSERT INTO imagenes (ruta_origen, tipo, timestamp_exif, pb, vuelo,
                              ruta_salida_original, bytes_origen, estado)
        VALUES ('/sd1/DJI_0001.JPG', 'RGB', '2026-05-01T10:00:00', 'PB1', 'V01',
                '/destino/a.JPG', 500, 'hecho');
    """)
    antiguo.commit()
    antiguo.close()

    manifiesto = Manifiesto(ruta)
    manifiesto.crear_esquema()

    fila = manifiesto.todas()[0]
    assert fila["estado"] == "hecho"
    assert fila["clave"] == "DJI_0001.JPG|2026-05-01T10:00:00|500"
    assert fila["meta_leida"] == 0
    resultado = manifiesto.insertar_o_reabrir([_fila("/sd2/DJI_0001.JPG", bytes_origen=500)])
    assert resultado.saltadas == 1
    # Idempotente: una segunda apertura no vuelve a migrar ni rompe.
    Manifiesto(ruta).crear_esquema()


def test_clave_imagen_rutas_windows_y_gcs():
    from atom_core.manifiesto import clave_imagen, nombre_de_ruta
    assert nombre_de_ruta(r"E:\DCIM\DJI_0001.JPG") == "DJI_0001.JPG"
    assert nombre_de_ruta("gs://b/x/DJI_0001.JPG") == "DJI_0001.JPG"
    assert clave_imagen("/a/DJI_0001.JPG", None, None) == "DJI_0001.JPG||0"
```

- [ ] **Step 2: Verificar que fallan**

Run: `.venv/bin/python -m pytest -q tests/test_manifiesto.py`
Expected: FAIL (`AttributeError: 'Manifiesto' object has no attribute 'insertar_o_reabrir'`, `TypeError` por campos nuevos).

- [ ] **Step 3: Implementación en `atom_core/manifiesto.py`**

3a. Imports: añadir `import datetime` y `import re`.

3b. Sustituir `_ESQUEMA` por una tupla de sentencias (se ejecutan una a una: `executescript` hace COMMIT implícito y rompería la migración transaccional):

```python
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
```

3c. Funciones de módulo (antes de `FilaManifiesto`):

```python
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
```

3d. `FilaManifiesto`: añadir tras `bytes_origen: int = 0`:

```python
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
```

3e. Tras `FilaManifiesto`:

```python
@dataclass
class ResultadoInsercion:
    nuevas: int
    saltadas: int
    reintentadas: int
    # (pb, vuelo) de las imágenes saltadas, sin repetir y en orden de aparición.
    vuelos_saltados: list[tuple[str, str]]
```

3f. `crear_esquema` y migración (sustituye `crear_esquema`; `_migrar_columnas` se queda igual):

```python
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
```

3g. Inserción (sustituye `insertar_muchas`):

```python
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
```

3h. Consultas y ejecuciones (junto a `filas_por_vuelo`):

```python
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
```

3i. En el docstring de `colisiones_ruta_salida_original`, cambiar "`ruta_origen` es UNIQUE (arriba)" por "`clave` es UNIQUE (arriba)".

- [ ] **Step 4: Verificar**

Run: `.venv/bin/python -m pytest -q tests/test_manifiesto.py tests/test_cierre_organizado.py tests/test_organize_stats_fases.py tests/test_colisiones_destino.py`
Expected: PASS. Si algún test previo inserta dos imágenes DISTINTAS con mismo nombre, mismo timestamp y mismo `bytes_origen` (default 0) esperando dos filas, dar `bytes_origen` distinto en ESE test (no cambiar la clave) y reportarlo.

- [ ] **Step 5: Reportar** archivos tocados y salida de pytest. Sin commit.

---

### Task 2: El índice lee y guarda la posición, reutiliza ángulos y avisa de lo saltado

**Files:**
- Modify: `atom_core/indice.py` (`_MetadatosImagen` ~l.72, `_leer_metadatos` l.255-334, `_construir_fila` l.484-552, `construir_indice` l.555-644)
- Create: `atom_core/equipo.py`
- Modify: `tests/conftest.py` (`make_dji_jpeg` acepta `make`/`model`, default `None`: no cambia otros tests)
- Test: `tests/test_indice_posicion.py` (nuevo), `tests/test_equipo.py` (nuevo)

**Interfaces:**
- Consumes: Task 1 (`FilaManifiesto` campos nuevos, `Manifiesto.insertar_o_reabrir`, `Manifiesto.angulos_por_vuelo`, `ResultadoInsercion`).
- Produces:
  - `@dataclass _Posicion(lat, lon, altitud_abs, altura_relativa, gimbal_yaw, gimbal_pitch, gimbal_roll, flight_yaw, ancho_px, alto_px)`
  - `_MetadatosImagen` gana `posicion: _Posicion | None = None` y `meta_leida: bool = False`
  - `_gps_desde_buffer(buf: bytes) -> tuple[float, float] | None`
  - `campos_posicion(dato: _MetadatosImagen) -> dict` (kwargs de `FilaManifiesto`)
  - `construir_indice(..., ejecucion_id: int | None = None) -> dict` con claves nuevas `"nuevas"`, `"saltadas"`, `"reintentadas"`, `"vuelos_equipo_discrepa"`
  - `_make_desde_buffer(buf: bytes) -> str | None`; `_MetadatosImagen` gana `make: str | None = None`
  - Ventanas de `_ventanas_por_vuelo` ganan `"equipo": str | None`
  - `_avisar_equipo(asignaciones, progress_callback) -> int`
  - `atom_core/equipo.py`: `normalizar(texto) -> str`, `familias_estadillo(texto) -> set[str]`, `familias_exif(modelo) -> set[str]`, `equipo_coincide(texto_estadillo, modelo_exif) -> bool | None`, `texto_coincide(valor: bool | None) -> str | None`

- [ ] **Step 1: Tests que fallan** — crear `tests/test_indice_posicion.py`:

```python
"""El índice guarda la posición de cada imagen con los MISMOS valores que hoy
lee `MetaLocation` al generar meta/location en el cierre. Si divergieran, el
CSV desde manifiesto dejaría de ser idéntico al actual."""
import datetime as dt

from atom_core import indice
import exif


class _Cb:
    def emit(self, *a, **k):
        pass


def test_posicion_coincide_con_metalocation(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "DJI_0001_T.JPG"), lat=37.123456, lon=-5.654321,
                         dt_val=dt.datetime(2026, 1, 15, 10, 0, 0),
                         relative_altitude=48.3, gimbal_yaw=-87.4, gimbal_pitch=-45.2)
    gi = exif.GeneralInformationFromImage(logger)
    ml = exif.MetaLocation(logger)

    dato = indice._leer_metadatos(ruta, gi, _Cb())

    coords = ml.leerLatitudLongitudAltitud_exif_DJI(ruta, _Cb())
    gimbal = gi.get_gimbal_yaw_pitch(ruta)
    xmp = gi.get_xmp_data(ruta)
    p = dato.posicion
    assert dato.meta_leida is True
    assert (p.lat, p.lon) == (coords[1], coords[2])
    assert (p.gimbal_yaw, p.gimbal_pitch) == (gimbal[0], gimbal[1])
    assert (p.altitud_abs, p.altura_relativa, p.gimbal_roll, p.flight_yaw) == (xmp[0], xmp[1], xmp[2], xmp[4])
    assert (p.ancho_px, p.alto_px) == (64, 48)
    assert dato.yaw == float(gimbal[0])


def test_sin_gps_queda_leida_pero_sin_coordenadas(tmp_path, logger):
    from PIL import Image
    ruta = str(tmp_path / "SIN_GPS.JPG")
    Image.new("RGB", (32, 16)).save(ruta, format="JPEG")
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    assert dato.meta_leida is True
    assert dato.posicion.lat is None and dato.posicion.lon is None


def test_campos_posicion_mapea_a_fila(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "A.JPG"))
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    campos = indice.campos_posicion(dato)
    assert campos["meta_leida"] is True
    assert set(campos) == {"meta_leida", "lat", "lon", "altitud_abs", "altura_relativa",
                           "gimbal_yaw", "gimbal_pitch", "gimbal_roll", "flight_yaw",
                           "ancho_px", "alto_px"}


def test_campos_posicion_sin_lectura_es_meta_leida_false():
    dato = indice._MetadatosImagen(ruta="gs://b/A.JPG", nombre="A.JPG", timestamp=None,
                                   modelo=None, yaw=None, gps=None)
    assert indice.campos_posicion(dato) == {"meta_leida": False}


def test_make_y_modelo_desde_la_misma_lectura(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "DJI_0002_T.JPG"), make="DJI", model="M4T")
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    assert (dato.make, dato.modelo) == ("DJI", "M4T")


def test_sin_make_queda_none(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "DJI_0003_T.JPG"))
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    assert dato.make is None


class _CbLineas:
    def __init__(self):
        self.lineas = []

    def emit(self, v):
        self.lineas.append(v)


def _dato(nombre, modelo):
    return indice._MetadatosImagen(ruta=f"/sd/{nombre}", nombre=nombre, timestamp=None,
                                   modelo=modelo, yaw=None, gps=None)


def test_aviso_equipo_una_linea_por_vuelo_que_discrepa():
    """Caso real MELINESTI: estadillo 'DJI M300', EXIF M4T."""
    v1 = {"pb": "TS09", "vuelo": "1", "equipo": "DJI M300"}
    v2 = {"pb": "TS09", "vuelo": "2", "equipo": "DJI Matrice 4T"}
    v3 = {"pb": "TS10", "vuelo": "1", "equipo": "Dron1"}
    cb = _CbLineas()
    n = indice._avisar_equipo([(_dato("A_T.JPG", "M4T"), v1), (_dato("B_T.JPG", "M4T"), v1),
                               (_dato("C_T.JPG", "M4T"), v2), (_dato("D_T.JPG", "M4T"), v3),
                               (_dato("E_T.JPG", "M4T"), None)], cb)
    assert n == 1
    avisos = [l for l in cb.lineas if "AVISO" in l]
    assert len(avisos) == 1
    assert "TS09" in avisos[0] and "'DJI M300'" in avisos[0] and "M4T" in avisos[0]


def test_ventanas_llevan_equipo_del_estadillo():
    import pandas as pd

    class _Pipeline:
        def ventana_horaria_vuelo(self, *a):
            return 0, 1

    cfg = type("Cfg", (), {"seconds_range": 0, "mismatch_hours": 0, "mismatch_minutes": 0})()
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha", "Hora_de_inicio": "HI",
            "Hora_final": "HF", "Equipo_de_vuelo": "Equipo_de_vuelo"}
    df = pd.DataFrame({"PB": ["1", "2"], "Vuelo": ["1", "1"], "Fecha": ["2026:09:10"] * 2,
                       "HI": ["10:00:00"] * 2, "HF": ["10:20:00"] * 2,
                       "Equipo_de_vuelo": ["DJI M300", float("nan")]})
    ventanas = indice._ventanas_por_vuelo(df, cols, _Pipeline(), cfg, _Cb())
    assert [v["equipo"] for v in ventanas] == ["DJI M300", None]
    sin_col = indice._ventanas_por_vuelo(df.drop(columns=["Equipo_de_vuelo"]), cols,
                                         _Pipeline(), cfg, _Cb())
    assert [v["equipo"] for v in sin_col] == [None, None]
```

Crear `tests/test_equipo.py` (valores reales: estadillos del SSD del portátil y `indai.camara_modelos.alias_exif`):

```python
import pytest

from atom_core.equipo import equipo_coincide, texto_coincide


@pytest.mark.parametrize("estadillo, modelo, esperado", [
    ("DJI M300", "M4T", False),
    ("DJI M200", "M4T", False),
    ("Mavic 2EA", "M4T", False),
    ("AT-M2EA-01", "MAVIC2-ENTERPRISE-ADVANCED", True),
    ("Mavic 2EA", "MAVIC2-ENTERPRISE-ADVANCED", True),
    ("DJI Matrice 4T", "M4T", True),
    ("DJI M4T", "Matrice 4T", True),
    ("DJI M300", "XT2", True),
    ("DJI M300 RTK", "ZH20T", True),
    ("DJI M200", "ZH20T", False),
    ("DJI M30T", "M3T", False),
    ("DJI M300", "M30T", False),
    ("Dron1", "M4T", None),
    ("", "M4T", None),
    ("DJI M300", None, None),
    ("DJI M300", "FC6310", None),
    ("DJI M300", "M4T\x00", False),
])
def test_equipo_coincide(estadillo, modelo, esperado):
    assert equipo_coincide(estadillo, modelo) is esperado


def test_texto_coincide():
    assert (texto_coincide(True), texto_coincide(False), texto_coincide(None)) == ("Sí", "No", None)
```

Añadir a `make_dji_jpeg` (`tests/conftest.py`) los parámetros `make: str | None = None, model: str | None = None` (al final de la firma) y, antes de `piexif.dump`, `if make is not None: zeroth_ifd[piexif.ImageIFD.Make] = make` y lo mismo con `Model`. Actualizar el docstring.

- [ ] **Step 2: Verificar que fallan**

Run: `.venv/bin/python -m pytest -q tests/test_indice_posicion.py`
Run: `.venv/bin/python -m pytest -q tests/test_equipo.py`

Expected: FAIL (`AttributeError: '_MetadatosImagen' object has no attribute 'posicion'`; `ModuleNotFoundError: atom_core.equipo`).

- [ ] **Step 3: Implementación**

3a. Tras `_MetadatosImagen` añadir `_Posicion` y ampliar `_MetadatosImagen` (actualizar su docstring: `gps` ya es real):

```python
@dataclass
class _Posicion:
    """Posición de una imagen tal y como la escribe hoy meta/location: lat/lon
    en decimal y el resto como TEXTO crudo del XMP DJI (`get_gimbal_yaw_pitch`,
    `get_xmp_data`), para que el CSV desde manifiesto salga idéntico."""

    lat: float | None
    lon: float | None
    altitud_abs: str | None
    altura_relativa: str | None
    gimbal_yaw: str | None
    gimbal_pitch: str | None
    gimbal_roll: str | None
    flight_yaw: str | None
    ancho_px: int | None
    alto_px: int | None
```

En `_MetadatosImagen`, tras `gps: object`:

```python
    posicion: "_Posicion | None" = None
    meta_leida: bool = False
```

3b. Réplica pura del GPS (junto a `_modelo_desde_buffer`):

```python
def _gps_desde_buffer(buf: bytes) -> tuple[float, float] | None:
    """Réplica en memoria de `MetaLocation.leerLatitudLongitudAltitud_exif_DJI`
    (exif.py:1164), sin logging ni contadores: mismas referencias N/S/E/W,
    misma aritmética (mismo orden de operaciones -> mismo float). `None` en
    los mismos casos en que la original devuelve `None`."""
    img = PIL.Image.open(io.BytesIO(buf))
    try:
        datos_exif = img.getexif()
    finally:
        img.close()
    if len(datos_exif) == 0:
        return None
    coordenadas = datos_exif.get_ifd(34853)
    lat_ref = coordenadas.get(1)
    lon_ref = coordenadas.get(3)
    if lat_ref is None or lon_ref is None:
        return None
    latitud = coordenadas.get(2, 0)
    longitud = coordenadas.get(4, 0)
    if lat_ref == 'N':
        lat = float(latitud[0]) + float(latitud[1]) / 60 + float(latitud[2]) / 3600
    elif lat_ref == 'S':
        lat = (float(latitud[0]) + float(latitud[1]) / 60 + float(latitud[2]) / 3600) * -1
    else:
        return None
    if lon_ref == 'E':
        lon = float(longitud[0]) + float(longitud[1]) / 60 + float(longitud[2]) / 3600
    elif lon_ref == 'W':
        lon = (float(longitud[0]) + float(longitud[1]) / 60 + float(longitud[2]) / 3600) * -1
    else:
        return None
    return lat, lon


def _dimensiones_desde_buffer(buf: bytes) -> tuple[int | None, int | None]:
    try:
        with PIL.Image.open(io.BytesIO(buf)) as img:
            return img.size
    except Exception:  # noqa: BLE001 — dato informativo del índice, no bloquea
        return None, None
```

3c. En `_leer_metadatos`, sustituir desde `yaw = None` hasta el `return` por (el docstring: quitar el párrafo "El GPS ... NO entra en el atajo ... `gps` sale `None`" y explicar que ahora se lee con réplica pura; las rutas `gs://` quedan con `meta_leida=False` y el cierre relee su salida):

```python
    yaw = None
    gimbal = None
    xmp = None
    try:
        if buf is not None:
            bloque_xmp = _bloque_xmp_desde_buffer(buf, ruta)
            gimbal = exif.get_gimbal_yaw_pitch(ruta, bloque_xmp=bloque_xmp)
            try:
                xmp = exif.get_xmp_data(ruta, bloque_xmp=bloque_xmp)
            except Exception:  # noqa: BLE001
                xmp = None
        else:
            gimbal = exif.get_gimbal_yaw_pitch(ruta)
        yaw = float(gimbal[0])
    except Exception:  # noqa: BLE001
        yaw = None

    gps = None
    if buf is not None:
        try:
            if buf_exif is not None:
                gps = _con_reintento_fichero_completo(_gps_desde_buffer, buf_exif, ruta)
            else:
                with open(ruta, 'rb') as fd:
                    gps = _gps_desde_buffer(fd.read())
        except Exception:  # noqa: BLE001 — sin GPS la fila queda fuera de meta/location, como hoy
            gps = None

    # Solo local: con buffer, gimbal y XMP leídos. `gs://…` o lectura rota ->
    # `meta_leida=False` y el cierre relee el fichero de salida como siempre.
    posicion = None
    meta_leida = buf is not None and gimbal is not None and xmp is not None
    if meta_leida:
        ancho, alto = _dimensiones_desde_buffer(buf)
        posicion = _Posicion(
            lat=gps[0] if gps else None, lon=gps[1] if gps else None,
            altitud_abs=xmp[0], altura_relativa=xmp[1],
            gimbal_yaw=gimbal[0], gimbal_pitch=gimbal[1],
            gimbal_roll=xmp[2], flight_yaw=xmp[4],
            ancho_px=ancho, alto_px=alto)

    return _MetadatosImagen(ruta=ruta, nombre=nombre, timestamp=timestamp,
                            modelo=modelo, yaw=yaw, gps=gps,
                            posicion=posicion, meta_leida=meta_leida)
```

3d. Helper público (antes de `_construir_fila`):

```python
def campos_posicion(dato: _MetadatosImagen) -> dict:
    """kwargs de posición para `FilaManifiesto`. Sin lectura, solo
    `meta_leida=False` (el resto queda en su default `None`)."""
    if not dato.meta_leida or dato.posicion is None:
        return {"meta_leida": False}
    p = dato.posicion
    return {"meta_leida": True, "lat": p.lat, "lon": p.lon,
            "altitud_abs": p.altitud_abs, "altura_relativa": p.altura_relativa,
            "gimbal_yaw": p.gimbal_yaw, "gimbal_pitch": p.gimbal_pitch,
            "gimbal_roll": p.gimbal_roll, "flight_yaw": p.flight_yaw,
            "ancho_px": p.ancho_px, "alto_px": p.alto_px}
```

En `_construir_fila`, en el `return FilaManifiesto(...)` añadir `**campos_posicion(dato),` tras `bytes_origen=bytes_origen,`.

3e. `construir_indice`: firma añade `ejecucion_id: int | None = None` (último parámetro). Sustituir:

```python
    angulos = _consenso_de_angulo_por_vuelo(asignaciones, pipeline, cfg,
                                            cfg.output_folder, progress_callback)

    filas = [_construir_fila(dato, ventana, angulos, cfg, pipeline)
             for dato, ventana in asignaciones]
    manifiesto.insertar_muchas(filas)
```

por:

```python
    angulos = _consenso_de_angulo_por_vuelo(asignaciones, pipeline, cfg,
                                            cfg.output_folder, progress_callback)
    # Cachito posterior del mismo destino: el ángulo ya decidido para un vuelo
    # manda sobre el recalculado, para que JPG y TIFF del vuelo no discrepen.
    angulos.update(manifiesto.angulos_por_vuelo())

    filas = [_construir_fila(dato, ventana, angulos, cfg, pipeline)
             for dato, ventana in asignaciones]
    resultado = manifiesto.insertar_o_reabrir(filas, ejecucion_id=ejecucion_id)
    if resultado.saltadas:
        vuelos = ", ".join(_nombre_carpeta_vuelo(pb, vuelo, cfg.include_v)
                           for pb, vuelo in resultado.vuelos_saltados) or "sin vuelo asignado"
        progress_callback.emit(
            f"\n{resultado.saltadas} imagen(es) ya estaban organizadas en este destino "
            f"y se saltan (vuelos: {vuelos}).\n")
```

En el JSON de `STATS_INDICE_PREFIX` añadir `"ya_organizadas": resultado.saltadas,`. En el `return` final añadir `"nuevas": resultado.nuevas, "saltadas": resultado.saltadas, "reintentadas": resultado.reintentadas`. Actualizar el docstring de retorno.

3f. Crear `atom_core/equipo.py`:

```python
"""¿El dron del estadillo es el que hizo las fotos?

`Equipo_de_vuelo` es texto libre ('DJI M300', 'Mavic 2EA', 'AT-M2EA-01') y el
EXIF trae el modelo de cámara ('M4T', 'MAVIC2-ENTERPRISE-ADVANCED', o solo el
payload 'XT2' en drones de cámara intercambiable). Se comparan por FAMILIA.
Sin BD: el Organizer trabaja offline. Solo informa (aviso + columna del
Excel); nunca bloquea: un estadillo copiado de otro día no para la organización."""
from __future__ import annotations

import re

# Familia -> alias normalizados (ver `normalizar`). En el estadillo se buscan
# como SUBCADENA ('ATM2EA01' contiene 'M2EA'); en el EXIF, exactos.
FAMILIAS: dict[str, tuple[str, ...]] = {
    "M4T": ("MATRICE4T", "M4T"),
    "M30T": ("MATRICE30T", "M30T"),
    "M3T": ("MAVIC3T", "M3T"),
    "M2EA": ("MAVIC2ENTERPRISEADVANCED", "MAVIC2EA", "M2EA"),
    "M300": ("MATRICE300", "M300"),
    "M350": ("MATRICE350", "M350"),
    "M200": ("MATRICE200", "MATRICE210", "M200", "M210"),
}

# Cámaras payload: el EXIF no dice el dron, solo en cuáles puede ir montada.
PAYLOADS: dict[str, frozenset[str]] = {
    "XT2": frozenset({"M200", "M300"}),
    "ZH20T": frozenset({"M300", "M350"}),
    "H20T": frozenset({"M300", "M350"}),
}


def normalizar(texto) -> str:
    """Mayúsculas y solo `[A-Z0-9]`: el mismo dron se escribe 'Matrice 4T',
    'MATRICE-4T' o 'M4T\\x00' según quién o qué firmware."""
    return re.sub(r"[^A-Z0-9]", "", str(texto or "").upper())


def familias_estadillo(texto) -> set[str]:
    n = normalizar(texto)
    return {familia for familia, alias in FAMILIAS.items() if any(a in n for a in alias)}


def familias_exif(modelo) -> set[str]:
    n = normalizar(modelo)
    if n.startswith("DJI"):
        n = n[3:]
    if n in PAYLOADS:
        return set(PAYLOADS[n])
    return {familia for familia, alias in FAMILIAS.items() if n in alias}


def equipo_coincide(texto_estadillo, modelo_exif) -> bool | None:
    """`True`/`False` si se puede decidir; `None` si el estadillo no nombra
    un dron reconocible o el EXIF no trae un modelo conocido (no se avisa)."""
    del_estadillo = familias_estadillo(texto_estadillo)
    del_exif = familias_exif(modelo_exif)
    if not del_estadillo or not del_exif:
        return None
    return bool(del_estadillo & del_exif)


def texto_coincide(valor: bool | None) -> str | None:
    if valor is None:
        return None
    return "Sí" if valor else "No"
```

3g. Equipo en `atom_core/indice.py`:

- Import: `from atom_core import equipo as equipo_mod`.
- `_MetadatosImagen`: tras `meta_leida: bool = False`, añadir `make: str | None = None`.
- Junto a `_modelo_desde_buffer`:

```python
def _make_desde_buffer(buf: bytes) -> str | None:
    """Fabricante EXIF (`Image Make`), misma lectura que `_modelo_desde_buffer`.
    `exif` no tiene función original para el fabricante: sin tag -> `None`."""
    f = io.BytesIO(buf)
    tags = exifread.process_file(f, details=False, stop_tag="Image Make")
    if "Image Make" not in tags:
        f.seek(0)
        tags = exifread.process_file(f, details=True)
    if "Image Make" not in tags:
        return None
    return str(tags["Image Make"]).strip("\x00").strip() or None
```

- En `_leer_metadatos`, tras el bloque de `modelo` (solo local; `gs://` queda `None`):

```python
    make = None
    if buf_exif is not None:
        try:
            make = _con_reintento_fichero_completo(_make_desde_buffer, buf_exif, ruta)
        except Exception:  # noqa: BLE001 — dato informativo, no bloquea
            make = None
```

  y pasar `make=make` al `_MetadatosImagen(...)` del `return`.
- `_ventanas_por_vuelo`: antes del bucle `col_equipo = nombres_columnas.get("Equipo_de_vuelo")`; dentro, antes de `ventanas.append`:

```python
        equipo = None
        if col_equipo is not None and col_equipo in estadillo_df.columns:
            texto = str(estadillo_df[col_equipo].iloc[indice]).strip()
            equipo = texto if texto and texto.lower() != "nan" else None
```

  y añadir `"equipo": equipo` al dict de la ventana.
- `_construir_fila`: en `FilaManifiesto(...)` añadir `make=dato.make,` y `equipo_estadillo=None if unassigned else ventana.get("equipo"),`.
- Antes de `construir_indice`:

```python
def _avisar_equipo(asignaciones, progress_callback) -> int:
    """Una línea por vuelo cuyo `Equipo_de_vuelo` no cuadra con el modelo EXIF
    de alguna de sus imágenes. Solo avisa. Devuelve cuántos vuelos discrepan."""
    discrepancias: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for dato, ventana in asignaciones:
        if ventana is None or not ventana.get("equipo"):
            continue
        if equipo_mod.equipo_coincide(ventana["equipo"], dato.modelo) is False:
            _texto, modelos = discrepancias.setdefault(
                (ventana["pb"], ventana["vuelo"]), (ventana["equipo"], set()))
            modelos.add(str(dato.modelo).strip("\x00").strip())
    for (pb, vuelo), (texto, modelos) in discrepancias.items():
        progress_callback.emit(
            f"\nAVISO: PB{pb} vuelo {vuelo}: el estadillo dice '{texto}' pero el EXIF "
            f"es {', '.join(sorted(modelos))}. Revisa el estadillo.\n")
    return len(discrepancias)
```

- En `construir_indice`, tras `asignaciones = [...]`: `vuelos_equipo_discrepa = _avisar_equipo(asignaciones, progress_callback)`; añadir `"vuelos_equipo_discrepa": vuelos_equipo_discrepa` al `return`.

- [ ] **Step 4: Verificar**

Run: `.venv/bin/python -m pytest -q tests/test_equipo.py tests/test_indice_posicion.py tests/test_organizado_plan_apply_e2e.py tests/test_clasificar_tipo_point.py $(ls tests/test_indice*.py)`
Expected: PASS salvo `test_tras_un_run_limpio_no_queda_el_manifiesto_en_el_arbol_entregado` si ya fallara por otra causa (se cambia en Task 5). Reportar cualquier otro fallo sin tocar tests ajenos.

- [ ] **Step 5: Reportar.** Sin commit.

---

### Task 3: meta/location desde el manifiesto (sin releer imágenes)

**Files:**
- Modify: `exif.py` (`MetaLocation.gen_meta_location`, l.959-1090)
- Modify: `atom_core/cierre.py` (`_emitir_meta_location` l.147-186, `emitir_csvs` l.189-201)
- Test: `tests/test_cierre_meta_location_manifiesto.py` (nuevo)

**Interfaces:**
- Consumes: Task 1 (columnas `meta_leida`, `lat`, `lon`, `altitud_abs`, `altura_relativa`, `gimbal_yaw`, `gimbal_pitch`), Task 2 (`indice._leer_metadatos`, `indice.campos_posicion`, `indice._nombre_carpeta_vuelo`, `indice.NOMBRE_CARPETA_RGB_EXTRA`).
- Produces:
  - `MetaLocation.leer_exif_imagen(ruta: str, progress_callback) -> tuple` → `(coords, gimbal, xmp_data)` o `(None, None, None)`
  - `MetaLocation.df_desde_lecturas(images: list[str], lecturas: list[tuple], progress_callback, progress_bar, flight_height: float, calculate_proyected_distance: bool) -> pd.DataFrame`
  - `MetaLocation.publicar_csv(df: pd.DataFrame, input_folder: str, filename: str, csv_folder: str, progress_callback) -> str | None`
  - `cierre.emitir_csvs(manifiesto, cfg, progress_callback, proyecciones: dict | None = None) -> dict[str, str]`; si `proyecciones` es un dict, se rellena `{ruta_salida_original: (CalculatedDistance, LatitudFoto, LongitudFoto)}`

- [ ] **Step 1: Refactor sin cambio de comportamiento en `exif.py`**

Partir `gen_meta_location` en tres métodos y dejar `gen_meta_location` llamándolos. Mover código tal cual, sin cambiar emits ni orden:

```python
    def leer_exif_imagen(self, ruta: str, progress_callback) -> tuple:
        """(coords, gimbal, xmp_data) de una imagen; (None, None, None) sin EXIF.
        Antes era el cierre `_leer_exif` de `gen_meta_location`."""
        # CUERPO: el de `_leer_exif` actual, con `ruta` recibido en vez de
        # `unir(input_folder, image)`, comentarios incluidos.

    def df_desde_lecturas(self, images, lecturas, progress_callback, progress_bar,
                          flight_height: float, calculate_proyected_distance: bool) -> pd.DataFrame:
        """DataFrame meta/location a partir de lecturas ya hechas, en el orden de
        `images` (índice = posición, igual que siempre). Incluye la corrección
        de gimbal a cero (`check_gimbal_yaw_pitch_values`)."""
        # CUERPO: desde `image_theoretical_position = dict()` + `nombresColumnas` + `df = pd.DataFrame(...)`
        # hasta `df = self.check_gimbal_yaw_pitch_values(...)` inclusive, usando
        # `lecturas[indice]` donde hoy usa `exif_por_imagen[indice]`. `return df`.

    def publicar_csv(self, df: pd.DataFrame, input_folder: str, filename: str,
                     csv_folder: str, progress_callback) -> str | None:
        """Ordena por fecha y escribe `<carpeta>_<filename>` en la carpeta y en
        `csv_folder`, sin cabecera. `None` si no hay filas."""
        if df.empty:
            return None
        # CUERPO: el bloque actual `progress_callback.emit("\nGenerando csv: " ...)`
        # hasta el `shutil.copy2(csv_generado, csv_folder)` inclusive.
        return csv_generado
```

`gen_meta_location` queda:

```python
        images = self.utils_obj.get_images_from_dir(input_folder, ["_CROP"], solo_fuente=True)
        if len(images) > 0:
            progress_callback.emit(...)   # los dos avisos "Procesando N imágenes" de hoy
            self.organizer_logger.logger.info(...)
        if images and not self.stop:
            with ThreadPoolExecutor(max_workers=utils.max_io_workers()) as executor:
                lecturas = list(executor.map(
                    lambda image: self.leer_exif_imagen(unir(input_folder, image), progress_callback), images))
        else:
            lecturas = [(None, None, None)] * len(images)
        df = self.df_desde_lecturas(images, lecturas, progress_callback, progress_bar,
                                    flight_height, calculate_proyected_distance)
        if len(images) > 0:
            self.publicar_csv(df, input_folder, filename, csv_folder, progress_callback)
```

Mantener el comentario largo final sobre location.csv en TERMICA.

Run: `.venv/bin/python -m pytest -q tests/test_meta_location.py tests/test_meta_location_altitud_siempre.py tests/test_meta_location_sin_location_en_termica.py tests/test_exif_defensive.py`
Expected: PASS (refactor puro).

- [ ] **Step 2: Tests que fallan** — crear `tests/test_cierre_meta_location_manifiesto.py`:

```python
"""meta/location desde el manifiesto == meta/location releyendo imágenes.

Se construye el mismo árbol de salida dos veces: en uno corre el camino de
siempre (`MetaLocation.check_input_folder_and_iterate`), en el otro el cierre
nuevo alimentado por filas de manifiesto. Los CSV tienen que ser idénticos
byte a byte."""
import datetime as dt
import os
import shutil
from types import SimpleNamespace

import pytest

import exif
from atom_core import cierre, indice
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import OrganizerLogger


class _Cb:
    def emit(self, *a, **k):
        pass


# (tipo, nombre, lat, lon, yaw, pitch, rel_alt)
_IMAGENES = [
    ("RGB", "20260115_100005_DJI_0002_D.JPG", 37.10001, -5.60001, 12.5, -90.0, 50.0),
    ("RGB", "20260115_100000_DJI_0001_D.JPG", 37.10000, -5.60000, 0.0, -45.0, 50.0),
    ("RGB", "20260115_100010_DJI_0003_D.JPG", 37.10002, -5.60002, 30.0, -60.0, 51.5),
    ("TERMICA", "20260115_100000_DJI_0001_T.JPG", 37.10000, -5.60000, -88.1, -90.0, 50.0),
    ("TERMICA", "20260115_100005_DJI_0002_T.JPG", 37.10001, -5.60001, 91.3, -30.0, 49.0),
    ("RGB_Extra", "20260115_100000_DJI_0001_W.JPG", 37.10000, -5.60000, 5.0, -90.0, 50.0),
]


def _arbol(raiz, make_dji_jpeg):
    for tipo, nombre, lat, lon, yaw, pitch, rel in _IMAGENES:
        carpeta = raiz / tipo / "PB1" / "PB1_V1"
        carpeta.mkdir(parents=True, exist_ok=True)
        make_dji_jpeg(str(carpeta / nombre), lat=lat, lon=lon, gimbal_yaw=yaw,
                      gimbal_pitch=pitch, relative_altitude=rel,
                      dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))
    (raiz / "CSVs").mkdir()


def _cfg(raiz, calcular):
    return SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                           calculate_proyected_distance=calcular, gen_meta_location=True)


def _manifiesto(raiz, logger, con_metadatos):
    manifiesto = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    manifiesto.crear_esquema()
    gi = exif.GeneralInformationFromImage(logger)
    filas = []
    for tipo, nombre, *_ in _IMAGENES:
        ruta = str(raiz / tipo / "PB1" / "PB1_V1" / nombre)
        extra = indice.campos_posicion(indice._leer_metadatos(ruta, gi, _Cb())) if con_metadatos else {}
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None,
            pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=ruta, ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta), **extra))
    manifiesto.insertar_o_reabrir(filas)
    for fila in manifiesto.todas():
        manifiesto.marcar_hecha(fila["id"], "")
    return manifiesto


def _csvs(raiz):
    return {os.path.relpath(os.path.join(d, f), raiz): open(os.path.join(d, f), "rb").read()
            for d, _s, fs in os.walk(raiz) if ".organizado" not in d
            for f in fs if f.endswith(".csv")}


@pytest.mark.parametrize("calcular", [True, False])
@pytest.mark.parametrize("con_metadatos", [True, False])
def test_csv_desde_manifiesto_identico_al_actual(tmp_path, make_dji_jpeg, logger, calcular, con_metadatos):
    ref, nuevo = tmp_path / "ref", tmp_path / "nuevo"
    _arbol(ref, make_dji_jpeg)
    shutil.copytree(ref, nuevo)

    ml = exif.MetaLocation(OrganizerLogger("t", create_file_handler=False))
    ml.total_images_number = len(_IMAGENES)
    assert ml.check_input_folder_and_iterate(str(ref), _Cb(), _Cb(), str(ref / "CSVs"), 50.0, calcular)

    manifiesto = _manifiesto(nuevo, logger, con_metadatos)
    proyecciones = {}
    cierre._emitir_meta_location(manifiesto, _cfg(nuevo, calcular), _Cb(), proyecciones)

    esperado = _csvs(ref)
    assert esperado, "el camino de referencia no generó CSV: test mal montado"
    assert _csvs(nuevo) == esperado
    if calcular:
        assert len(proyecciones) == len(_IMAGENES)


def test_cierre_no_relee_imagenes_con_metadatos(tmp_path, make_dji_jpeg, logger, monkeypatch):
    raiz = tmp_path / "d"
    _arbol(raiz, make_dji_jpeg)
    manifiesto = _manifiesto(raiz, logger, con_metadatos=True)
    monkeypatch.setattr(exif.MetaLocation, "leer_exif_imagen",
                        lambda *a, **k: pytest.fail("releyó una imagen con meta_leida=1"))
    cierre._emitir_meta_location(manifiesto, _cfg(raiz, True), _Cb(), {})
```

- [ ] **Step 3: Verificar que fallan**

Run: `.venv/bin/python -m pytest -q tests/test_cierre_meta_location_manifiesto.py`
Expected: FAIL (`TypeError: _emitir_meta_location() takes 2 positional arguments`).

- [ ] **Step 4: Implementación en `atom_core/cierre.py`**

Import: `from atom_core.indice import NOMBRE_CARPETA_RGB_EXTRA, TIPOS_RGB, _nombre_carpeta_vuelo`.

Sustituir `_emitir_meta_location` entero:

```python
# Mismo recorrido que `MetaLocation.check_input_folder_and_iterate` (exif.py:1119):
# RGB, TERMICA y, si existe, RGB_Extra, en ESTE orden. El location de RGB_Extra
# pisa en `CSVs/` al de RGB homónimo igual que hoy.
_RAICES_META_LOCATION = (("RGB", "location.csv"), ("TERMICA", "meta.csv"),
                         (NOMBRE_CARPETA_RGB_EXTRA, "location.csv"))


def _lectura_de_fila(meta_location_obj, fila, progress_callback) -> tuple:
    """(coords, gimbal, xmp_data) como los devolvería `leer_exif_imagen`, pero
    desde el manifiesto. Filas sin posición leída (migradas, `gs://…`) caen a
    releer SU fichero de salida, como siempre."""
    if fila["meta_leida"] and fila["gimbal_yaw"] is not None:
        if fila["lat"] is None:
            return None, None, None
        nombre = os.path.basename(fila["ruta_salida_original"])
        return ((nombre, fila["lat"], fila["lon"], None),
                [fila["gimbal_yaw"], fila["gimbal_pitch"]],
                [fila["altitud_abs"], fila["altura_relativa"]])
    return meta_location_obj.leer_exif_imagen(fila["ruta_salida_original"], progress_callback)


def _emitir_meta_location(manifiesto, cfg, progress_callback, proyecciones=None) -> dict[str, str]:
    """Emite `meta.csv`/`location.csv` desde el manifiesto.

    Antes reabría TODAS las imágenes de salida (≈300 s en MELINESTI). Ahora la
    posición viene del índice y solo se relee lo que no la tenga. La
    construcción del DataFrame y la escritura son las MISMAS funciones de
    `exif.MetaLocation` (`df_desde_lecturas`, `publicar_csv`): mismo orden,
    misma corrección de gimbal, mismo formato. El `import` es perezoso: `exif.py`
    arrastra `pyexiv2`/`geopy`/`exifread`."""
    import exif  # noqa: PLC0415 (import perezoso, ver docstring)
    from natsort import natsorted  # noqa: PLC0415
    from rjpeg_a_tiff import EXTS_FUENTE  # noqa: PLC0415

    organizer_logger = utils.OrganizerLogger("cierre_organizado", create_file_handler=False)
    meta_location_obj = exif.MetaLocation(organizer_logger)
    csv_folder = unir(cfg.output_folder, "CSVs")

    if not (existe_ruta(unir(cfg.output_folder, "TERMICA")) and existe_ruta(unir(cfg.output_folder, "RGB"))):
        progress_callback.emit("\nNo se han podido generar los archivos meta y location.\n")
        return {}

    grupos: "dict[tuple[str, str, str], list]" = {}
    for fila in manifiesto.todas():
        if fila["estado"] != "hecho" or fila["unassigned"] or not fila["pb"] or not fila["vuelo"]:
            continue
        grupos.setdefault((fila["tipo"], fila["pb"], fila["vuelo"]), []).append(fila)
    meta_location_obj.total_images_number = sum(len(f) for f in grupos.values())

    rutas_emitidas: dict[str, str] = {}
    try:
        for tipo, nombre_csv in _RAICES_META_LOCATION:
            for (tipo_grupo, pb, vuelo), filas in grupos.items():
                if tipo_grupo != tipo:
                    continue
                carpeta = unir(cfg.output_folder, tipo, f"PB{pb}",
                               _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v))
                # Mismo filtro que `get_images_from_dir(..., ["_CROP"], solo_fuente=True)`.
                por_nombre = {}
                for fila in filas:
                    nombre = os.path.basename(fila["ruta_salida_original"])
                    if os.path.splitext(nombre)[1].lower() in EXTS_FUENTE and "_CROP" not in nombre:
                        por_nombre[nombre] = fila
                images = natsorted(por_nombre)
                if not images:
                    continue
                progress_callback.emit(
                    "\nProcesando {0} imágenes en directorio {1}".format(len(images), carpeta) + "\n")
                lecturas = [_lectura_de_fila(meta_location_obj, por_nombre[n], progress_callback)
                            for n in images]
                df = meta_location_obj.df_desde_lecturas(
                    images, lecturas, progress_callback, progress_callback,
                    cfg.flight_height, cfg.calculate_proyected_distance)
                meta_location_obj.publicar_csv(df, carpeta, nombre_csv, csv_folder, progress_callback)
                if proyecciones is not None and cfg.calculate_proyected_distance:
                    for _indice, linea in df.iterrows():
                        proyecciones[por_nombre[linea["Foto"]]["ruta_salida_original"]] = (
                            linea["CalculatedDistance"], linea["LatitudFoto"], linea["LongitudFoto"])
        rutas_emitidas["meta_location"] = csv_folder
    except Exception as excepcion:  # pragma: no cover - salvaguarda
        # No tumbar el cierre entero: criterio y verificaciones tienen que completarse.
        progress_callback.emit(f"\nERROR generando meta/location: {excepcion}\n")
    return rutas_emitidas
```

`emitir_csvs`:

```python
def emitir_csvs(manifiesto, cfg, progress_callback, proyecciones: dict | None = None) -> dict[str, str]:
    """... (docstring actual) ... Si `proyecciones` es un dict, se rellena con
    `{ruta_salida_original: (CalculatedDistance, LatitudFoto, LongitudFoto)}`
    para el índice Excel."""
    rutas_emitidas = _emitir_csv_criterio(manifiesto, cfg, progress_callback)
    if cfg.gen_meta_location:
        rutas_emitidas.update(_emitir_meta_location(manifiesto, cfg, progress_callback, proyecciones))
    return rutas_emitidas
```

Nota: si `copytree`/`check_input_folder_and_iterate` de referencia fija `Foto` con otro valor que `basename(ruta_salida_original)`, el test lo detecta; NO ajustar el test para que pase, reportar.

- [ ] **Step 5: Verificar**

Run: `.venv/bin/python -m pytest -q tests/test_cierre_meta_location_manifiesto.py tests/test_cierre_organizado.py tests/test_meta_location*.py tests/test_organizado_plan_apply_e2e.py`
Expected: PASS (salvo el test e2e del borrado de `.organizado`, que cambia en Task 5).

- [ ] **Step 6: Reportar.** Sin commit.

---

### Task 4: Excel `INDICE_<PLANTA>.xlsx`

**Files:**
- Create: `atom_core/indice_excel.py`
- Test: `tests/test_indice_excel.py`

**Interfaces:**
- Consumes: Task 1 (`Manifiesto.todas()`, `Manifiesto.ejecuciones()`, columnas nuevas), Task 2 (`atom_core.equipo.equipo_coincide`, `texto_coincide`).
- Produces:
  - `COLUMNAS: tuple[str, ...]`
  - `nombre_planta(output_folder: str) -> str`
  - `ruta_indice(output_folder: str) -> str`
  - `escribir_indice(manifiesto, cfg, proyecciones: dict, progress_callback) -> str` (ruta final escrita)

- [ ] **Step 1: Tests que fallan** — crear `tests/test_indice_excel.py`:

```python
import os
from types import SimpleNamespace

from openpyxl import load_workbook

from atom_core import indice_excel
from atom_core.manifiesto import FilaManifiesto, Manifiesto


class _Cb:
    def __init__(self):
        self.lineas = []

    def emit(self, v):
        self.lineas.append(v)


def _preparar(tmp_path):
    destino = tmp_path / "MELINESTI"
    (destino / ".organizado").mkdir(parents=True)
    m = Manifiesto(destino / ".organizado" / "manifiesto.db")
    m.crear_esquema()
    ej = m.abrir_ejecucion("/media/sd1", "3.4.93")
    orig = str(destino / "RGB" / "PB1" / "PB1_V1" / "20260115_100000_DJI_0001_D.JPG")
    m.insertar_o_reabrir([
        FilaManifiesto(ruta_origen="/media/sd1/DJI_0001_D.JPG", tipo="RGB",
                       timestamp_exif="2026-01-15T10:00:00", modelo="M4T", make="DJI",
                       equipo_estadillo="DJI M300", pb="1", vuelo="1",
                       nombre_nuevo="20260115_100000_DJI_0001_D.JPG", angulo_giro=270,
                       pct_recorte=0.8, comprime=True, ruta_salida_original=orig,
                       ruta_salida_crop=orig.replace(".JPG", "_CROP.JPG"), ruta_salida_tiff=None,
                       unassigned=False, bytes_origen=9000, meta_leida=True, lat=37.1, lon=-5.6,
                       gimbal_yaw="+12.50", gimbal_pitch="-90.00", altura_relativa="+50.000",
                       ancho_px=4032, alto_px=3024),
        FilaManifiesto(ruta_origen="/media/sd1/DJI_9999_D.JPG", tipo="RGB", timestamp_exif=None,
                       modelo=None, pb=None, vuelo=None, nombre_nuevo="", angulo_giro=0,
                       pct_recorte=None, comprime=True,
                       ruta_salida_original=str(destino / "SIN_ORDENAR" / "RGB" / "DJI_9999_D.JPG"),
                       ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=True),
    ], ejecucion_id=ej)
    filas = m.todas()
    m.marcar_hecha(filas[0]["id"], "")
    m.marcar_fallida(filas[1]["id"], "sin timestamp")
    cfg = SimpleNamespace(output_folder=str(destino), flight_height=50.0,
                          calculate_proyected_distance=True)
    return m, cfg, orig


def test_escribe_una_fila_por_imagen_con_columnas_comunes(tmp_path):
    m, cfg, orig = _preparar(tmp_path)
    ruta = indice_excel.escribir_indice(m, cfg, {orig: (0.0, 37.1, -5.6)}, _Cb())

    assert ruta == os.path.join(cfg.output_folder, "INDICE_MELINESTI.xlsx")
    ws = load_workbook(ruta, read_only=True)["Imagenes"]
    filas = list(ws.iter_rows(values_only=True))
    assert filas[0] == indice_excel.COLUMNAS
    assert len(filas) == 3
    fila = dict(zip(indice_excel.COLUMNAS, filas[1]))
    assert fila["Vuelo"] == "1" and fila["Tipo"] == "RGB" and fila["SinOrdenar"] == "No"
    assert fila["NombreOriginal"] == "DJI_0001_D.JPG"
    assert fila["TimestampEXIF"] == "2026-01-15 10:00:00"
    assert (fila["Make"], fila["Model"], fila["EquipoEstadillo"], fila["EquipoCoincide"]) == ("DJI", "M4T", "DJI M300", "No")
    assert (fila["Lat"], fila["GimbalYaw"], fila["AlturaRelativa"]) == (37.1, 12.5, 50.0)
    assert (fila["AnguloGiro"], fila["PctRecorte"], fila["Comprime"]) == (270, 80.0, "Sí")
    assert fila["RutaOriginal"] == os.path.join("RGB", "PB1", "PB1_V1", "20260115_100000_DJI_0001_D.JPG")
    assert fila["CalculatedDistance"] == 0.0 and fila["AlturaVuelo"] == 50.0
    assert fila["Ejecucion"] == 1 and fila["FechaEjecucion"]
    fallida = dict(zip(indice_excel.COLUMNAS, filas[2]))
    assert (fallida["Estado"], fallida["MotivoFallo"], fallida["SinOrdenar"]) == ("fallido", "sin timestamp", "Sí")
    assert (fallida["Make"], fallida["EquipoCoincide"]) == (None, None)


def test_excel_abierto_escribe_con_otro_nombre_y_avisa(tmp_path, monkeypatch):
    m, cfg, _orig = _preparar(tmp_path)
    bloqueado = indice_excel.ruta_indice(cfg.output_folder)
    replace_real = os.replace

    def _replace(origen, destino):
        if os.path.abspath(destino) == os.path.abspath(bloqueado):
            raise PermissionError("abierto en Excel")
        return replace_real(origen, destino)

    monkeypatch.setattr(indice_excel.os, "replace", _replace)
    cb = _Cb()
    ruta = indice_excel.escribir_indice(m, cfg, {}, cb)

    assert ruta != bloqueado
    assert os.path.basename(ruta).startswith("INDICE_MELINESTI_") and ruta.endswith(".xlsx")
    assert os.path.isfile(ruta)
    assert any("abierto" in l for l in cb.lineas)
    assert not [f for f in os.listdir(cfg.output_folder) if f.endswith(".tmp")]
```

- [ ] **Step 2: Verificar que fallan**

Run: `.venv/bin/python -m pytest -q tests/test_indice_excel.py`
Expected: FAIL (`ImportError: cannot import name 'indice_excel'`).

- [ ] **Step 3: Implementación** — crear `atom_core/indice_excel.py`:

```python
"""Índice Excel de la planta: `<destino>/INDICE_<PLANTA>.xlsx`, una fila por
imagen del manifiesto.

Se regenera ENTERO en cada cierre desde el manifiesto (la fuente); nunca se
lee de vuelta. Organizar por cachitos sobre el mismo destino hace que el
Excel acumule solo, porque el manifiesto acumula."""
from __future__ import annotations

import datetime
import os
import tempfile

from openpyxl import Workbook

from atom_core.almacen import es_uri_gcs, publicar_en
from atom_core.equipo import equipo_coincide, texto_coincide

COLUMNAS = (
    "PB", "Vuelo", "Tipo", "SinOrdenar",
    "NombreOriginal", "NombreNuevo", "TimestampEXIF", "Make", "Model", "EquipoEstadillo", "EquipoCoincide",
    "AnchoPx", "AltoPx", "BytesOrigen",
    "Lat", "Lon", "AltitudAbs", "AlturaRelativa", "GimbalYaw", "GimbalPitch", "GimbalRoll",
    "FlightYaw", "AlturaVuelo", "CalculatedDistance", "LatitudFoto", "LongitudFoto",
    "AnguloGiro", "PctRecorte", "Comprime", "RutaOriginal", "RutaCrop", "RutaTIFF",
    "Estado", "MotivoFallo", "Ejecucion", "FechaEjecucion", "RutaOrigen",
)


def nombre_planta(output_folder: str) -> str:
    return os.path.basename(str(output_folder).rstrip("/\\")) or "PLANTA"


def ruta_indice(output_folder: str) -> str:
    nombre = f"INDICE_{nombre_planta(output_folder)}.xlsx"
    if es_uri_gcs(output_folder):
        return f"{output_folder.rstrip('/')}/{nombre}"
    return os.path.join(output_folder, nombre)


def _numero(valor):
    """El XMP llega como texto (`+12.50`): en el Excel va como número para
    poder filtrar y ordenar. Lo que no sea número se deja tal cual."""
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return valor


def _relativa(ruta: str | None, base: str) -> str | None:
    if not ruta:
        return None
    if es_uri_gcs(base):
        prefijo = base.rstrip("/") + "/"
        return ruta[len(prefijo):] if ruta.startswith(prefijo) else ruta
    try:
        return os.path.relpath(ruta, base)
    except ValueError:  # otra unidad en Windows
        return ruta


def _si_no(valor) -> str:
    return "Sí" if valor else "No"


def _filas(manifiesto, cfg, proyecciones):
    ejecuciones = manifiesto.ejecuciones()
    base = cfg.output_folder
    for fila in manifiesto.todas():
        ejecucion = ejecuciones.get(fila["ejecucion_id"])
        distancia, lat_foto, lon_foto = proyecciones.get(fila["ruta_salida_original"], (None, None, None))
        pct = fila["pct_recorte"]
        yield (
            fila["pb"], fila["vuelo"], fila["tipo"], _si_no(fila["unassigned"]),
            fila["nombre_original"], fila["nombre_nuevo"] or None,
            fila["timestamp_exif"].replace("T", " ") if fila["timestamp_exif"] else None,
            fila["make"], fila["modelo"], fila["equipo_estadillo"],
            texto_coincide(equipo_coincide(fila["equipo_estadillo"], fila["modelo"])),
            fila["ancho_px"], fila["alto_px"], fila["bytes_origen"],
            fila["lat"], fila["lon"], _numero(fila["altitud_abs"]), _numero(fila["altura_relativa"]),
            _numero(fila["gimbal_yaw"]), _numero(fila["gimbal_pitch"]), _numero(fila["gimbal_roll"]),
            _numero(fila["flight_yaw"]),
            cfg.flight_height if cfg.calculate_proyected_distance else None,
            _numero(distancia), _numero(lat_foto), _numero(lon_foto),
            fila["angulo_giro"], round(pct * 100, 2) if pct is not None else None, _si_no(fila["comprime"]),
            _relativa(fila["ruta_salida_original"], base), _relativa(fila["ruta_salida_crop"], base),
            _relativa(fila["ruta_salida_tiff"], base),
            fila["estado"], fila["motivo_fallo"], fila["ejecucion_id"],
            ejecucion["inicio"] if ejecucion is not None else None, fila["ruta_origen"],
        )


def escribir_indice(manifiesto, cfg, proyecciones: dict, progress_callback) -> str:
    """Escribe el índice y devuelve la ruta final. Si el fichero está abierto
    (Excel en Windows bloquea el `.xlsx`), escribe al lado con marca de hora y
    avisa: un índice bloqueado no puede tumbar el cierre."""
    libro = Workbook(write_only=True)
    hoja = libro.create_sheet("Imagenes")
    hoja.freeze_panes = "A2"
    hoja.append(list(COLUMNAS))
    for fila in _filas(manifiesto, cfg, proyecciones):
        hoja.append(list(fila))

    destino = ruta_indice(cfg.output_folder)
    if es_uri_gcs(destino):
        descriptor, temporal = tempfile.mkstemp(suffix=".xlsx")
        os.close(descriptor)
        try:
            libro.save(temporal)
            publicar_en(temporal, destino)
        finally:
            os.unlink(temporal)
        return destino

    # Temporal en la MISMA carpeta: `os.replace` entre discos falla (EXDEV).
    temporal = destino + ".tmp"
    libro.save(temporal)
    try:
        os.replace(temporal, destino)
        return destino
    except PermissionError:
        marca = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alternativo = os.path.join(os.path.dirname(destino),
                                   f"INDICE_{nombre_planta(cfg.output_folder)}_{marca}.xlsx")
        os.replace(temporal, alternativo)
        progress_callback.emit(
            f"\nAVISO: {os.path.basename(destino)} está abierto; el índice se ha "
            f"guardado como {os.path.basename(alternativo)}.\n")
        return alternativo
    finally:
        if os.path.exists(temporal):
            os.unlink(temporal)
```

Si `write_only` no admite `freeze_panes`, quitar `write_only=True` (38k filas caben) y reportarlo.

- [ ] **Step 4: Verificar**

Run: `.venv/bin/python -m pytest -q tests/test_indice_excel.py`
Expected: PASS.

- [ ] **Step 5: Reportar.** Sin commit.

---

### Task 5: Orquestación, guard, subida y empaquetado

**Files:**
- Modify: `atom_core/phases.py` (`organizar_plan_apply`, ~l.1030-1165)
- Modify: `atom_core/organize.py:776-792`
- Modify: `atom_core/cloud_upload.py:203-210`
- Modify: `atom_organizer_webview.spec`, `atom_organizer_webview_linux.spec`
- Test: `tests/test_organizado_plan_apply_e2e.py`, `tests/test_organize_stats_fases.py`, `tests/test_cloud_upload.py`

**Interfaces:**
- Consumes: Task 1 (`abrir_ejecucion`, `cerrar_ejecucion`), Task 2 (`construir_indice(..., ejecucion_id=)` y sus claves `nuevas/saltadas/reintentadas`), Task 3 (`emitir_csvs(..., proyecciones=)`), Task 4 (`indice_excel.escribir_indice`, `indice_excel.ruta_indice`).

- [ ] **Step 1: Tests que fallan**

1a. En `tests/test_organizado_plan_apply_e2e.py`, sustituir `test_tras_un_run_limpio_no_queda_el_manifiesto_en_el_arbol_entregado` por:

```python
def test_tras_un_run_limpio_el_manifiesto_y_el_indice_se_quedan(_inspeccion, logger, monkeypatch):
    """Decisión 2026-09-15: el manifiesto es la memoria del destino para
    organizar por cachitos, así que ya NO se borra tras un cierre limpio. La
    subida lo excluye (`cloud_upload.build_plan`)."""
    from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO

    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    _correr(host, cfg, monkeypatch)

    assert os.path.isfile(os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db"))
    assert os.path.isfile(os.path.join(cfg.output_folder, "INDICE_destino.xlsx"))


def test_segundo_cachito_acumula_salta_lo_hecho_y_reutiliza_el_angulo(tmp_path, make_dji_jpeg, logger, monkeypatch):
    """Cachito 1: RGB del vuelo con yaw ~90 -> ángulo 90. Cachito 2 (otra
    carpeta de origen): repite una imagen del 1 y trae una nueva con yaw 0.
    La repetida se salta y se avisa; la nueva entra con el ángulo del vuelo
    ya decidido (90), no con el recalculado (0)."""
    from openpyxl import load_workbook
    from atom_core.manifiesto import Manifiesto, NOMBRE_CARPETA_MANIFIESTO

    ts = dt.datetime(2026, 1, 15, 10, 0, 0)
    sd1, sd2 = tmp_path / "sd1", tmp_path / "sd2"
    sd1.mkdir()
    sd2.mkdir()
    make_dji_jpeg(str(sd1 / "DJI_0001_D.JPG"), dt_val=ts, gimbal_yaw=90.0)
    make_dji_jpeg(str(sd1 / "DJI_0002_D.JPG"), dt_val=ts + dt.timedelta(seconds=5), gimbal_yaw=90.0)
    import shutil as _sh
    _sh.copy2(sd1 / "DJI_0002_D.JPG", sd2 / "DJI_0002_D.JPG")
    make_dji_jpeg(str(sd2 / "DJI_0003_D.JPG"), dt_val=ts + dt.timedelta(seconds=9), gimbal_yaw=0.0)
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", "2026:01:15", "09:55:00", "10:05:00")])

    cfg1 = _cfg(tmp_path, input_folder=str(sd1), convert_to_tif=False)
    _correr(_HostDePrueba(logger), cfg1, monkeypatch)
    cfg2 = _cfg(tmp_path, input_folder=str(sd2), convert_to_tif=False)
    pcb, _pbar, _psum = _correr(_HostDePrueba(logger), cfg2, monkeypatch)

    m = Manifiesto(os.path.join(cfg2.output_folder, NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db"))
    filas = m.todas()
    assert sorted(f["nombre_original"] for f in filas) == ["DJI_0001_D.JPG", "DJI_0002_D.JPG", "DJI_0003_D.JPG"]
    assert {f["angulo_giro"] for f in filas} == {90}
    assert all(f["estado"] == "hecho" for f in filas)
    assert len(m.ejecuciones()) == 2
    assert any("ya estaban organizadas" in str(linea) for linea in pcb.lineas)
    assert len(os.listdir(os.path.join(cfg2.output_folder, "RGB", "PB1", "PB1_V1"))) == 3
    hoja = load_workbook(os.path.join(cfg2.output_folder, "INDICE_destino.xlsx"), read_only=True)["Imagenes"]
    assert len(list(hoja.iter_rows(values_only=True))) == 4
```

Antes de escribirlo, comprobar en el fichero el nombre real del atributo que acumula líneas en `_SignalFalsa` (`lineas` u otro) y usarlo; y que `_cfg` acepta overrides de `input_folder` (sí: `base.update(overrides)`).

1b. En `tests/test_organize_stats_fases.py`, clase `TestGuardCarpetaSalidaSplitImages`, añadir:

```python
    def test_no_aborta_si_hay_residuo_pero_existe_manifiesto(self, monkeypatch, tmp_path):
        """Destino ya organizado por un cachito anterior: se acumula."""
        destino = tmp_path / "salida"
        (destino / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True)
        (destino / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db").write_bytes(b"")
        (destino / "RGB").mkdir()

        eventos = self._run_split(monkeypatch, destino)

        assert not any("no está vacía" in str(e) for e in _de_tipo(eventos, "error"))
        assert _de_tipo(eventos, "done")
```

1c. En `tests/test_cloud_upload.py`, junto a los `test_build_plan_*`:

```python
def test_build_plan_nunca_sube_el_manifiesto(tmp_path):
    root = tmp_path / "planta"
    (root / ".organizado").mkdir(parents=True)
    (root / ".organizado" / "manifiesto.db").write_bytes(b"x")
    (root / ".organizado" / "foto.jpg").write_bytes(b"x")
    (root / "a.jpg").write_bytes(b"x")
    plan = cu.build_plan(root, prefix="p", suffixes=())
    assert [i.remote for i in plan.items] == ["p/a.jpg"]
```

(Verificar que `build_plan` tiene el parámetro `suffixes`; si el nombre difiere, usar el real.)

- [ ] **Step 2: Verificar que fallan**

Run: `.venv/bin/python -m pytest -q tests/test_organizado_plan_apply_e2e.py tests/test_organize_stats_fases.py tests/test_cloud_upload.py`
Expected: FAIL en los 4 tests nuevos.

- [ ] **Step 3: Implementación**

3a. `atom_core/phases.py`:
- Imports: `from atom_core import indice_excel` y `from version import __version__`.
- Tras `reabiertas = ...` y su aviso: `ejecucion_id = manifiesto.abrir_ejecucion(cfg.input_folder, __version__)`.
- `indice_mod.construir_indice(...)` → `resumen_indice = indice_mod.construir_indice(cfg, adaptador, self.split_images_obj.exif_management_obj, manifiesto, progress_callback, progress_bar, progress_summarize, ejecucion_id=ejecucion_id)`.
- Sustituir `cierre_mod.emitir_csvs(manifiesto, cfg, progress_callback)` por:

```python
            proyecciones: dict = {}
            cierre_mod.emitir_csvs(manifiesto, cfg, progress_callback, proyecciones=proyecciones)
```

- Tras `problemas = cierre_mod.verificar(manifiesto, cfg)`:

```python
            # El índice Excel es informativo: si falla, se avisa y el cierre sigue.
            try:
                ruta_indice = indice_excel.escribir_indice(manifiesto, cfg, proyecciones, progress_callback)
                progress_callback.emit(f"\nÍndice de la planta: {ruta_indice}\n")
            except Exception as excepcion:  # noqa: BLE001
                progress_callback.emit(f"\nAVISO: no se pudo generar el índice Excel: {excepcion}\n")
            manifiesto.cerrar_ejecucion(
                ejecucion_id, (resumen_indice or {}).get("nuevas", 0),
                (resumen_indice or {}).get("saltadas", 0), (resumen_indice or {}).get("reintentadas", 0))
```

- Borrar `cierre_limpio = False`, `cierre_limpio = True` y el bloque `if cierre_limpio: shutil.rmtree(...)` del `finally` (queda solo `manifiesto.cerrar()`). Reescribir el párrafo del docstring "Por eso el borrado NO es incondicional..." por: "`.organizado/` se conserva SIEMPRE: es la memoria del destino para organizar por cachitos (decisión 2026-09-15). La subida lo excluye." y el comentario del `finally` en consecuencia.

3b. `atom_core/organize.py` en el guard, tras calcular `_restos`:

```python
            # Destino ya organizado por un cachito anterior: su manifiesto sabe
            # qué hay dentro, así que se acumula en vez de abortar.
            _hay_manifiesto = bool(_out) and os.path.isfile(
                os.path.join(_out, NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db"))
            if _guard_activo and _restos and _hay_manifiesto:
                emit("log", f"El destino ya tiene un organizado previo (\"{_out}\"): "
                            "se acumula sobre él y se saltan las imágenes ya hechas.")
            if _guard_activo and _restos and not _hay_manifiesto:
```

(la última línea sustituye a `if _guard_activo and _restos:`; el cuerpo del error no cambia).

3c. `atom_core/cloud_upload.py` en `build_plan`, tras `if not path.is_file(): continue`:

```python
        # El manifiesto del organizado vive dentro del destino (memoria para
        # organizar por cachitos) pero nunca se entrega.
        if ".organizado" in path.relative_to(root).parts:
            continue
```

3d. Specs: en `atom_organizer_webview.spec` y `atom_organizer_webview_linux.spec`, junto a los otros `collect_all`, añadir `openpyxl_datas, openpyxl_binaries, openpyxl_hidden = collect_all('openpyxl')` y sumar `openpyxl_hidden` a `hiddenimports`, `openpyxl_datas` a `datas` y `openpyxl_binaries` a `binaries`, siguiendo EXACTAMENTE el patrón de `pandas` en cada fichero (en el linux, el de `pyexiv2`).

- [ ] **Step 4: Verificar suite completa**

Run: `.venv/bin/python -m pytest -q tests/ -x -p no:cacheprovider 2>&1 | tail -15`
Expected: PASS. Comparar contra `git stash`-free baseline: si falla algo, correr ese test en `HEAD` limpio (`git worktree add /tmp/oo-base HEAD` y pytest allí) para distinguir fallo previo de regresión; reportar ambos.

- [ ] **Step 5: Reportar.** Sin commit.

---

### Task 6: Validación real en el portátil (hilo base, no subagente)

**Files:** ninguno (medición).

- [ ] **Step 1:** Sincronizar `~/atom-organizer-work` (sin `.venv`, `tmpwork`, `bench`) a `~/atom-organizer` del portátil (`rsync` por `ssh -p 2222 saez@localhost`). Mirar `pgrep rustc` y `uptime` antes de medir.
- [ ] **Step 2:** Organizar el vuelo `DJI_202609101001_001` de MELINESTI a destino NUEVO en `$M` con `export PATH=/usr/bin/vendor_perl:$PATH`, config igual al bench CPU. Anotar tiempo de Cierre del `[done]`.
- [ ] **Step 3:** Comparar `CSVs/*meta.csv` y `*location.csv` del vuelo contra los de `BENCH_LAP_MELINESTI_0910` (`cmp`). Esperado: idénticos. Si difieren, `diff` de 5 líneas y parar.
- [ ] **Step 3b:** Equipo: el estadillo MELINESTI dice `DJI M300` y el EXIF es `M4T` → esperado 1 `AVISO: PBTS09 vuelo 1: ...` en el log y `EquipoCoincide=No`, `Make=DJI`, `Model=M4T` en el Excel.
- [ ] **Step 4:** Relanzar sobre el MISMO destino con un segundo vuelo como origen + el primero repetido. Esperado: aviso "ya estaban organizadas", Excel con filas de ambos, sin abortar por destino no vacío.
- [ ] **Step 5:** Reportar a Cas: cierre antes/después, CSV idénticos sí/no, acumulación OK. Commit por tarea tras su OK.
