# Estadillos en ubicación canónica del bucket — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el estadillo acabe siempre en una ruta determinista del bucket, con formato normalizado estable, sin depender de cómo lo nombre ni dónde lo ponga el operario.

**Architecture:** Se añade un módulo puro `atom_core/estadillo_canonico.py` que calcula rutas, nombres, manifest y plan de subida sin tocar red, más una función de validación en el módulo de estadillos que ya existe. La app expone dos acciones nuevas en la clase `Api` (validar y subir), independientes de organizar, de modo que la ubicación canónica es la misma en modo local (organizar → Drive → bucket) y en modo RAW.

**Tech Stack:** Python 3 + pandas 1.5.3, pytest, cliente GCS propio resumable (`atom_core/cloud_upload.py`), webui en React + Vitest, pywebview como puente JS↔Python.

**Spec:** `docs/superpowers/specs/2026-08-17-estadillos-canonicos-bucket-design.md`

## Alcance de este plan

Solo el repo `atom-organizer-work`. Al terminar, el estadillo se valida, se sube a la ruta canónica con crudo + normalizado + manifest, y se notifica a la Suite incluyendo la ruta del manifest.

**Fuera de alcance** (plan aparte, repo `Atom-suite`, toca producción): que la Suite guarde la ruta del manifest en `organizer_operaciones.estadillo`, que pase el manifest al Cloud Run Job, y retirar `elegirEstadillo` del camino nuevo. Hasta que ese plan entre, la Suite sigue funcionando como hoy: este plan no rompe nada suyo porque solo **añade** un campo al body que ya manda.

## Global Constraints

- pandas está pinneado a `pandas==1.5.3` (`requirements.txt:31`). No subir de versión.
- El `<PLANTA>` del bucket se obtiene **únicamente** con `prefijo_desde_carpeta()` (`atom_core/cloud_config.py:121-140`). No añadir una segunda normalización de nombres de planta.
- Hash de contenido: MD5, reutilizando `_file_md5_b64()` (`atom_core/cloud_upload.py:677-687`). No introducir sha256.
- Prefijo canónico literal: `PREPARACION/ESTADILLOS`.
- Ningún test toca red ni el bucket. La subida se prueba inyectando un uploader falso.
- 🚫 `gs://plantas_pv_nl` es solo para plantas. Ninguna prueba escribe en prefijos inventados.
- Tests Python: `python -m pytest tests/ -q --ignore=tests/test_dark_theme.py`
- Tests webui: `cd webui && npm test`
- Estilo de test del repo: import directo del módulo, nombres en español (`test_<condición>_si_<caso>`), un assert simple por test.
- NO commitear nada fuera de los ficheros que cada task lista.

---

### Task 1: Declarar openpyxl

`pd.read_excel` necesita openpyxl y no está declarado en ningún requirements. Hoy funciona por transitiva; en instalación limpia un estadillo `.xlsx` reventaría. Todo este plan depende de leer xlsx, así que va primero.

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-linux.txt`
- Modify: `requirements-server.txt`
- Modify: `requirements-webview.txt`
- Test: `tests/test_estadillo_xlsx.py`

**Interfaces:**
- Consumes: nada.
- Produces: garantía de que `atom_core.estadillo._read_dataframe` puede leer `.xlsx`.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_estadillo_xlsx.py`:

```python
import pandas as pd

from atom_core import estadillo


def test_lee_xlsx_si_openpyxl_disponible(tmp_path):
    ruta = tmp_path / "estadillo.xlsx"
    pd.DataFrame(
        {
            "PB": ["1"],
            "Vuelo": ["1"],
            "Fecha": ["2026-08-17"],
            "Hora_de_inicio": ["09:12:33"],
            "Hora_final": ["09:41:02"],
        }
    ).to_excel(ruta, index=False)

    df = estadillo._read_dataframe(str(ruta))

    assert list(df["PB"]) == ["1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -c "import openpyxl" ; python -m pytest tests/test_estadillo_xlsx.py -q`

Expected: si openpyxl no está en el entorno, FAIL con `ImportError: Missing optional dependency 'openpyxl'`. Si ya está instalado como transitiva, el test PASA — eso es esperado y no invalida la task: el objetivo es **declarar** la dependencia para que un entorno limpio no rompa. Anotar cuál de los dos casos se ha observado.

- [ ] **Step 3: Declarar la dependencia**

Añadir la línea a los cuatro ficheros, junto a las demás dependencias (respetar el orden alfabético si el fichero lo lleva):

```
openpyxl==3.1.2
```

- [ ] **Step 4: Verificar en entorno limpio**

Run: `python -m pytest tests/test_estadillo_xlsx.py -q`
Expected: PASS

Y comprobar que la línea está en los cuatro:

Run: `grep -n openpyxl requirements.txt requirements-linux.txt requirements-server.txt requirements-webview.txt`
Expected: cuatro líneas, una por fichero.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt requirements-linux.txt requirements-server.txt requirements-webview.txt tests/test_estadillo_xlsx.py
git commit -m "fix: declarar openpyxl, necesario para leer estadillos xlsx"
```

---

### Task 2: Validar estadillos antes de subir

El requisito es que al subir confirme que carga bien. La validación reutiliza `combinar_estadillos` (que ya valida cabeceras y lanza `EstadilloHeaderError`) y `filas_para_suite` (que ya produce el shape final). No se escribe parser nuevo.

**Files:**
- Modify: `atom_core/estadillo.py` (añadir al final del módulo)
- Test: `tests/test_estadillo_validacion.py`

**Interfaces:**
- Consumes: `combinar_estadillos(rutas: list[str]) -> pd.DataFrame` (`atom_core/estadillo.py:116`), `filas_para_suite(df, origen_por_fila=None) -> list[dict]` (`:277`), `EstadilloHeaderError` (`:61`).
- Produces: `validar_para_subida(rutas: list[str]) -> dict` con claves exactas `ok: bool`, `error: str | None`, `vuelos: list[dict]`, `vuelos_detectados: int`, `filas_con_problemas: int`. Lo consumen las Tasks 4, 5 y 6.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_estadillo_validacion.py`:

```python
import pandas as pd

from atom_core import estadillo


def _escribir_csv(ruta, filas):
    pd.DataFrame(filas).to_csv(ruta, sep=";", index=False)


def _fila(pb="1", vuelo="1", fecha="2026-08-17", inicio="09:12:33", final="09:41:02"):
    return {
        "PB": pb,
        "Vuelo": vuelo,
        "Fecha": fecha,
        "Hora_de_inicio": inicio,
        "Hora_final": final,
    }


def test_ok_si_cabeceras_correctas(tmp_path):
    ruta = tmp_path / "e.csv"
    _escribir_csv(ruta, [_fila(), _fila(vuelo="2")])

    res = estadillo.validar_para_subida([str(ruta)])

    assert res["ok"] is True
    assert res["error"] is None
    assert res["vuelos_detectados"] == 2
    assert len(res["vuelos"]) == 2


def test_falla_si_falta_columna_esencial(tmp_path):
    ruta = tmp_path / "e.csv"
    fila = _fila()
    del fila["PB"]
    _escribir_csv(ruta, [fila])

    res = estadillo.validar_para_subida([str(ruta)])

    assert res["ok"] is False
    assert res["error"]
    assert res["vuelos"] == []
    assert res["vuelos_detectados"] == 0


def test_falla_si_no_hay_rutas():
    res = estadillo.validar_para_subida([])

    assert res["ok"] is False
    assert res["error"]


def test_cuenta_filas_con_problemas_si_falta_fecha(tmp_path):
    ruta = tmp_path / "e.csv"
    _escribir_csv(ruta, [_fila(), _fila(vuelo="2", fecha="")])

    res = estadillo.validar_para_subida([str(ruta)])

    assert res["ok"] is True
    assert res["vuelos_detectados"] == 2
    assert res["filas_con_problemas"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_estadillo_validacion.py -q`
Expected: FAIL con `AttributeError: module 'atom_core.estadillo' has no attribute 'validar_para_subida'`

- [ ] **Step 3: Write minimal implementation**

Añadir al final de `atom_core/estadillo.py`:

```python
def validar_para_subida(rutas: list[str]) -> dict:
    """Valida N estadillos y devuelve el resumen que se muestra al operario.

    No sube nada ni toca red. Es la puerta que impide subir un estadillo que
    no se puede leer: si `ok` es False, la subida no debe ocurrir.
    """
    vacio = {
        "ok": False,
        "error": None,
        "vuelos": [],
        "vuelos_detectados": 0,
        "filas_con_problemas": 0,
    }

    limpias = [str(r).strip() for r in (rutas or []) if str(r).strip()]
    if not limpias:
        return {**vacio, "error": "No se ha seleccionado ningún estadillo."}

    try:
        df = combinar_estadillos(limpias)
        vuelos = filas_para_suite(df)
    except EstadilloHeaderError as exc:
        return {**vacio, "error": str(exc)}
    except Exception as exc:  # fichero corrupto, extensión ilegible, etc.
        return {**vacio, "error": f"No se ha podido leer el estadillo: {exc}"}

    problemas = sum(
        1
        for v in vuelos
        if not v.get("fecha") or not v.get("hora_inicio") or not v.get("pb")
    )

    return {
        "ok": True,
        "error": None,
        "vuelos": vuelos,
        "vuelos_detectados": len(vuelos),
        "filas_con_problemas": problemas,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_estadillo_validacion.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add atom_core/estadillo.py tests/test_estadillo_validacion.py
git commit -m "feat: validar_para_subida, valida estadillos y resume lo entendido"
```

---

### Task 3: Rutas y nombres canónicos

Módulo nuevo con funciones puras. Sin red, sin ficheros: solo cálculo de strings.

**Files:**
- Create: `atom_core/estadillo_canonico.py`
- Test: `tests/test_estadillo_canonico_rutas.py`

**Interfaces:**
- Consumes: `prefijo_desde_carpeta(nombre: str) -> str` (`atom_core/cloud_config.py:121-140`).
- Produces:
  - `PREFIJO_ESTADILLOS: str` = `"PREPARACION/ESTADILLOS"`
  - `NOMBRE_MANIFEST: str` = `"manifest.json"`
  - `NOMBRE_NORMALIZADO: str` = `"estadillo.json"`
  - `CARPETA_ACTUAL: str` = `"actual"`
  - `carpeta_subida(ahora: datetime) -> str`
  - `prefijo_planta(planta: str) -> str`
  - `nombre_objeto(orden: int, md5_hex: str, ext: str) -> str`
  - `md5_hex_desde_b64(md5_b64: str) -> str`
  Los consumen las Tasks 4 y 5.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_estadillo_canonico_rutas.py`:

```python
from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec


def test_carpeta_subida_usa_timestamp_utc_ordenable():
    ahora = datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc)

    assert ec.carpeta_subida(ahora) == "2026-08-17T034501Z"


def test_carpetas_de_subida_ordenan_alfabeticamente_por_tiempo():
    antes = ec.carpeta_subida(datetime(2026, 8, 14, 9, 12, 33, tzinfo=timezone.utc))
    despues = ec.carpeta_subida(datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc))

    assert sorted([despues, antes]) == [antes, despues]


def test_prefijo_planta_normaliza_el_nombre():
    assert ec.prefijo_planta("MARISOLES_LOS MANGOS") == (
        "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS"
    )


def test_nombre_objeto_lleva_orden_con_dos_digitos_y_md5_corto():
    assert ec.nombre_objeto(1, "9f3c2e11aabbccdd", ".xlsx") == "01__9f3c2e11.xlsx"
    assert ec.nombre_objeto(12, "9f3c2e11aabbccdd", ".csv") == "12__9f3c2e11.csv"


def test_nombre_objeto_normaliza_extension_sin_punto():
    assert ec.nombre_objeto(1, "9f3c2e11aabbccdd", "xlsx") == "01__9f3c2e11.xlsx"


