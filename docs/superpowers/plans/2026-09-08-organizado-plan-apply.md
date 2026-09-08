# Motor de organizado plan→apply — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sustituir el motor de organizado de 7 fases con ficheros intermedios por un motor índice → manifiesto SQLite → apply de un solo decode por imagen → cierre, con idéntica salida y paralelismo que se auto-ajusta al disco.

**Architecture:** El **índice** hace UNA pasada de metadatos (EXIF/XMP + estadillo) sobre el origen y decide en memoria todo lo que hoy se decide a trozos repartido entre fases: vuelo destino, nombre nuevo, ángulo de giro, % de recorte, si comprime y las rutas finales. Eso se persiste en un **manifiesto SQLite en modo WAL**, una fila por imagen. El **apply** recorre el manifiesto con pools cuyo tamaño se reajusta en caliente y escribe cada salida directa a su carpeta final, abriendo cada imagen una sola vez. El **cierre** emite los CSV y verifica manifiesto contra disco. El origen se conserva intacto.

**Tech Stack:** Python 3 (`.venv/bin/python`), `sqlite3` de la stdlib (WAL), Pillow, piexif, `psutil` (ya en uso en `atom_core/medicion_recursos.py`), `concurrent.futures`, `exiftool -stay_open` y `dji_irp` como procesos externos. Tests con `pytest` (config en `tests/conftest.py`, sin `pytest.ini`).

**Spec:** `docs/superpowers/specs/2026-09-08-organizado-plan-apply-design.md` — léela entera antes de empezar. Lleva el inventario de las 7 fases, las 12 dependencias globales, el esquema del manifiesto, el paralelismo adaptativo y el plan de validación.

## Global Constraints

Estas reglas aplican a TODAS las tareas. No se repiten en cada una.

- **Es PROD.** Lo usan Daniel y el equipo. No se rompe lo que ya funciona. **NO deploy a `main` ni release sin OK expreso de Rodrigo.**
- **La corrección manda sobre la velocidad.** Prioridad expresa de Rodrigo. Ante la duda entre "más rápido" y "seguro que sale igual que antes", gana lo segundo.
- **Python**: usar SIEMPRE `.venv/bin/python`. En el shell base `python` no existe (hay `python3`).
- **Tests Python**: `.venv/bin/python -m pytest`. **Tests webui**: `cd webui && npx vitest run`.
- **Estilo de tests de la casa**: dobles falsos escritos a mano (`_ObjetoQueApunta`, `_HostDePrueba`) y `monkeypatch`. **NADA de `unittest.mock`.** Docstrings largos que explican el incidente real que el test previene. Calca `tests/test_etapas_pipeline.py`.
- **Sin imágenes reales versionadas**: todo material de test es sintético, generado en `tmp_path`. Fixtures existentes reutilizables: `make_dji_jpeg` (JPEG 64x48 con EXIF GPS + DateTimeOriginal + bloque XMP DJI) y `tmp_inspection` (estructura `PB1_V01/RGB` + `PB1_V01/TERMICA`).
- **Idioma**: todo texto visible, comentario y docstring en **español con tildes y ñ**. Los identificadores de código, en español también (el repo ya lo hace: `medicion_recursos`, `almacen`, `publicar_en`).
- **Nunca `px` en CSS/Tailwind** si alguna tarea toca `webui/` — `rem`/`vh`/`vw`.
- **Git**: comandos SUELTOS, nunca compuestos con `&&` (el clasificador los bloquea). Los `-c` van ENTRE `git` y `commit`. **SIN `Co-Authored-By`.** Autor:
  `git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "..."`
- **Docker**: SOLO `dev-fast`, `dev-build` y `docker exec`. Prohibido `docker build`, `docker compose build`, `docker run`, `docker prune`.
- **No romper las métricas del modal de progreso** (MB/s y CPU por fase, v3.4.80): `MedidorRecursos` en `atom_core/medicion_recursos.py:81`, consumidas en `webui/src/ProgressModal.jsx:48-49,67-70` y `webui/src/App.jsx:373,389`. El canal de eventos es `emit(nombre, payload)` (`EmitFn` en `atom_core/organize.py:70`); las fases NO llaman a `emit`, reciben tres `_Signal` (`pcb` log, `pbar` progreso int, `psum` resumen) construidos en `atom_core/organize.py:734-736`. `MedidorRecursos.abrir_fase()` se dispara al ver el prefijo `---> SUBPROCESO:` en el canal `psum`: **cualquier fase nueva debe emitir ese prefijo o desaparece de las métricas.**
- **Sin constantes mágicas de hardware.** Prohibido asumir SSD, número de núcleos o tamaño de imagen. Todo lo que dependa del disco se mide en caliente.
- **Cero configuración nueva para el usuario.** El paralelismo se auto-ajusta; Rodrigo no toca nada.

---

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `atom_core/manifiesto.py` *(nuevo)* | El manifiesto SQLite WAL: esquema, alta de filas, transiciones de estado, consultas de pendientes y resumen. No sabe nada de imágenes. |
| `atom_core/paralelismo.py` *(nuevo)* | `ControladorAdaptativo`: mide throughput/latencia/RAM y decide cuántos trabajadores debe haber en cada momento. Función pura de decisión + envoltorio que la aplica a un pool. No sabe nada de imágenes. |
| `atom_core/indice.py` *(nuevo)* | Construye el manifiesto: una pasada de metadatos sobre el origen, cruce con el estadillo, y resolución de vuelo, nombre, ángulo, % de recorte y rutas de salida. **Decide, no escribe imágenes.** |
| `atom_core/apply.py` *(nuevo)* | Recorre el manifiesto y escribe: RGB con un único decode (compresión + recorte + giro), térmicas con `dji_irp` + `exiftool`. Escribe a temporal y renombra. |
| `atom_core/cierre.py` *(nuevo)* | Emite `meta`, `location` y `_Videofiles` desde el manifiesto y corre las verificaciones manifiesto-contra-disco. |
| `atom_core/phases.py` *(modificar)* | Nuevo método `organizar_plan_apply` que sustituye a `split_images` y a las fases posteriores, con la misma firma `(self, cfg, progress_callback, progress_bar, progress_summarize)`. |
| `atom_core/organize.py` *(modificar)* | Lista de fases del modal (`_SPLIT_PHASES`, `_active_split_phases`, ~275-300) y orden de llamada (~291-299, 762). |

Los módulos nuevos van en `atom_core/` y no en la raíz: `pipeline.py` ya tiene 5703 líneas y la parte nueva no debe engordarla. Se reutiliza lo que ya existe en `pipeline.py`/`exif.py` en vez de reimplementarlo — cada tarea dice exactamente qué.

## Correcciones a la spec descubiertas al leer el código

Se aplican en la Tarea 0 y mandan sobre lo que dice la spec:

1. **El `New Name` NO es una numeración secuencial por vuelo.** La spec y el ledger lo decían mal. El nombre final de salida lo decide `Pipeline.nombre_destino` (`pipeline.py:2680`) y es `AAAAMMDD_HHMMSS_<nombre_original>` a partir del `DateTimeOriginal` más el desfase; devuelve `""` (no renombrar) si `rename=False` o si no hay timestamp EXIF. La numeración `<PBx_Vy>_0001.JPG` (`pipeline.py:2078-2189`) es **solo la clave de fila del CSV de criterio de giro**, no un nombre de fichero entregado.
2. **El % de recorte no está hardcodeado por modelo**: sale de `Config.ini`, sección `[percentage_by_models]`, cargado en `external_tools.py:240` y consultado por `Pipeline.get_percentage_by_model` (`pipeline.py:4206`) normalizando el modelo a mayúsculas. Solo aplica si `percentage_cropping_auto=True`; si no, manda `percentage_cropping_manual`.
3. **Ya existe un decode único parcial**: `_procesar_y_guardar_imagen` (`pipeline.py:234`) aplica recorte y giro sobre el mismo objeto abierto y guarda una sola vez, con `ImageProcessConfig` (`pipeline.py:189-197`). El apply nuevo **reutiliza esa función**, no escribe otra.
4. **Calidad al girar**: hoy una RGB girada se re-encodea con `_ROTATION_JPEG_QUALITY = 40` hardcodeado (`pipeline.py:280-283`), pisando la calidad de la interfaz. **Decisión de Rodrigo (2026-09-08): replicar el criterio del motor viejo, no mejorarlo por sorpresa.** El apply usa calidad 40 cuando la fila lleva giro y `cfg.compress_level` cuando no. Consecuencia para la validación: el motor viejo llega a ese 40 tras un encode previo a calidad de compresión (doble pérdida) y el nuevo lo hace de una sola vez, así que los píxeles de las RGB **giradas** diferirán algo más que los de las no giradas. La Tarea 8 calibra la tolerancia con datos reales y documenta el resultado.
5. **Ángulos de giro posibles: 0, 90 y 270** (`read_auto_rotate_degree`, `pipeline.py:3308`). Nunca 180. Si falta el CSV de criterio, devuelve 0 sin reventar.
6. **Inconsistencia de casing detectada**: `RGB_Extra` (`pipeline.py:2829`) vs `RGB_extra` (`pipeline.py:1377`). En NTFS no se nota; en el bucket sí. El índice fija UN solo nombre y la Tarea 3 documenta cuál.

---

### Tarea 0: Corregir la spec y el ledger

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-organizado-plan-apply-design.md`
- Modify: `.workflow/LEDGER-organizado-plan-apply.md`

**Interfaces:**
- Consumes: nada.
- Produces: una spec que no miente sobre `New Name`. Todas las tareas siguientes la leen.

- [ ] **Paso 1: Corregir la spec**

En la sección "Índice (plan)", sustituir `nombre nuevo (`New Name` secuencial por vuelo)` por:

```
nombre nuevo (`AAAAMMDD_HHMMSS_<original>` vía `Pipeline.nombre_destino`, `pipeline.py:2680`; cadena vacía si no se renombra o falta el timestamp EXIF)
```

En la tabla del manifiesto, cambiar la fila `nombre_nuevo` de `` `New Name` secuencial por vuelo `` a `nombre de salida ya resuelto (`AAAAMMDD_HHMMSS_<original>`), vacío si no se renombra`.

Añadir al final de la sección "Arquitectura" el bloque completo de las 6 correcciones de arriba, bajo el título `## Correcciones tras leer el código (2026-09-08)`.

- [ ] **Paso 2: Corregir el ledger**

En `.workflow/LEDGER-organizado-plan-apply.md`, sustituir la línea

```
- [ ] Numeración `New Name` secuencial dentro del vuelo, idéntica al motor viejo.
```

por

```
- [ ] Nombre de salida idéntico al motor viejo: `AAAAMMDD_HHMMSS_<original>` vía `Pipeline.nombre_destino`. (Corregido 2026-09-08: NO es numeración secuencial; eso es solo la clave del CSV de criterio de giro.)
```

Y añadir a `## Clarified`:

```
- **Calidad al girar una RGB (2026-09-08)**: se replica el criterio del motor viejo (`_ROTATION_JPEG_QUALITY = 40`), NO se mejora. Decisión expresa de Rodrigo.
```

- [ ] **Paso 3: Marcar hecho lo ya entregado en el ledger**

Marcar `- [x]` la línea de la spec commiteada y la del comparador (`tools/comparar_organizados.py`, commit `7ccc6de`), añadiendo esta línea nueva en `### Validación`:

```
- [x] `tools/comparar_organizados.py` + tests (commit `7ccc6de`): árbol, TIFF byte a byte, JPG por píxeles con tolerancia de recompresión + EXIF normalizado, CSV fila a fila.
```

- [ ] **Paso 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-08-organizado-plan-apply-design.md .workflow/LEDGER-organizado-plan-apply.md
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "docs: corregir la spec del motor plan-apply tras leer el codigo"
```

---

### Tarea 1: Manifiesto SQLite

**Files:**
- Create: `atom_core/manifiesto.py`
- Test: `tests/test_manifiesto.py`

**Interfaces:**
- Consumes: nada. Es la base de todo el resto.
- Produces:

```python
ESTADOS = ("pendiente", "en_curso", "hecho", "fallido")

@dataclass(frozen=True)
class FilaManifiesto:
    ruta_origen: str
    tipo: str                    # "RGB" | "TERMICA" | "RGB_EXTRA"
    timestamp_exif: str | None   # ISO 8601, None si la imagen no lo trae
    modelo: str | None
    pb: str | None               # None si la imagen es unassigned
    vuelo: str | None            # None si la imagen es unassigned
    nombre_nuevo: str            # "" si no se renombra
    angulo_giro: int             # 0, 90 o 270
    pct_recorte: float | None    # None si no se recorta
    comprime: bool
    ruta_salida_original: str
    ruta_salida_crop: str | None
    ruta_salida_tiff: str | None
    unassigned: bool

class Manifiesto:
    def __init__(self, ruta_db: "str | Path") -> None: ...
    def crear_esquema(self) -> None: ...
    def insertar_muchas(self, filas: "Iterable[FilaManifiesto]") -> int: ...
    def pendientes(self, limite: int | None = None) -> "list[sqlite3.Row]": ...
    def marcar_en_curso(self, id_fila: int) -> None: ...
    def marcar_hecha(self, id_fila: int, verificacion: str) -> None: ...
    def marcar_fallida(self, id_fila: int, motivo: str) -> None: ...
    def reabrir_huerfanas(self) -> int: ...
    def resumen(self) -> "dict[str, int]": ...
    def filas_por_vuelo(self, pb: str, vuelo: str) -> "list[sqlite3.Row]": ...
    def todas(self) -> "list[sqlite3.Row]": ...
    def cerrar(self) -> None: ...