def test_md5_hex_desde_b64_convierte_el_hash_de_gcs():
    # MD5 de b"hola" es 4d186321c1a7f0f354b297e8914ab240
    import base64
    import hashlib

    b64 = base64.b64encode(hashlib.md5(b"hola").digest()).decode()

    assert ec.md5_hex_desde_b64(b64) == "4d186321c1a7f0f354b297e8914ab240"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_estadillo_canonico_rutas.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'atom_core.estadillo_canonico'`

- [ ] **Step 3: Write minimal implementation**

Crear `atom_core/estadillo_canonico.py`:

```python
"""Ubicación canónica de los estadillos en el bucket.

Funciones puras: calculan rutas, nombres y manifest. No tocan red ni disco,
para que la ruta de un estadillo sea reproducible y testeable sin bucket.
"""

import base64
from datetime import datetime

from atom_core.cloud_config import prefijo_desde_carpeta

PREFIJO_ESTADILLOS = "PREPARACION/ESTADILLOS"
NOMBRE_MANIFEST = "manifest.json"
NOMBRE_NORMALIZADO = "estadillo.json"
CARPETA_ACTUAL = "actual"

VERSION_MANIFEST = 1
VERSION_NORMALIZADO = 1


def carpeta_subida(ahora: datetime) -> str:
    """Identificador de subida: fecha+hora UTC, ordenable alfabéticamente.

    No se usa el run_id de la Suite a propósito: lo genera el servidor y sin
    login no existe, así que la ruta no puede depender de él.
    """
    return ahora.strftime("%Y-%m-%dT%H%M%SZ")


def prefijo_planta(planta: str) -> str:
    return f"{prefijo_desde_carpeta(planta)}/{PREFIJO_ESTADILLOS}"


def nombre_objeto(orden: int, md5_hex: str, ext: str) -> str:
    sufijo = ext if ext.startswith(".") else f".{ext}"
    return f"{orden:02d}__{md5_hex[:8]}{sufijo}"


def md5_hex_desde_b64(md5_b64: str) -> str:
    return base64.b64decode(md5_b64).hex()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_estadillo_canonico_rutas.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add atom_core/estadillo_canonico.py tests/test_estadillo_canonico_rutas.py
git commit -m "feat: rutas y nombres canonicos de estadillos en el bucket"
```

---

### Task 4: Manifest y normalizado

El manifest es la fuente de verdad del orden de prioridad. El normalizado es `filas_para_suite` serializado, para que el estadillo se pueda leer a futuro aunque el crudo cambie de formato.

**Files:**
- Modify: `atom_core/estadillo_canonico.py`
- Test: `tests/test_estadillo_canonico_manifest.py`

**Interfaces:**
- Consumes: `VERSION_MANIFEST`, `VERSION_NORMALIZADO` (Task 3); el dict de `validar_para_subida` (Task 2).
- Produces:
  - `construir_manifest(planta, subido_en, subido_por, ficheros, validacion) -> dict` donde `ficheros` es `list[dict]` con claves `orden, objeto, nombre_original, md5_b64, bytes`.
  - `construir_normalizado(vuelos: list[dict]) -> dict`
  Los consume la Task 5.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_estadillo_canonico_manifest.py`:

```python
from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec


def _ficheros():
    return [
        {
            "orden": 1,
            "objeto": "01__9f3c2e11.xlsx",
            "nombre_original": "Estadillo VUELOS 17 agosto (2).xlsx",
            "md5_b64": "nzwuEQ==",
            "bytes": 48213,
        }
    ]


def _validacion():
    return {"vuelos_detectados": 34, "filas_con_problemas": 0}


def test_manifest_lleva_version_y_planta():
    m = ec.construir_manifest(
        planta="MARISOLES_LOS MANGOS",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por="daniel@aerotools.es",
        ficheros=_ficheros(),
        validacion=_validacion(),
    )

    assert m["version"] == 1
    assert m["planta"] == "MARISOLES_LOS_MANGOS"
    assert m["subido_en"] == "2026-08-17T034501Z"
    assert m["subido_por"] == "daniel@aerotools.es"


def test_manifest_conserva_nombre_original_como_metadato():
    m = ec.construir_manifest(
        planta="X",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por=None,
        ficheros=_ficheros(),
        validacion=_validacion(),
    )

    assert m["ficheros"][0]["nombre_original"] == "Estadillo VUELOS 17 agosto (2).xlsx"
    assert m["ficheros"][0]["objeto"] == "01__9f3c2e11.xlsx"


def test_manifest_guarda_el_orden_de_prioridad():
    ficheros = [
        {"orden": 2, "objeto": "02__b.csv", "nombre_original": "b", "md5_b64": "x", "bytes": 1},
        {"orden": 1, "objeto": "01__a.xlsx", "nombre_original": "a", "md5_b64": "y", "bytes": 2},
    ]

    m = ec.construir_manifest(
        planta="X",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por=None,
        ficheros=ficheros,
        validacion=_validacion(),
    )

    assert [f["orden"] for f in m["ficheros"]] == [1, 2]


def test_manifest_incluye_resumen_de_validacion():
    m = ec.construir_manifest(
        planta="X",
        subido_en=datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc),
        subido_por=None,
        ficheros=_ficheros(),
        validacion={"vuelos_detectados": 34, "filas_con_problemas": 2},
    )

    assert m["validacion"] == {"vuelos_detectados": 34, "filas_con_problemas": 2}


def test_normalizado_usa_el_shape_que_acepta_la_suite():
    vuelos = [
        {
            "fecha": "2026-08-17",
            "piloto": "Daniel",
            "equipo_vuelo": "E1",
            "pb": "1",
            "num_vuelo": "1",
            "hora_inicio": "09:12:33",
            "hora_fin": "09:41:02",
            "origen": None,
        }
    ]

    n = ec.construir_normalizado(vuelos)

    assert n["version"] == 1
    assert n["vuelos"] == vuelos
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_estadillo_canonico_manifest.py -q`
Expected: FAIL con `AttributeError: module 'atom_core.estadillo_canonico' has no attribute 'construir_manifest'`