```

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `tests/test_manifiesto.py`. Cada test lleva docstring explicando qué incidente previene. Nada de `unittest.mock`.

```python
"""Tests del manifiesto SQLite del organizado.

El manifiesto es la memoria del run: si pierde filas, se corrompe al
escribirlo desde varios procesos a la vez, o no sabe distinguir lo hecho de
lo pendiente, el organizado deja imágenes sin procesar SIN QUE NADIE SE
ENTERE. Por eso se prueba la concurrencia y la reanudación, no solo el CRUD.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from atom_core.manifiesto import FilaManifiesto, Manifiesto


def _fila(ruta_origen="/origen/DJI_0001.JPG", **cambios):
    """Construye una fila válida; los tests solo sobrescriben lo que les importa."""
    base = dict(
        ruta_origen=ruta_origen,
        tipo="RGB",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb="PB1",
        vuelo="V01",
        nombre_nuevo="20260501_100000_DJI_0001.JPG",
        angulo_giro=90,
        pct_recorte=0.8,
        comprime=True,
        ruta_salida_original="/destino/PB1_V01/RGB/20260501_100000_DJI_0001.JPG",
        ruta_salida_crop="/destino/PB1_V01/RGB/20260501_100000_DJI_0001_CROP.JPG",
        ruta_salida_tiff=None,
        unassigned=False,
    )
    base.update(cambios)
    return FilaManifiesto(**base)


def test_esquema_en_modo_wal(tmp_path):
    """El manifiesto se escribe desde varios workers a la vez. Sin WAL, SQLite
    serializa con bloqueo de fichero entero y el apply se convierte en un
    cuello de botella (o revienta con 'database is locked')."""
    manifiesto = Manifiesto(tmp_path / "manifiesto.db")
    manifiesto.crear_esquema()
    with sqlite3.connect(tmp_path / "manifiesto.db") as conexion:
        modo = conexion.execute("PRAGMA journal_mode").fetchone()[0]
    assert modo.lower() == "wal"
    manifiesto.cerrar()


def test_insertar_y_contar_pendientes(tmp_path):
    """Toda fila recién insertada nace 'pendiente': el apply no puede saltarse
    ninguna imagen por un default mal puesto."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    insertadas = manifiesto.insertar_muchas(
        [_fila("/origen/A.JPG"), _fila("/origen/B.JPG")]
    )
    assert insertadas == 2
    assert len(manifiesto.pendientes()) == 2
    assert manifiesto.resumen() == {
        "pendiente": 2, "en_curso": 0, "hecho": 0, "fallido": 0
    }
    manifiesto.cerrar()


def test_ruta_origen_es_unica(tmp_path):
    """Insertar dos veces la misma imagen la duplicaría en el destino. Un
    re-run del índice sobre el mismo manifiesto no debe multiplicar filas."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG")])
    manifiesto.insertar_muchas([_fila("/origen/A.JPG")])
    assert len(manifiesto.todas()) == 1
    manifiesto.cerrar()


def test_transiciones_de_estado(tmp_path):
    """Una fila hecha guarda su verificación; una fallida guarda el motivo.
    Sin motivo no se puede reintentar con criterio ni explicar el fallo."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG"), _fila("/origen/B.JPG")])
    primera, segunda = manifiesto.todas()

    manifiesto.marcar_en_curso(primera["id"])
    manifiesto.marcar_hecha(primera["id"], verificacion="1234")
    manifiesto.marcar_fallida(segunda["id"], motivo="EXIF ilegible")

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas["/origen/A.JPG"]["estado"] == "hecho"
    assert filas["/origen/A.JPG"]["verificacion"] == "1234"
    assert filas["/origen/B.JPG"]["estado"] == "fallido"
    assert filas["/origen/B.JPG"]["motivo_fallo"] == "EXIF ilegible"
    manifiesto.cerrar()


def test_reabrir_huerfanas(tmp_path):
    """Si el proceso muere a mitad, quedan filas 'en_curso' que nadie está
    procesando. Al reanudar hay que devolverlas a 'pendiente' o esas imágenes
    no se escriben nunca."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG")])
    id_fila = manifiesto.todas()[0]["id"]
    manifiesto.marcar_en_curso(id_fila)

    assert manifiesto.reabrir_huerfanas() == 1
    assert manifiesto.todas()[0]["estado"] == "pendiente"
    manifiesto.cerrar()


def test_escrituras_concurrentes_no_pierden_filas(tmp_path):
    """El apply marca filas desde muchos workers a la vez. Este test reproduce
    esa concurrencia: si el manifiesto perdiera actualizaciones, el resumen
    final diría que quedan pendientes imágenes que sí se escribieron (o al
    revés, que está todo hecho cuando no lo está)."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila(f"/origen/{indice:03d}.JPG") for indice in range(60)])
    ids = [fila["id"] for fila in manifiesto.todas()]

    def marcar(id_fila):
        manifiesto.marcar_en_curso(id_fila)
        manifiesto.marcar_hecha(id_fila, verificacion=str(id_fila))

    with ThreadPoolExecutor(max_workers=8) as ejecutor:
        list(ejecutor.map(marcar, ids))

    assert manifiesto.resumen()["hecho"] == 60
    assert manifiesto.pendientes() == []
    manifiesto.cerrar()


def test_filas_por_vuelo(tmp_path):
    """El cierre emite un CSV de criterio por vuelo. Necesita pedir las filas
    de un (PB, vuelo) concreto sin releer el manifiesto entero."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", pb="PB1", vuelo="V01"),
        _fila("/origen/B.JPG", pb="PB1", vuelo="V02"),
        _fila("/origen/C.JPG", pb="PB1", vuelo="V01"),
    ])
    rutas = sorted(fila["ruta_origen"] for fila in manifiesto.filas_por_vuelo("PB1", "V01"))
    assert rutas == ["/origen/A.JPG", "/origen/C.JPG"]
    manifiesto.cerrar()


def test_fila_unassigned_admite_pb_y_vuelo_vacios(tmp_path):
    """Las imágenes que no casan con ninguna ventana horaria van a
    SIN_ORDENAR: no tienen PB ni vuelo, y el esquema tiene que aceptarlo en
    vez de reventar con NOT NULL."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/X.JPG", pb=None, vuelo=None, unassigned=True,
              ruta_salida_original="/destino/SIN_ORDENAR/RGB/X.JPG",
              ruta_salida_crop=None)
    ])
    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 1
    assert fila["pb"] is None
    manifiesto.cerrar()
```

- [ ] **Paso 2: Ejecutar los tests y verificar que fallan**

Run: `.venv/bin/python -m pytest tests/test_manifiesto.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'atom_core.manifiesto'`

- [ ] **Paso 3: Implementar `atom_core/manifiesto.py`**

```python
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
    estado TEXT NOT NULL DEFAULT 'pendiente',
    motivo_fallo TEXT,
    verificacion TEXT
);
CREATE INDEX IF NOT EXISTS idx_estado ON imagenes(estado);
CREATE INDEX IF NOT EXISTS idx_vuelo ON imagenes(pb, vuelo);
"""


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
        conexion.commit()

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

    def cerrar(self) -> None:
        conexion = getattr(self._local, "conexion", None)
        if conexion is not None:
            conexion.close()
            self._local.conexion = None
```

- [ ] **Paso 4: Ejecutar los tests y verificar que pasan**

Run: `.venv/bin/python -m pytest tests/test_manifiesto.py -v`
Expected: 8 passed.

- [ ] **Paso 5: Comprobar que no se rompió nada**

Run: `.venv/bin/python -m pytest -q`
Expected: los 1295 tests previos siguen pasando, más los 8 nuevos.

- [ ] **Paso 6: Commit**

```bash
git add atom_core/manifiesto.py tests/test_manifiesto.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): manifiesto SQLite WAL con estado por imagen"
```

---

### Tarea 2: Controlador adaptativo de paralelismo

**Files:**
- Create: `atom_core/paralelismo.py`
- Test: `tests/test_paralelismo_adaptativo.py`

**Contexto (por qué existe esta tarea):** hoy el número de trabajadores lo fija `workers_para_lote` (`utils.py:1305-1333`) UNA vez al arrancar, a partir de núcleos y RAM. Eso ignora dos cosas: el tipo de disco (en HDD, más hilos = thrashing) y la carga del resto de la máquina. Rodrigo reportó el 2026-09-08 que un mismo organizado tardó **10 minutos más** con el PC ocupado por otros procesos: el dimensionado estático pidió los mismos trabajadores y se peleó con ellos. El controlador mide y se ajusta en caliente. `workers_para_lote` NO se borra: sigue dando el punto de partida y el techo por RAM.

**Interfaces:**
- Consumes: `utils.workers_para_lote(mb_por_worker)` (`utils.py:1305`) para el arranque y el techo por RAM.
- Produces:

```python
@dataclass(frozen=True)
class Medicion:
    trabajadores: int
    completados: int        # items terminados en la ventana
    segundos: float         # duración de la ventana
    mb_procesados: float
    cpu_ociosa_pct: float   # 0-100, del sistema entero
    ram_libre_mb: float

def decidir_trabajadores(historial: "list[Medicion]", minimo: int, maximo: int) -> int: ...

class ControladorAdaptativo:
    def __init__(self, minimo: int = 1, maximo: int | None = None,
                 ventana_segundos: float = 5.0, mb_por_worker: float = ...,
                 reloj=time.monotonic, lector_recursos=None) -> None: ...
    @property
    def trabajadores(self) -> int: ...
    def registrar(self, mb: float) -> None: ...   # un item terminado
    def revisar(self) -> int: ...                 # cierra ventana si toca y devuelve trabajadores
```

`lector_recursos` es un callable sin argumentos que devuelve `(cpu_ociosa_pct, ram_libre_mb)`. En producción lo aporta `psutil` (ya se usa en `atom_core/medicion_recursos.py`); en los tests se inyecta a mano. `reloj` se inyecta igual para no dormir en los tests.

**Reglas de decisión (implementar literalmente, en este orden):**
1. Si `ram_libre_mb` de la última medición es menor que `2 * mb_por_worker` → bajar a `max(minimo, trabajadores - 1)`. La RAM es un límite duro, no una preferencia.
2. Con menos de 2 mediciones → mantener.
3. Calcular `rendimiento = completados / segundos` de las dos últimas ventanas. Si la última mejora la anterior en más del 5% → subir 1 (hasta `maximo`). Ese 5% es la zona muerta que evita oscilar por ruido de medida.
4. Si empeora más del 5% → bajar 1 (hasta `minimo`). Esto es lo que detecta el thrashing en HDD: más hilos, menos throughput.
5. Si queda dentro del ±5% y `cpu_ociosa_pct > 40` → subir 1 (hay margen de máquina sin usar).
6. En cualquier otro caso → mantener.

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `tests/test_paralelismo_adaptativo.py`:

```python
"""Tests del controlador adaptativo de paralelismo.

El organizado corre en portátiles distintos, con discos distintos (SSD o
HDD, mismo disco o dos) y con el usuario haciendo otras cosas a la vez.
Rodrigo reportó un run 10 minutos más lento solo porque el PC estaba
ocupado. Un número de trabajadores fijado al arrancar no puede acertar en
todos esos casos: este controlador se corrige durante el run.