- [ ] **Step 3: Write minimal implementation**

Añadir a `atom_core/estadillo_canonico.py`:

```python
def construir_manifest(
    planta: str,
    subido_en: datetime,
    subido_por: str | None,
    ficheros: list[dict],
    validacion: dict,
) -> dict:
    """Manifest de una subida. Es la fuente de verdad del orden de prioridad.

    El nombre que puso el operario se conserva como metadato, nunca como
    identidad: la identidad es `objeto`.
    """
    return {
        "version": VERSION_MANIFEST,
        "planta": prefijo_desde_carpeta(planta),
        "subido_en": carpeta_subida(subido_en),
        "subido_por": subido_por,
        "ficheros": sorted(ficheros, key=lambda f: f["orden"]),
        "validacion": {
            "vuelos_detectados": validacion.get("vuelos_detectados", 0),
            "filas_con_problemas": validacion.get("filas_con_problemas", 0),
        },
    }


def construir_normalizado(vuelos: list[dict]) -> dict:
    """Formato estable del estadillo, independiente de si el crudo es xlsx o csv."""
    return {"version": VERSION_NORMALIZADO, "vuelos": vuelos}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_estadillo_canonico_manifest.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add atom_core/estadillo_canonico.py tests/test_estadillo_canonico_manifest.py
git commit -m "feat: manifest y formato normalizado del estadillo"
```

---

### Task 5: Plan de subida en orden seguro

Qué objetos se escriben y **en qué orden**, como dato puro. El manifest va último para que su presencia signifique "subida completa"; si la subida se corta, la carpeta queda inválida por construcción.

**Files:**
- Modify: `atom_core/estadillo_canonico.py`
- Test: `tests/test_estadillo_canonico_plan.py`

**Interfaces:**
- Consumes: todo lo de Tasks 3 y 4.
- Produces: `plan_subida(planta, ficheros_locales, vuelos, validacion, ahora, subido_por=None) -> list[dict]`, donde `ficheros_locales` es `list[dict]` con `orden, ruta, nombre_original, md5_b64, bytes, ext`. Cada elemento devuelto tiene `remoto: str` y **o** `ruta_local: str` **o** `contenido: dict` (JSON a serializar), nunca ambos. Lo consume la Task 6.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_estadillo_canonico_plan.py`:

```python
from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec

AHORA = datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc)


def _locales():
    return [
        {
            "orden": 1,
            "ruta": "/home/op/Estadillo raro (2).xlsx",
            "nombre_original": "Estadillo raro (2).xlsx",
            "md5_b64": "TRhjIcGn8PNUspfokUqyQA==",
            "bytes": 10,
            "ext": ".xlsx",
        }
    ]


def _plan():
    return ec.plan_subida(
        planta="MARISOLES_LOS MANGOS",
        ficheros_locales=_locales(),
        vuelos=[{"pb": "1"}],
        validacion={"vuelos_detectados": 1, "filas_con_problemas": 0},
        ahora=AHORA,
    )


def test_el_crudo_va_a_la_carpeta_con_timestamp_con_nombre_determinista():
    plan = _plan()
    base = "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS/2026-08-17T034501Z"

    assert plan[0]["remoto"] == f"{base}/01__4d186321.xlsx"
    assert plan[0]["ruta_local"] == "/home/op/Estadillo raro (2).xlsx"


def test_el_manifest_se_escribe_despues_del_normalizado_y_del_crudo():
    remotos = [p["remoto"] for p in _plan()]
    base = "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS/2026-08-17T034501Z"

    assert remotos.index(f"{base}/01__4d186321.xlsx") < remotos.index(
        f"{base}/estadillo.json"
    )
    assert remotos.index(f"{base}/estadillo.json") < remotos.index(
        f"{base}/manifest.json"
    )


def test_actual_se_escribe_entera_despues_de_la_carpeta_con_timestamp():
    remotos = [p["remoto"] for p in _plan()]
    base = "MARISOLES_LOS_MANGOS/PREPARACION/ESTADILLOS"

    assert remotos.index(f"{base}/2026-08-17T034501Z/manifest.json") < remotos.index(
        f"{base}/actual/01__4d186321.xlsx"
    )
    assert remotos[-1] == f"{base}/actual/manifest.json"


def test_cada_entrada_es_o_fichero_local_o_contenido_json():
    for entrada in _plan():
        assert ("ruta_local" in entrada) != ("contenido" in entrada)


def test_el_normalizado_contiene_los_vuelos():
    plan = _plan()
    normalizado = next(p for p in plan if p["remoto"].endswith("2026-08-17T034501Z/estadillo.json"))

    assert normalizado["contenido"]["vuelos"] == [{"pb": "1"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_estadillo_canonico_plan.py -q`
Expected: FAIL con `AttributeError: module 'atom_core.estadillo_canonico' has no attribute 'plan_subida'`

- [ ] **Step 3: Write minimal implementation**

Añadir a `atom_core/estadillo_canonico.py`:

```python
def plan_subida(
    planta: str,
    ficheros_locales: list[dict],
    vuelos: list[dict],
    validacion: dict,
    ahora: datetime,
    subido_por: str | None = None,
) -> list[dict]:
    """Objetos a escribir, EN ORDEN.

    El manifest va último en cada carpeta: su presencia es la señal de
    "subida completa", así que una subida cortada deja la carpeta inválida
    sin necesidad de estado extra.
    """
    base = prefijo_planta(planta)
    carpeta = carpeta_subida(ahora)

    ficheros_manifest = []
    entradas_crudo = []
    for f in sorted(ficheros_locales, key=lambda x: x["orden"]):
        objeto = nombre_objeto(f["orden"], md5_hex_desde_b64(f["md5_b64"]), f["ext"])
        entradas_crudo.append({"objeto": objeto, "ruta_local": f["ruta"]})
        ficheros_manifest.append(
            {
                "orden": f["orden"],
                "objeto": objeto,
                "nombre_original": f["nombre_original"],
                "md5_b64": f["md5_b64"],
                "bytes": f["bytes"],
            }
        )

    manifest = construir_manifest(
        planta=planta,
        subido_en=ahora,
        subido_por=subido_por,
        ficheros=ficheros_manifest,
        validacion=validacion,
    )
    normalizado = construir_normalizado(vuelos)

    plan = []
    for destino in (carpeta, CARPETA_ACTUAL):
        for entrada in entradas_crudo:
            plan.append(
                {
                    "remoto": f"{base}/{destino}/{entrada['objeto']}",
                    "ruta_local": entrada["ruta_local"],
                }
            )
        plan.append(
            {
                "remoto": f"{base}/{destino}/{NOMBRE_NORMALIZADO}",
                "contenido": normalizado,
            }
        )
        plan.append(
            {
                "remoto": f"{base}/{destino}/{NOMBRE_MANIFEST}",
                "contenido": manifest,
            }
        )

    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_estadillo_canonico_plan.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add atom_core/estadillo_canonico.py tests/test_estadillo_canonico_plan.py
git commit -m "feat: plan de subida del estadillo con manifest al final"
```

---

### Task 6: Ejecutar la subida

Ejecuta el plan contra el bucket. El uploader se inyecta para poder probar el orden y los cortes sin tocar red.

**Files:**
- Modify: `atom_core/estadillo_canonico.py`
- Test: `tests/test_estadillo_canonico_ejecutar.py`

**Interfaces:**
- Consumes: `plan_subida` (Task 5).
- Produces: `ejecutar_plan(plan: list[dict], subir_fichero, subir_json) -> dict` con claves `ok: bool`, `subidos: int`, `ruta_manifest: str | None`, `error: str | None`. `subir_fichero(remoto: str, ruta_local: str) -> None` y `subir_json(remoto: str, obj: dict) -> None` son callables; cualquier excepción que lancen aborta el plan. `ruta_manifest` es el manifest de la carpeta con timestamp, no el de `actual/`. Lo consume la Task 7.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_estadillo_canonico_ejecutar.py`:

```python
from datetime import datetime, timezone

from atom_core import estadillo_canonico as ec

AHORA = datetime(2026, 8, 17, 3, 45, 1, tzinfo=timezone.utc)


def _plan():
    return ec.plan_subida(
        planta="X",
        ficheros_locales=[
            {
                "orden": 1,
                "ruta": "/tmp/e.xlsx",
                "nombre_original": "e.xlsx",
                "md5_b64": "TRhjIcGn8PNUspfokUqyQA==",
                "bytes": 10,
                "ext": ".xlsx",
            }
        ],
        vuelos=[{"pb": "1"}],
        validacion={"vuelos_detectados": 1, "filas_con_problemas": 0},
        ahora=AHORA,
    )


def test_sube_todo_el_plan_en_orden():
    escritos = []

    res = ec.ejecutar_plan(
        _plan(),
        subir_fichero=lambda remoto, ruta: escritos.append(remoto),
        subir_json=lambda remoto, obj: escritos.append(remoto),
    )

    assert res["ok"] is True
    assert res["subidos"] == 6
    assert escritos[-1].endswith("actual/manifest.json")


def test_devuelve_la_ruta_del_manifest_con_timestamp_no_la_de_actual():
    res = ec.ejecutar_plan(
        _plan(),
        subir_fichero=lambda remoto, ruta: None,
        subir_json=lambda remoto, obj: None,
    )

    assert res["ruta_manifest"] == (
        "X/PREPARACION/ESTADILLOS/2026-08-17T034501Z/manifest.json"
    )


def test_aborta_sin_escribir_manifest_si_falla_un_crudo():
    escritos = []

    def subir_fichero(remoto, ruta):
        raise OSError("conexion caida")

    res = ec.ejecutar_plan(
        _plan(),
        subir_fichero=subir_fichero,
        subir_json=lambda remoto, obj: escritos.append(remoto),
    )

    assert res["ok"] is False
    assert res["error"]
    assert res["ruta_manifest"] is None
    assert escritos == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_estadillo_canonico_ejecutar.py -q`
Expected: FAIL con `AttributeError: module 'atom_core.estadillo_canonico' has no attribute 'ejecutar_plan'`

- [ ] **Step 3: Write minimal implementation**

Añadir a `atom_core/estadillo_canonico.py`:

```python
def ejecutar_plan(plan: list[dict], subir_fichero, subir_json) -> dict:
    """Ejecuta el plan en orden y aborta en el primer fallo.

    Abortar deja la carpeta sin manifest, que es exactamente lo que queremos:
    ningún consumidor la considerará válida.
    """
    subidos = 0
    ruta_manifest = None

    for entrada in plan:
        remoto = entrada["remoto"]
        try:
            if "ruta_local" in entrada:
                subir_fichero(remoto, entrada["ruta_local"])
            else:
                subir_json(remoto, entrada["contenido"])
        except Exception as exc:
            return {
                "ok": False,
                "subidos": subidos,
                "ruta_manifest": None,
                "error": f"Fallo subiendo {remoto}: {exc}",
            }

        subidos += 1
        if remoto.endswith(f"/{NOMBRE_MANIFEST}") and ruta_manifest is None:
            ruta_manifest = remoto

    return {"ok": True, "subidos": subidos, "ruta_manifest": ruta_manifest, "error": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_estadillo_canonico_ejecutar.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add atom_core/estadillo_canonico.py tests/test_estadillo_canonico_ejecutar.py
git commit -m "feat: ejecutar el plan de subida del estadillo, abortando sin manifest"
```

---

### Task 7: Acciones en la app de escritorio

Dos métodos nuevos en la clase `Api`. Son acciones propias, **no** cuelgan de organizar ni de subir la jornada: eso es lo que hace la ubicación canónica invariante al modo (local→Drive→bucket o RAW).

**Files:**
- Modify: `app_webview.py` (añadir métodos a la clase `Api`, definida en `app_webview.py:108`; colocarlos junto a las demás acciones `cloud_*`, tras `cloud_upload` en `:573-641`)
- Test: `tests/test_api_estadillo.py`

**Interfaces:**
- Consumes: `estadillo.validar_para_subida` (Task 2), `estadillo_canonico.plan_subida` y `ejecutar_plan` (Tasks 5-6), `_file_md5_b64()` (`atom_core/cloud_upload.py:677-687`), `upload_file()` (`atom_core/cloud_upload.py:710`), `self._push_cloud({...})` (`app_webview.py:649`).
- Produces:
  - `Api.estadillo_validar(self, rutas: list[str]) -> dict` — **síncrono**, devuelve el dict de `validar_para_subida` sin la clave `vuelos` (el webui no necesita las filas).
  - `Api.estadillo_subir(self, folder: str, rutas: list[str]) -> dict` — devuelve `{"started": bool, "reason": str | None}` como `cloud_upload`, y empuja eventos `kind: start|done|error` con `self._push_cloud`.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_api_estadillo.py`. Se prueba el contrato sin abrir ventana ni tocar red, instanciando `Api` y monkeypatcheando lo que sale al mundo:

```python
import pandas as pd
import pytest

import app_webview


@pytest.fixture
def api():
    return app_webview.Api()


def _csv(tmp_path, nombre="e.csv"):
    ruta = tmp_path / nombre
    pd.DataFrame(
        {
            "PB": ["1"],
            "Vuelo": ["1"],
            "Fecha": ["2026-08-17"],
            "Hora_de_inicio": ["09:12:33"],
            "Hora_final": ["09:41:02"],
        }
    ).to_csv(ruta, sep=";", index=False)
    return str(ruta)


def test_validar_devuelve_resumen_sin_las_filas(api, tmp_path):
    res = api.estadillo_validar([_csv(tmp_path)])

    assert res["ok"] is True
    assert res["vuelos_detectados"] == 1
    assert "vuelos" not in res


def test_validar_reporta_error_si_no_carga(api, tmp_path):
    ruta = tmp_path / "malo.csv"
    ruta.write_text("no;son;cabeceras\n1;2;3\n", encoding="utf-8")

    res = api.estadillo_validar([str(ruta)])

    assert res["ok"] is False
    assert res["error"]


def test_subir_no_arranca_si_la_validacion_falla(api, tmp_path):
    ruta = tmp_path / "malo.csv"
    ruta.write_text("no;son;cabeceras\n1;2;3\n", encoding="utf-8")

    res = api.estadillo_subir("MI_PLANTA", [str(ruta)])

    assert res["started"] is False
    assert res["reason"]


def test_subir_arranca_si_la_validacion_pasa(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_subir_estadillo_worker", lambda *a, **k: None)

    res = api.estadillo_subir("MI_PLANTA", [_csv(tmp_path)])

    assert res["started"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_estadillo.py -q`
Expected: FAIL con `AttributeError: 'Api' object has no attribute 'estadillo_validar'`

- [ ] **Step 3: Write minimal implementation**

Añadir a la clase `Api` en `app_webview.py`, siguiendo el patrón de `cloud_upload` (devolver de inmediato, trabajar en un thread daemon, empujar eventos):

```python
    def estadillo_validar(self, rutas: list[str]) -> dict:
        """Valida los estadillos elegidos y devuelve lo que se ha entendido.

        Síncrono a propósito: el operario tiene que ver el resultado antes de
        que se suba nada.
        """
        res = estadillo_mod.validar_para_subida(rutas)
        return {k: v for k, v in res.items() if k != "vuelos"}

    def estadillo_subir(self, folder: str, rutas: list[str]) -> dict:
        """Sube los estadillos a la ubicación canónica del bucket.

        Acción propia: no depende de haber organizado ni de haber subido la
        jornada, así que la ruta canónica es la misma en modo local y en RAW.
        """
        validacion = estadillo_mod.validar_para_subida(rutas)
        if not validacion["ok"]:
            return {"started": False, "reason": validacion["error"]}

        def worker():
            self._subir_estadillo_worker(folder, rutas, validacion)

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True, "reason": None}

    def _subir_estadillo_worker(self, folder: str, rutas: list[str], validacion: dict):
        from datetime import datetime, timezone

        self._push_cloud({"kind": "start", "scope": "estadillo"})
        try:
            locales = []
            for i, ruta in enumerate(rutas, start=1):
                locales.append(
                    {
                        "orden": i,
                        "ruta": ruta,
                        "nombre_original": os.path.basename(ruta),
                        "md5_b64": cloud_upload._file_md5_b64(ruta),
                        "bytes": os.path.getsize(ruta),
                        "ext": os.path.splitext(ruta)[1],
                    }
                )

            plan = estadillo_canonico.plan_subida(
                planta=folder,
                ficheros_locales=locales,
                vuelos=validacion["vuelos"],
                validacion=validacion,
                ahora=datetime.now(timezone.utc),
                subido_por=self._cuenta_actual(),
            )

            res = estadillo_canonico.ejecutar_plan(
                plan,
                subir_fichero=self._subir_objeto_fichero,
                subir_json=self._subir_objeto_json,
            )
        except Exception as exc:
            self._push_cloud({"kind": "error", "scope": "estadillo", "error": str(exc)})
            return

        if not res["ok"]:
            self._push_cloud({"kind": "error", "scope": "estadillo", "error": res["error"]})
            return

        self._push_cloud(
            {
                "kind": "done",
                "scope": "estadillo",
                "ruta_manifest": res["ruta_manifest"],
                "vuelos_detectados": validacion["vuelos_detectados"],
            }
        )
```

Notas de integración para quien implemente:

- Los imports (`estadillo_mod`, `estadillo_canonico`, `cloud_upload`, `os`, `threading`) ya existen o siguen el patrón del módulo; `estadillo_mod` es el alias con el que `app_webview.py` ya importa `atom_core.estadillo` (ver su uso en `app_webview.py:716-720`).
- `self._cuenta_actual()` y `self._subir_objeto_fichero` / `self._subir_objeto_json` son los adaptadores mínimos a lo que ya hay: la cuenta la conoce el flujo de `cloud_login` (`app_webview.py:454`), y la subida de un objeto suelto se hace con `upload_file()` (`atom_core/cloud_upload.py:710`). Si no existen con ese nombre, crearlos como métodos privados finos, sin lógica: solo el puente al cliente GCS.
- El `folder` es el nombre de carpeta/planta que ya se usa hoy para el destino (`_destino()`, `app_webview.py:508-527`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_estadillo.py -q`
Expected: PASS (4 tests)

Y la suite entera, para descartar regresión:

Run: `python -m pytest tests/ -q --ignore=tests/test_dark_theme.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app_webview.py tests/test_api_estadillo.py
git commit -m "feat: acciones de validar y subir estadillo a la ubicacion canonica"
```

---

### Task 8: Notificar la ruta y matar el camino duplicado

La notificación pasa a colgar de la subida del estadillo, y se elimina la del final del organizado. Es imprescindible que muera **precisamente porque el modo local sigue vivo**: si quedaran las dos, cada jornada organizada en local produciría doble ingesta.

**Files:**
- Modify: `atom_core/run_reporter.py:298-320` (añadir el campo al body de `estadillo()`)
- Modify: `app_webview.py:691-693` (quitar la llamada) y `app_webview.py:697-724` (eliminar `_notificar_estadillo`)
- Test: `tests/test_run_reporter_estadillo.py`

**Interfaces:**
- Consumes: `res["ruta_manifest"]` de `ejecutar_plan` (Task 6).
- Produces: `RunReporter.estadillo(self, vuelos: list[dict], planta_id=None, inspeccion_id=None, ruta_manifest: str | None = None) -> dict | None`. El body pasa a incluir `ruta_manifest`. Campo **aditivo**: la Suite de hoy lo ignora, así que este cambio no la rompe.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_run_reporter_estadillo.py`:

```python
from atom_core import run_reporter


def test_el_body_incluye_la_ruta_del_manifest(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None):
        capturado["metodo"] = metodo
        capturado["ruta"] = ruta
        capturado["cuerpo"] = cuerpo
        return {"ok": True}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep.estadillo(
        [{"pb": "1"}],
        planta_id=None,
        inspeccion_id=None,
        ruta_manifest="X/PREPARACION/ESTADILLOS/2026-08-17T034501Z/manifest.json",
    )

    assert capturado["ruta"] == "/api/organizer/estadillo"
    assert capturado["cuerpo"]["ruta_manifest"] == (
        "X/PREPARACION/ESTADILLOS/2026-08-17T034501Z/manifest.json"
    )
    assert capturado["cuerpo"]["vuelos"] == [{"pb": "1"}]


def test_sin_ruta_el_body_sigue_siendo_valido(monkeypatch):
    capturado = {}

    def fake_peticion(self, metodo, ruta, cuerpo=None):
        capturado["cuerpo"] = cuerpo
        return {"ok": True}

    monkeypatch.setattr(run_reporter.RunReporter, "_peticion", fake_peticion)

    rep = run_reporter.RunReporter.__new__(run_reporter.RunReporter)
    rep.estadillo([{"pb": "1"}])

    assert capturado["cuerpo"]["ruta_manifest"] is None
```

El helper real es `_peticion(metodo, ruta, cuerpo)` (verificado en `atom_core/run_reporter.py:317`). Ojo: `estadillo()` es **fail-open** —envuelve todo en `try/except` y devuelve `None`— así que un assert que falle dentro del fake se tragaría; por eso el fake solo captura y los asserts van fuera. También devuelve `None` de inmediato si `vuelos` está vacío.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_reporter_estadillo.py -q`
Expected: FAIL con `KeyError: 'ruta_manifest'`

- [ ] **Step 3: Write minimal implementation**

En `atom_core/run_reporter.py`, en `estadillo()` (`:298`), añadir el parámetro y la clave nueva al cuerpo. **Conservar el docstring, el `try/except` fail-open y la guarda de `vuelos` vacío tal cual** — el cambio es solo el parámetro y la clave:

```python
    def estadillo(self, vuelos: list[dict], planta_id=None, inspeccion_id=None,
                  ruta_manifest: str | None = None) -> dict | None:
        # ...docstring actual, sin tocar...
        try:
            if not vuelos:
                return None
            cuerpo = {
                "planta_id": planta_id,
                "inspeccion_id": inspeccion_id,
                "vuelos": vuelos,
                "ruta_manifest": ruta_manifest,
            }
            return self._peticion("POST", "/api/organizer/estadillo", cuerpo)
        except Exception as exc:  # noqa: BLE001 - fail-open
            _log.debug("run_reporter.estadillo: excepcion inesperada (%s)", exc)
            return None
```

En `app_webview.py`, eliminar la llamada de `:691-693` y borrar el método `_notificar_estadillo` (`:697-724`) por completo, incluido su docstring, que ya no describe la realidad: el estadillo ya no está en scope solo al organizar.

En el worker de la Task 7, notificar tras subir con éxito, añadiendo antes del `_push_cloud` de `done`:

```python
            reporter = self._reporter_actual()
            if reporter is not None:
                reporter.estadillo(
                    validacion["vuelos"],
                    ruta_manifest=res["ruta_manifest"],
                )
```

`self._reporter_actual()` devuelve el `RunReporter` si hay sesión, o `None`. Sin login no hay notificación —igual que hoy— pero el crudo **ya está en el bucket**, así que el estadillo es re-ingestable, que es la mejora real frente al fail-open actual.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_run_reporter_estadillo.py -q`
Expected: PASS (2 tests)

Verificar que el camino viejo ya no existe:

Run: `grep -n "_notificar_estadillo" app_webview.py`
Expected: sin resultados.

Run: `python -m pytest tests/ -q --ignore=tests/test_dark_theme.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add atom_core/run_reporter.py app_webview.py tests/test_run_reporter_estadillo.py
git commit -m "feat: notificar la ruta del manifest y retirar el aviso del organizado"
```

---

### Task 9: Preview de validación en el webui

El operario elige los ficheros, ve **qué se ha entendido**, y solo entonces sube. Si no carga, error visible: lo contrario del fail-open silencioso de hoy.

**Files:**
- Modify: `webui/src/EstadilloField.jsx` (componente en `:13`, props `{ value, onChange, disabled }`)
- Modify: `webui/src/App.jsx` (estado `estadillos` en `:368`, render de `EstadilloField` en `:432`)
- Test: `webui/src/test/estadilloSubida.test.jsx`

**Interfaces:**
- Consumes: `pywebview.api.estadillo_validar(rutas)` y `pywebview.api.estadillo_subir(folder, rutas)` (Task 7).
- Produces: en `EstadilloField`, dos props nuevas: `onValidar(rutas) -> Promise<dict>` y `onSubir(rutas) -> Promise<dict>`. `EstadilloField` sigue siendo controlado y sigue devolviendo el array completo por `onChange` — el orden de la lista **es** el orden de prioridad.

- [ ] **Step 1: Write the failing test**

Crear `webui/src/test/estadilloSubida.test.jsx`. Seguir el estilo de los tests existentes en `webui/src/test/` (leerlos antes para copiar imports de `@testing-library/react` y el patrón de render que ya use el repo):

```jsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import EstadilloField from '../EstadilloField'

describe('subida de estadillo', () => {
  it('muestra los vuelos detectados tras validar', async () => {
    const onValidar = vi.fn().mockResolvedValue({
      ok: true, error: null, vuelos_detectados: 34, filas_con_problemas: 0,
    })

    render(
      <EstadilloField
        value={['/tmp/e.xlsx']}
        onChange={() => {}}
        disabled={false}
        onValidar={onValidar}
        onSubir={vi.fn()}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: /comprobar/i }))

    await waitFor(() => expect(screen.getByText(/34/)).toBeInTheDocument())
  })

  it('muestra el error y no permite subir si no carga', async () => {
    const onValidar = vi.fn().mockResolvedValue({
      ok: false, error: 'Falta la columna PB', vuelos_detectados: 0, filas_con_problemas: 0,
    })
    const onSubir = vi.fn()

    render(
      <EstadilloField
        value={['/tmp/malo.csv']}
        onChange={() => {}}
        disabled={false}
        onValidar={onValidar}
        onSubir={onSubir}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: /comprobar/i }))

    await waitFor(() =>
      expect(screen.getByText(/Falta la columna PB/)).toBeInTheDocument()
    )
    expect(screen.getByRole('button', { name: /subir/i })).toBeDisabled()
    expect(onSubir).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webui && npx vitest run src/test/estadilloSubida.test.jsx`
Expected: FAIL — no existe el botón "Comprobar" en `EstadilloField`.

- [ ] **Step 3: Write minimal implementation**

En `EstadilloField.jsx`, aceptar las props nuevas y añadir dos botones y el resumen. Mantener el componente controlado y no tocar la reordenación que ya funciona:

```jsx
export default function EstadilloField({ value, onChange, disabled, onValidar, onSubir }) {
  const [check, setCheck] = useState(null)
  const [busy, setBusy] = useState(false)

  async function handleComprobar() {
    setBusy(true)
    try {
      setCheck(await onValidar(value))
    } finally {
      setBusy(false)
    }
  }

  async function handleSubir() {
    setBusy(true)
    try {
      await onSubir(value)
    } finally {
      setBusy(false)
    }
  }

  // ...resto del componente tal cual...

  return (
    <>
      {/* ...file-picker y lista reordenable existentes... */}

      <button type="button" onClick={handleComprobar} disabled={disabled || busy || !value.length}>
        Comprobar
      </button>

      <button type="button" onClick={handleSubir} disabled={disabled || busy || !check?.ok}>
        Subir al bucket
      </button>

      {check?.ok && (
        <p>
          {check.vuelos_detectados} vuelos detectados
          {check.filas_con_problemas > 0 && ` · ${check.filas_con_problemas} filas con problemas`}
        </p>
      )}
      {check && !check.ok && <p role="alert">{check.error}</p>}
    </>
  )
}
```

Al reordenar o cambiar la lista de ficheros, invalidar el check (`setCheck(null)`), para que no se pueda subir con un resumen que ya no corresponde a la selección.

En `App.jsx:432`, pasar las dos props nuevas cableadas al puente:

```jsx
<EstadilloField
  value={estadillos}
  onChange={setEstadillos}
  disabled={running}
  onValidar={(rutas) => window.pywebview.api.estadillo_validar(rutas)}
  onSubir={(rutas) => window.pywebview.api.estadillo_subir(origen, rutas)}
/>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webui && npx vitest run src/test/estadilloSubida.test.jsx`
Expected: PASS (2 tests)

Run: `cd webui && npm run lint && npm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webui/src/EstadilloField.jsx webui/src/App.jsx webui/src/test/estadilloSubida.test.jsx
git commit -m "feat: comprobar y subir el estadillo desde la webui"
```

---

## Verificación final

- [ ] `python -m pytest tests/ -q --ignore=tests/test_dark_theme.py` → PASS
- [ ] `cd webui && npm run lint && npm test` → PASS
- [ ] `grep -n "_notificar_estadillo" app_webview.py` → sin resultados
- [ ] Prueba manual contra el bucket, sobre una planta **de prueba real** (nunca un prefijo inventado): subir dos estadillos con nombres arbitrarios y comprobar que aparecen como `01__<md5>` / `02__<md5>` con `manifest.json` y `estadillo.json`, y que `actual/` queda igual.
- [ ] Comprobar que el `md5Hash` que devuelve GCS para cada objeto coincide con el `md5_b64` del manifest.
- [ ] Limpiar los objetos de prueba del bucket al terminar.
- [ ] ⚠️ La notificación escribe en `aerotoolsDB`, que es **la misma BD en dev y en prod**. La prueba manual de notificación no se hace con ids reales de plantas de producción.