Todo se prueba con reloj y lector de recursos inyectados: un test que
dependiera del hardware real o de dormir sería lento y no determinista.
"""
import pytest

from atom_core.paralelismo import ControladorAdaptativo, Medicion, decidir_trabajadores


def _medicion(trabajadores=4, completados=100, segundos=10.0, mb_procesados=500.0,
              cpu_ociosa_pct=10.0, ram_libre_mb=8000.0):
    return Medicion(trabajadores, completados, segundos, mb_procesados,
                    cpu_ociosa_pct, ram_libre_mb)


def test_sube_si_el_rendimiento_mejora():
    """Mientras añadir trabajadores dé más imágenes por segundo, hay que seguir
    subiendo: es el caso de un SSD infrautilizado."""
    historial = [_medicion(trabajadores=4, completados=100),
                 _medicion(trabajadores=5, completados=130)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_baja_si_el_rendimiento_empeora():
    """Thrashing de HDD: al subir trabajadores el throughput CAE. Si el
    controlador no lo detecta y baja, el run se hace más lento cuanto más
    paralelismo le echamos, que es justo lo contrario de lo que se busca."""
    historial = [_medicion(trabajadores=6, completados=130),
                 _medicion(trabajadores=7, completados=90)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_la_ram_manda_sobre_el_rendimiento():
    """Aunque el rendimiento esté mejorando, si queda poca RAM hay que bajar.
    Un pico de RAM con muchos decodes simultáneos tumba el proceso entero y
    se pierde el run: es un límite duro, no una preferencia."""
    historial = [_medicion(trabajadores=6, completados=100, ram_libre_mb=9000.0),
                 _medicion(trabajadores=7, completados=200, ram_libre_mb=200.0)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_mantiene_si_esta_en_la_zona_muerta_y_no_hay_cpu_libre():
    """Sin zona muerta, el ruido de medida haría oscilar el número de
    trabajadores arriba y abajo en cada ventana, y cada cambio de tamaño de
    pool cuesta."""
    historial = [_medicion(trabajadores=5, completados=100, cpu_ociosa_pct=5.0),
                 _medicion(trabajadores=5, completados=102, cpu_ociosa_pct=5.0)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 5


def test_sube_en_zona_muerta_si_sobra_cpu():
    """Rendimiento plano pero máquina medio ociosa: el cuello no es la CPU,
    merece la pena probar un trabajador más."""
    historial = [_medicion(trabajadores=5, completados=100, cpu_ociosa_pct=60.0),
                 _medicion(trabajadores=5, completados=101, cpu_ociosa_pct=60.0)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_respeta_minimo_y_maximo():
    """Nunca cero trabajadores (el run se pararía) ni por encima del techo de
    RAM calculado al arrancar."""
    empeora = [_medicion(trabajadores=1, completados=100),
               _medicion(trabajadores=1, completados=10)]
    assert decidir_trabajadores(empeora, minimo=1, maximo=16) == 1

    mejora = [_medicion(trabajadores=16, completados=100),
              _medicion(trabajadores=16, completados=300)]
    assert decidir_trabajadores(mejora, minimo=1, maximo=16) == 16


def test_una_sola_medicion_mantiene():
    """Sin dos ventanas no hay comparación posible: inventarse una tendencia
    con un solo dato es peor que esperar."""
    assert decidir_trabajadores([_medicion()], minimo=1, maximo=16) == 4


class _RelojFalso:
    """Reloj monótono controlado por el test. Evita dormir de verdad."""

    def __init__(self):
        self.ahora = 0.0

    def __call__(self):
        return self.ahora

    def avanzar(self, segundos):
        self.ahora += segundos


def test_el_controlador_cierra_ventanas_por_tiempo():
    """La ventana se cierra por tiempo, no por número de items: si no, un lote
    de imágenes enormes no cerraría ninguna ventana y el controlador nunca
    reaccionaría."""
    reloj = _RelojFalso()
    recursos = {"cpu": 60.0, "ram": 9000.0}
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (recursos["cpu"], recursos["ram"]),
    )
    inicial = controlador.trabajadores

    for _ in range(10):
        controlador.registrar(mb=5.0)
    assert controlador.revisar() == inicial  # aún no ha pasado la ventana

    reloj.avanzar(6.0)
    for _ in range(10):
        controlador.registrar(mb=5.0)
    controlador.revisar()
    reloj.avanzar(6.0)
    for _ in range(30):
        controlador.registrar(mb=5.0)

    assert controlador.revisar() > inicial


def test_el_controlador_baja_cuando_se_acaba_la_ram():
    """Comprobación de extremo a extremo de la regla dura de RAM a través del
    controlador, no solo de la función de decisión."""
    reloj = _RelojFalso()
    recursos = {"cpu": 60.0, "ram": 9000.0}
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (recursos["cpu"], recursos["ram"]),
    )
    reloj.avanzar(6.0)
    controlador.registrar(mb=5.0)
    controlador.revisar()

    recursos["ram"] = 100.0
    reloj.avanzar(6.0)
    controlador.registrar(mb=5.0)
    antes = controlador.trabajadores
    assert controlador.revisar() < antes
```

- [ ] **Paso 2: Ejecutar los tests y verificar que fallan**

Run: `.venv/bin/python -m pytest tests/test_paralelismo_adaptativo.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'atom_core.paralelismo'`

- [ ] **Paso 3: Implementar `atom_core/paralelismo.py`**

Implementar `Medicion`, `decidir_trabajadores` (exactamente las 6 reglas de arriba, en ese orden) y `ControladorAdaptativo`. Notas de implementación obligatorias:

- El arranque sale de `utils.workers_para_lote(mb_por_worker)`; el `maximo` por defecto es ese mismo valor multiplicado por 2, nunca un número inventado.
- `registrar` es llamado desde varios hilos: proteger los contadores con un `threading.Lock`.
- `revisar` solo cierra ventana si `reloj() - inicio_ventana >= ventana_segundos`; si no, devuelve el valor actual sin tocar nada.
- El `lector_recursos` por defecto usa `psutil`: `100.0 - psutil.cpu_percent(interval=None)` y `psutil.virtual_memory().available / (1024 * 1024)`. Import perezoso dentro de la función, para no cargar psutil si el llamante inyecta el suyo.
- El controlador **no crea ni redimensiona pools**: solo dice el número. Quien lo consume (Tarea 4) decide cuántas tareas mantiene en vuelo. Redimensionar un `ProcessPoolExecutor` no es posible, así que el apply limita el trabajo en vuelo con un semáforo cuyo aforo sigue a `controlador.trabajadores`.

- [ ] **Paso 4: Ejecutar los tests y verificar que pasan**

Run: `.venv/bin/python -m pytest tests/test_paralelismo_adaptativo.py tests/test_workers_para_lote.py -v`
Expected: los 9 nuevos pasan y los de `workers_para_lote` siguen verdes (no se ha tocado esa función).

- [ ] **Paso 5: Commit**

```bash
git add atom_core/paralelismo.py tests/test_paralelismo_adaptativo.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): controlador adaptativo de trabajadores en caliente"
```

---

### Tarea 3: Índice — decidir sin tocar píxeles

**Files:**
- Create: `atom_core/indice.py`
- Test: `tests/test_indice_organizado.py`

**Contexto:** esta es la tarea que concentra las 12 dependencias globales. NO reimplementa ningún criterio: llama a las funciones que ya existen y que el motor viejo usa, para que la decisión salga idéntica.

| Qué decide | Con qué función existente |
|---|---|
| Estadillo fusionado | `atom_core.estadillo.combinar_estadillos(rutas)` (`estadillo.py:189`) |
| Colisiones PB+vuelo | `atom_core.estadillo.detectar_colisiones_pb_vuelo(df, nombres_columnas)` (`estadillo.py:228`) |
| Ventana horaria de un vuelo | `Pipeline.ventana_horaria_vuelo(fecha, horaInicio, horaFinal, margen_segundos, desfase_horas, desfase_minutos)` (`pipeline.py:1541`) → `(inicio, fin)` |
| Timestamp de la imagen | `Exif.get_timestamp_from_image(pathImagen)` (`exif.py:224`) → `datetime` o `None` |
| Modelo de cámara | `Exif.get_model(filename, progress_callback)` (`exif.py:110`) |
| Yaw/pitch gimbal | `Exif.get_gimbal_yaw_pitch(filename, bloque_xmp=None)` (`exif.py:277`) → `[yaw, pitch]` |
| GPS | `Exif.leerLatitudLongitudAltitud_exif_DJI(pathImagen, progress_callback)` (`exif.py:1136`) |
| Nombre de salida | `Pipeline.nombre_destino(image, input_folder, rename, mismatch_hours, mismatch_minutes, ruta_local=None)` (`pipeline.py:2680`) → `AAAAMMDD_HHMMSS_<original>` o `""` |
| % de recorte | `Pipeline.get_percentage_by_model(model, percentage_cropping_dict)` (`pipeline.py:4206`); si `percentage_cropping_auto=False`, manda `percentage_cropping_manual` |

**Criterio de asignación a vuelo** (idéntico a `obtenerListaImagenesVuelo`, `pipeline.py:1498`): la comparación es **estricta**, `inicio < timestamp < fin`. Una imagen que no cae en ninguna ventana es `unassigned` y su salida va a `SIN_ORDENAR/<RGB|TERMICA>/`.

**Ángulo de giro:** el índice calcula el consenso por carpeta `PBx_Vy` UNA vez y lo escribe en todas las filas de ese vuelo (RGB y térmica), de modo que el TIFF y su JPG comparten ángulo por construcción. Valores posibles: **0, 90, 270**. Si no se puede determinar, 0.

**Casing:** el índice usa **`RGB_Extra`** (con E mayúscula, el de `pipeline.py:2829`) como único nombre de la carpeta extra, y así se elimina la inconsistencia con `RGB_extra` de `pipeline.py:1377`.

**Interfaces:**
- Consumes: `atom_core.manifiesto.{Manifiesto, FilaManifiesto}` (Tarea 1).
- Produces:

```python
class ErrorColisionEstadillo(Exception):
    """Se han fusionado estadillos con el mismo (PB, vuelo) y fechas distintas.
    Aborta ANTES de escribir nada, no a mitad como hacía el motor viejo."""

def construir_indice(
    cfg,                       # SplitImagesConfig (utils.py:1037)
    pipeline,                  # instancia de Pipeline ya construida
    exif,                      # instancia de Exif ya construida
    manifiesto: "Manifiesto",
    progress_callback,
    progress_bar,
    progress_summarize,
    max_hilos: int | None = None,
) -> dict: ...
```

Devuelve `{"total": int, "unassigned": int, "sin_timestamp": int, "vuelos": int}`.

- [ ] **Paso 1: Escribir los tests que fallan**

Crear `tests/test_indice_organizado.py`. Usa las fixtures existentes `make_dji_jpeg` y `tmp_inspection` de `tests/conftest.py`. Dobles a mano (`_PipelineDePrueba`, `_ExifDePrueba`), nunca `unittest.mock`. Tests obligatorios, cada uno con su docstring explicando el incidente que previene:

1. `test_asigna_cada_imagen_a_su_vuelo_por_ventana_horaria` — dos imágenes con timestamps dentro de ventanas distintas acaban con `vuelo` distinto en el manifiesto.
2. `test_imagen_fuera_de_toda_ventana_queda_unassigned` — su `ruta_salida_original` contiene `SIN_ORDENAR` y `unassigned == 1`. Previene que se pierdan imágenes que hoy rescata un barrido posterior.
3. `test_la_comparacion_de_ventana_es_estricta` — una imagen con timestamp EXACTAMENTE igual al inicio de la ventana queda fuera, como en `pipeline.py:1498`. Previene una diferencia silenciosa de una imagen contra el motor viejo.
4. `test_todas_las_filas_de_un_vuelo_comparten_angulo` — RGB y térmica del mismo `PBx_Vy` tienen el mismo `angulo_giro`. **Este es el test del bug que motiva el proyecto entero** (hoy el TIFF y su JPG pueden acabar con criterios distintos).
5. `test_colision_pb_vuelo_aborta_sin_escribir_nada` — con colisión, `construir_indice` lanza `ErrorColisionEstadillo` y el manifiesto queda vacío.
6. `test_imagen_sin_timestamp_no_revienta_el_indice` — se contabiliza en `sin_timestamp`, queda `unassigned`, y el resto del índice se completa.
7. `test_pct_recorte_sale_del_modelo` — con `percentage_cropping_auto=True`, la fila lleva el porcentaje del diccionario del modelo; con `False`, el manual.
8. `test_el_indice_no_escribe_ninguna_imagen` — tras `construir_indice`, la carpeta de destino no contiene ningún fichero de imagen. El índice DECIDE, no escribe: si esto se rompe, se pierde la propiedad que hace seguro inspeccionar el plan antes de tocar nada.

- [ ] **Paso 2: Ejecutar los tests y verificar que fallan**

Run: `.venv/bin/python -m pytest tests/test_indice_organizado.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'atom_core.indice'`

- [ ] **Paso 3: Implementar `atom_core/indice.py`**

Estructura obligatoria:

```python
def construir_indice(cfg, pipeline, exif, manifiesto, progress_callback,
                     progress_bar, progress_summarize, max_hilos=None):
    progress_summarize.emit("---> SUBPROCESO: Índice del organizado")
    estadillo = estadillo_mod.combinar_estadillos(cfg.rutas_estadillos)
    colisiones = estadillo_mod.detectar_colisiones_pb_vuelo(estadillo, nombres_columnas)
    if colisiones:
        raise ErrorColisionEstadillo(...)   # ANTES de escribir nada

    ventanas = _ventanas_por_vuelo(estadillo, pipeline, cfg)   # [(pb, vuelo, inicio, fin)]
    imagenes = _listar_imagenes(cfg.input_folder)

    # Una sola pasada de metadatos, con ThreadPool: es I/O puro.
    with ThreadPoolExecutor(max_workers=max_hilos or utils.workers_para_lote(...)) as ejecutor:
        metadatos = list(ejecutor.map(lambda ruta: _leer_metadatos(ruta, exif), imagenes))

    asignadas = [_asignar_vuelo(dato, ventanas) for dato in metadatos]
    angulos = _consenso_de_angulo_por_vuelo(asignadas, pipeline, cfg)  # una vez por PBx_Vy
    filas = [_construir_fila(dato, angulos, cfg, pipeline) for dato in asignadas]
    manifiesto.insertar_muchas(filas)
    return resumen
```

Reglas que el implementador NO puede saltarse:
- El `progress_summarize.emit` con el prefijo `---> SUBPROCESO:` es obligatorio: sin él la fase desaparece de las métricas de MB/s y CPU del modal (`MedidorRecursos.abrir_fase`).
- `_leer_metadatos` devuelve un dataclass con `ruta`, `timestamp`, `modelo`, `yaw`, `gps`; **captura sus propias excepciones** y deja los campos a `None`. Una imagen ilegible no puede tumbar el índice entero.
- `_consenso_de_angulo_por_vuelo` reutiliza `pipeline.read_auto_rotate_degree` cuando ya existe el CSV de criterio; si no existe, calcula el consenso a partir de los yaw leídos y **guarda el resultado en el manifiesto**, que es de donde lo tomará el cierre para escribir el CSV.

- [ ] **Paso 4: Ejecutar los tests y verificar que pasan**

Run: `.venv/bin/python -m pytest tests/test_indice_organizado.py -v`
Expected: 8 passed.

- [ ] **Paso 5: Commit**

```bash
git add atom_core/indice.py tests/test_indice_organizado.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): indice que decide el destino de cada imagen sin tocar pixeles"
```

---

### Tarea 4: Apply de imágenes RGB — un solo decode

**Files:**
- Create: `atom_core/apply.py`
- Test: `tests/test_apply_rgb.py`

**Contexto:** hoy una RGB pasa por ~3 ciclos decode+encode (compresión, recorte, rotación) más una copia y un move. Aquí se hace todo sobre el mismo objeto abierto y se escribe directo a su carpeta final.

**Reutilización obligatoria:** `pipeline._procesar_y_guardar_imagen(img, cfg)` (`pipeline.py:234`) con `ImageProcessConfig` (`pipeline.py:189-197`). **No escribir otra función de guardado.** Ya aplica `crop_centered_pct`, `transpose(rotate_degrees)` y guarda una sola vez con `exif=img.getexif()`.

**Calidad (decisión de Rodrigo, ver Correcciones §4):** si `angulo_giro != 0`, la calidad del guardado es `pipeline._ROTATION_JPEG_QUALITY` (40); si no, `cfg.compress_level`. Se replica el criterio del motor viejo, no se mejora.

**Interfaces:**
- Consumes: `Manifiesto` (Tarea 1), `ControladorAdaptativo` (Tarea 2), `pipeline._procesar_y_guardar_imagen`.
- Produces:

```python
def aplicar_rgb(manifiesto, cfg, pipeline, progress_callback, progress_bar,
                progress_summarize, controlador=None) -> dict: ...

def _escribir_salidas_de_fila(fila, cfg, pipeline) -> str:
    """Escribe el original y su _CROP desde UN solo decode.
    Devuelve la cadena de verificación (tamaños de los ficheros escritos)."""
```

**Escritura atómica obligatoria:** cada salida se escribe a `<destino>.parcial` y se renombra con `os.replace` al nombre final. Un fallo a mitad no puede dejar un JPEG truncado en la carpeta de entrega.

- [ ] **Paso 1: Escribir los tests que fallan**

`tests/test_apply_rgb.py`, con la fixture `make_dji_jpeg`:

1. `test_escribe_original_y_crop_desde_un_solo_decode` — contando aperturas con un doble de `Image.open` inyectado por `monkeypatch`, la imagen se abre UNA vez y aparecen los dos ficheros de salida.
2. `test_conserva_el_exif_en_la_salida` — el `DateTimeOriginal` y el GPS del origen están en el fichero final. Previene entregar imágenes sin metadatos.
3. `test_la_rotacion_usa_la_calidad_del_motor_viejo` — con `angulo_giro=90`, el guardado recibe `quality=40`; con `0`, `cfg.compress_level`.
4. `test_un_fallo_marca_la_fila_y_no_para_el_run` — una fila cuyo origen no existe queda `fallido` con motivo, y las demás acaban `hecho`.
5. `test_no_deja_ficheros_parciales` — forzando un fallo en mitad del guardado, no queda ningún `.parcial` ni fichero final a medias.
6. `test_reanudacion_salta_las_filas_hechas` — con filas ya `hecho` y su salida en disco, un segundo `aplicar_rgb` no las reescribe (comprobado por `mtime`).
7. `test_el_origen_queda_intacto` — hash del fichero de origen antes y después: idéntico.

- [ ] **Paso 2: Verificar que fallan.** Run: `.venv/bin/python -m pytest tests/test_apply_rgb.py -v` → `ModuleNotFoundError: atom_core.apply`.

- [ ] **Paso 3: Implementar `aplicar_rgb`.** Notas obligatorias:
- Emitir `progress_summarize.emit("---> SUBPROCESO: Escritura de imágenes RGB")` al empezar.
- `ProcessPoolExecutor` para el trabajo (es CPU-bound: decode/encode), con el trabajo en vuelo limitado por un `threading.Semaphore` cuyo aforo sigue a `controlador.trabajadores` (el pool no se redimensiona; se redimensiona el aforo).
- Tras cada item: `controlador.registrar(mb=<tamaño del origen en MB>)` y `controlador.revisar()`.
- `progress_bar.emit(int(porcentaje))` con la misma semántica que hoy.

- [ ] **Paso 4: Verificar que pasan.** Run: `.venv/bin/python -m pytest tests/test_apply_rgb.py -v` → 7 passed.

- [ ] **Paso 5: Commit**

```bash
git add atom_core/apply.py tests/test_apply_rgb.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): apply de RGB con un unico decode por imagen"
```

---

### Tarea 5: Apply de térmicas — `dji_irp` + metadatos

**Files:**
- Modify: `atom_core/apply.py`
- Test: `tests/test_apply_termicas.py`

**Contexto:** las térmicas no son CPU del intérprete: son espera a dos procesos externos. Por eso van en `ThreadPoolExecutor`, no en procesos.

**Reutilización obligatoria:**
- Conversión: `Pipeline.convert_dji_image_to_tif` (`pipeline.py:3441`); en Linux entra por `_dji_measure_to_raw_linux` (ctypes sobre `libdirp.so`, `pipeline.py:3628`), en Windows por el `subprocess` de `dji_irp` (`pipeline.py:3556`).
- Metadatos: `Pipeline._run_exif_batch_local` (`pipeline.py:3065`), que agrupa pares `(jpg_origen, tiff_destino)` en un solo `exiftool -stay_open`. **No lanzar un `exiftool` por imagen.**
- Giro: el ángulo sale del manifiesto (`fila["angulo_giro"]`) y se traduce a la constante PIL igual que `pipeline.py:2302-2307` (`+90` → `Image.ROTATE_270`, `-90`/`270` → `Image.ROTATE_90`). El guardado usa `quality=96, subsampling=0` como `rotate_tiff_image` (`pipeline.py:2309`).

**Produces:**

```python
def aplicar_termicas(manifiesto, cfg, pipeline, progress_callback, progress_bar,
                     progress_summarize, controlador=None, tamano_lote_exif: int = 200) -> dict: ...
```

- [ ] **Paso 1: Tests que fallan** (`tests/test_apply_termicas.py`), con un `_PipelineDePrueba` que registra las llamadas en vez de invocar `dji_irp` de verdad:

1. `test_el_tiff_usa_el_angulo_del_manifiesto_igual_que_su_jpg` — la térmica y la RGB del mismo vuelo se giran con el mismo ángulo. **Es el invariante nº1 del proyecto.**
2. `test_los_metadatos_se_copian_en_lotes` — con 5 térmicas y `tamano_lote_exif=2`, `_run_exif_batch_local` se llama 3 veces, no 5. Previene volver al `exiftool` por imagen.
3. `test_una_termica_sin_su_jpg_de_origen_queda_fallida` — motivo explícito, el resto sigue.
4. `test_el_tiff_se_escribe_de_forma_atomica` — no queda `.parcial`.
5. `test_falta_de_metadatos_marca_la_fila_como_fallida` — si `exiftool` devuelve error para un par, esa fila NO puede quedar `hecho`. Invariante: "TIFF con TODOS sus metadatos".

- [ ] **Paso 2: Verificar que fallan.**
- [ ] **Paso 3: Implementar `aplicar_termicas`** con `ThreadPoolExecutor`, emitiendo `---> SUBPROCESO: Conversión térmica` al empezar.
- [ ] **Paso 4: Verificar que pasan.** Run: `.venv/bin/python -m pytest tests/test_apply_termicas.py -v` → 5 passed.
- [ ] **Paso 5: Commit**

```bash
git add atom_core/apply.py tests/test_apply_termicas.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): apply de termicas con exiftool por lotes y angulo del manifiesto"
```

---

### Tarea 6: Cierre — CSV desde el manifiesto y verificaciones

**Files:**
- Create: `atom_core/cierre.py`
- Test: `tests/test_cierre_organizado.py`

**Contexto:** hoy los CSV se emiten incrementalmente fase a fase y las verificaciones son contadores acumulados. Aquí salen de una sola consulta al manifiesto, así que no pueden desincronizarse.

**Produces:**

```python
def emitir_csvs(manifiesto, cfg, progress_callback) -> "dict[str, str]": ...
def verificar(manifiesto, cfg) -> "list[str]":
    """Devuelve la lista de problemas encontrados. Vacía = run correcto."""
```

**Verificaciones obligatorias** (equivalentes a las de hoy, reescritas como manifiesto-contra-disco):
- `jpg_count == tiff_count` por vuelo (hoy `pipeline.py:2446-2556`).
- `crop_count == non_crop_count` (hoy `pipeline.py:3900-3960`).
- `csv_lines == image_count` (hoy `exif.py:780-864`).
- Toda fila está `hecho` o `fallido`; ninguna `pendiente` ni `en_curso`.
- Toda ruta de salida declarada `hecho` **existe en disco y no está vacía**.

**CSV que se emiten:** `meta` y `location` (hoy `exif.py:1037` y `exif.py:1053`) y `CSVs/_criterio/<PBx_Vy>_Videofiles.csv` con las columnas `['New Name','Original Name','Degree']`, donde `New Name` es `<PBx_Vy>_NNNN.JPG` con `NNNN` = índice de la imagen dentro del vuelo empezando en 1, con `zfill(4)`, en el orden de `filas_por_vuelo` (`pipeline.py:2078-2189`).

- [ ] **Paso 1: Tests que fallan** (`tests/test_cierre_organizado.py`):
1. `test_el_csv_de_criterio_numera_desde_uno_con_cuatro_digitos` — `PB1_V01_0001.JPG`.
2. `test_desajuste_jpg_tiff_se_reporta` — un vuelo con 3 JPG y 2 TIFF sale en la lista de problemas.
3. `test_desajuste_crop_se_reporta`.
4. `test_fila_hecha_sin_fichero_en_disco_se_reporta` — el manifiesto dice `hecho` pero el fichero no está: es el fallo más peligroso posible y tiene que salir.
5. `test_run_completo_y_correcto_no_reporta_problemas` — lista vacía.
6. `test_las_filas_pendientes_se_reportan` — un run interrumpido no se da por bueno.

- [ ] **Paso 2: Verificar que fallan.**
- [ ] **Paso 3: Implementar `atom_core/cierre.py`.**
- [ ] **Paso 4: Verificar que pasan.** → 6 passed.
- [ ] **Paso 5: Commit**

```bash
git add atom_core/cierre.py tests/test_cierre_organizado.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): cierre que emite CSVs y verifica contra el manifiesto"
```

---

### Tarea 7: Cableado en el orquestador

**Files:**
- Modify: `atom_core/phases.py` (nuevo método `organizar_plan_apply`; `split_images` en :354-781 deja de invocarse)
- Modify: `atom_core/organize.py` (`_SPLIT_PHASES` y `_active_split_phases`, ~275-300; orden de fases ~291-299; llamada ~762)
- Test: `tests/test_organizado_plan_apply_e2e.py`

**Firma obligatoria** (la misma que el resto de fases, para no tocar el llamante):

```python
def organizar_plan_apply(self, cfg, progress_callback, progress_bar, progress_summarize) -> None:
```

Secuencia: `Manifiesto.crear_esquema()` → `reabrir_huerfanas()` → `construir_indice(...)` → `aplicar_rgb(...)` → `aplicar_termicas(...)` → `emitir_csvs(...)` → `verificar(...)`. Si `verificar` devuelve problemas, se emiten por `progress_callback` y el run termina en aviso, no en silencio.

**Lista de fases del modal:** `_SPLIT_PHASES` pasa a ser `["Índice", "Imágenes RGB", "Conversión térmica", "Cierre"]`. Cada una debe emitir su `---> SUBPROCESO: <nombre>` por `progress_summarize` **con exactamente el mismo texto**, o `MedidorRecursos.abrir_fase` no la reconoce y desaparecen las métricas de MB/s y CPU del modal.

- [ ] **Paso 1: Tests que fallan** (`tests/test_organizado_plan_apply_e2e.py`), sobre una inspección sintética completa con `tmp_inspection` y `make_dji_jpeg`:
1. `test_organizado_completo_produce_el_arbol_esperado` — carpetas `PBx_Vy/RGB`, `PBx_Vy/TERMICA`, `SIN_ORDENAR`, `CSVs/_criterio/`.
2. `test_emite_una_fase_por_cada_etapa_con_el_prefijo_de_subproceso` — se capturan los `emit` del canal summary y se comprueba que los 4 prefijos `---> SUBPROCESO:` aparecen. **Previene perder las métricas de v3.4.80.**
3. `test_la_barra_de_progreso_llega_a_cien`.
4. `test_el_origen_no_se_modifica` — hash del árbol de origen idéntico antes y después.
5. `test_un_segundo_run_es_idempotente` — repetir el organizado sobre el mismo destino no duplica ficheros ni cambia el árbol.

- [ ] **Paso 2: Verificar que fallan.**
- [ ] **Paso 3: Implementar el cableado.** No borrar `split_images` en este commit: dejarlo sin invocar. Se retira en la Tarea 8, cuando la validación contra planta real haya pasado.
- [ ] **Paso 4: Verificar que pasan** y correr la suite entera: `.venv/bin/python -m pytest -q`.
- [ ] **Paso 5: Comprobar el frontend:** `cd webui && npx vitest run`.
- [ ] **Paso 6: Commit**

```bash
git add atom_core/phases.py atom_core/organize.py tests/test_organizado_plan_apply_e2e.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(organizado): cablear el motor plan-apply en el orquestador"
```

---

### Tarea 8: Validación contra planta real y release

**Files:**
- Modify: `.workflow/LEDGER-organizado-plan-apply.md`
- Modify: `atom_core/phases.py` (retirar `split_images` y las fases muertas) — **solo si la validación pasa**

**Esta tarea NO la ejecuta un subagente.** Necesita datos y decisiones de Rodrigo.

- [ ] **Paso 1: Pedir a Rodrigo las rutas de KL19** — eligió KL19 como planta de referencia (2026-09-08), pero faltan: carpeta de origen, ruta del estadillo y ruta de la salida buena ya organizada con el motor viejo.

- [ ] **Paso 2: Organizar KL19 con el motor nuevo** a un destino distinto, sin tocar la salida buena.

- [ ] **Paso 3: Comparar**

```bash
.venv/bin/python tools/comparar_organizados.py <salida_buena> <salida_nueva> --json /tmp/diff-kl19.json
```

- [ ] **Paso 4: Interpretar el resultado**
- Árbol distinto (ficheros de más o de menos, o nombres distintos) → **bloquea**, es un fallo del índice.
- TIFF con diferencia byte a byte → **bloquea**, es un fallo de la conversión o del giro.
- CSV con diferencia de fila → **bloquea**.
- RGB **no giradas** fuera de tolerancia → **bloquea**.
- RGB **giradas** fuera de tolerancia → esperado hasta cierto punto (ver Correcciones §4: el viejo hace 85→40 y el nuevo un solo 40). Anotar el `dif_max` y el `rmse` reales observados, decidir con Rodrigo si son aceptables, y **documentar la tolerancia calibrada en la spec**. Si no son aceptables, el apply debe replicar también el doble encode.

- [ ] **Paso 5: Solo si todo lo anterior pasa** — retirar `split_images` y las fases muertas de `phases.py`, correr `.venv/bin/python -m pytest -q` y `cd webui && npx vitest run`, y commitear.

- [ ] **Paso 6: Cerrar el ledger** — todos los items en `- [x]`, y renombrar a `.workflow/LEDGER-organizado-plan-apply-archive.md`.

- [ ] **Paso 7: Release** — solo con el diff limpio, `pytest` verde y `vitest` verde, y **OK expreso de Rodrigo**. Versión: PATCH sobre la última publicada.

---

## Verificación final del plan

Antes de dar el trabajo por terminado:

```bash
.venv/bin/python -m pytest -q
cd webui
npx vitest run
```

Ambos verdes, sin excepciones. Y el ledger `.workflow/LEDGER-organizado-plan-apply.md` sin ningún `- [ ]` abierto.
