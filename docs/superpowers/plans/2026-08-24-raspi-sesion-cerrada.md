# Raspi — sesión cerrada, estado de credencial y gating parcial

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la Raspberry Pi sepa de verdad si su credencial sirve, avise en grande cuando no, y siga aceptando trabajo (subir/organizar) encolándolo hasta que vuelva a estar emparejada.

**Architecture:** Un estado de credencial único (`ok` / `sin-credencial` / `sin-conexion`) cacheado en la Pi, refrescado al arrancar, antes de cada acción y cada 6 h — sin polling. La comprobación profunda reutiliza `GoogleAuth.verificar()` (que ya pasa por el broker de la Suite); la barata usa un endpoint nuevo de solo lectura en la Suite. Los trabajos que no se pueden ejecutar sin credencial se persisten en JSON atómico bajo `user_data_dir()` y los drena un worker cuando el estado vuelve a `ok`.

**Tech Stack:** Python 3 + pytest (kiosco), React + Vite + vitest (webui), Node/Express + vitest (Atom-suite). CSS propio del kiosco (sin Tailwind, sin react-icons).

**Spec:** `docs/superpowers/specs/2026-08-24-raspi-sesion-cerrada-design.md`

## Global Constraints

- Dos repos: `/home/rodrigo_saez/atom-organizer-work` (kiosco) y `/home/rodrigo_saez/Atom-suite` (backend). Ninguna task toca los dos a la vez.
- **PROD**: la BD es la misma en dev y prod. Ningún deploy, ni merge, ni cambio en `/home/atom/**` sin OK explícito de Rodrigo en ese momento. Las tasks de este plan trabajan solo en el checkout de dev.
- **No se modifica ningún guard de auth existente.** Prohibido tocar `requireOrganizerIdentity`, `requireGoogleIdToken`, `requireIngestOrganizer`, `guardConsultaRuns`, `requireApiToken` o `/api/organizer/lanzar`. El único cambio de backend permitido es añadir una ruta nueva.
- CSS del kiosco: **nunca `px`**, solo `rem`/`vh`/`vw`. Variables de `webui/src/index.css:12-42`. Iconos: SVG inline al estilo de `webui/src/apps/registry.js`. Pantalla objetivo 480×320 landscape.
- Los subagentes **NO commitean en `Atom-suite`** salvo que la task lo pida explícitamente; en `atom-organizer-work` sí, un commit por task.
- Autor de commits: `git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "..."` — sin `Co-Authored-By`.
- Docker: solo `dev-fast`, `dev-build` y `docker exec <contenedor>`. **PROHIBIDO** `docker compose build`, `docker build`, `docker run`, `docker prune`.
- Nadie prueba en la Raspberry Pi: el túnel SSH inverso lo abre el portátil de Rodrigo y está caído. Las pruebas de hardware las hace Rodrigo.

**Valores literales del estado de credencial** (usar exactamente estas cadenas en Python y en JS):

```
"ok"              credencial válida
"sin-credencial"  401 del backend: no emparejado, token inválido o revocado
"sin-conexion"    timeout / DNS / 5xx: no se alcanzó el backend
```

Latido: `21600` segundos (6 h).

---

### Task 1: Endpoint de estado del dispositivo (Atom-suite)

**ELIMINADA** (ver ledger, ruling en Preflight — 2026-08-24): ningún task del plan
consume `GET /api/organizer/device/status`; todas las comprobaciones usan
`GoogleAuth.verificar()`. No se ejecuta ningún step de esta task.

Repo: `/home/rodrigo_saez/Atom-suite`. Es el ping barato que la Pi usa antes de cada acción, sin quemar un refresh de Google.

**Files:**
- Modify: `server.js` (insertar justo después del bloque `app.get('/api/organizer/inspecciones', ...)`, que termina en la línea 1969)

**Interfaces:**
- Consumes: `requireOrganizerIdentity` (ya existe, `server.js:1913-1948`), que deja `req.googleUsuario` (con `esAtom`) y `req.googleEmail`.
- Produces: `GET /api/organizer/device/status` → `200 {ok:true, email, esAtom}` | `401 {error:'device-no-emparejado'}` | `403 {error:'usuario-no-registrado'}`.

- [ ] **Step 1: Leer el contexto antes de editar**

Run: `sed -n '1913,1972p' /home/rodrigo_saez/Atom-suite/server.js`

Comprueba que `requireOrganizerIdentity` sigue asignando `req.googleUsuario` y `req.googleEmail`, y localiza el cierre de la ruta `/api/organizer/inspecciones`.

- [ ] **Step 2: Añadir la ruta**

Inserta inmediatamente después del cierre de `app.get('/api/organizer/inspecciones', ...)`:

```js
// Ping barato de la Raspberry Pi del Organizer: ¿mi device_token sigue vivo?
// La Pi lo llama antes de cada acción. NO refresca contra Google (eso lo hace
// POST /api/organizer/token) — se apoya en que aquel marca `revocado = true`
// ante invalid_grant, así que una credencial muerta acaba saliendo aquí como 401.
// Solo lectura: no muta nada y no toca ningún guard existente.
app.get('/api/organizer/device/status', requireOrganizerIdentity, (req, res) => {
  res.json({
    ok: true,
    email: req.googleEmail || null,
    esAtom: req.googleUsuario?.esAtom === true,
  });
});
```

- [ ] **Step 3: Comprobar que el fichero sigue siendo válido**

Run: `cd /home/rodrigo_saez/Atom-suite && node --check server.js`
Expected: sin salida (exit 0).

- [ ] **Step 4: Desplegar solo en dev y verificar los dos caminos de fallo**

Run: `cd /home/rodrigo_saez/Atom-suite && dev-fast back`
Expected: termina OK y el backend responde a `/api/ping`.

Luego:

```bash
docker exec suite-backend-saez sh -c \
  'curl -s -o /dev/null -w "%{http_code}\n" -H "x-organizer-device: token-que-no-existe" http://localhost:3000/api/organizer/device/status'
```
Expected: `401`.

```bash
docker exec suite-backend-saez sh -c \
  'curl -s -H "x-organizer-device: token-que-no-existe" http://localhost:3000/api/organizer/device/status'
```
Expected: JSON con `"error":"device-no-emparejado"`.

Si el puerto interno no es 3000, averígualo con `docker exec suite-backend-saez printenv PORT` y repite. **No pruebes contra producción.**

- [ ] **Step 5: NO commitear**

Deja el cambio sin commitear en el checkout de dev y repórtalo. El commit y cualquier subida a producción los decide Rodrigo.

---

### Task 2: Módulo de estado de credencial (kiosco)

Repo: `/home/rodrigo_saez/atom-organizer-work`. Lógica pura y testable: clasificar un resultado de comprobación y decidir cuándo toca volver a comprobar. Sin red, sin hilos, sin tocar `Api` todavía.

**Files:**
- Create: `atom_core/credencial.py`
- Test: `tests/test_credencial.py`

**Interfaces:**
- Consumes: nada del repo (módulo hoja).
- Produces:
  - Constantes `ESTADO_OK = "ok"`, `ESTADO_SIN_CREDENCIAL = "sin-credencial"`, `ESTADO_SIN_CONEXION = "sin-conexion"`, `LATIDO_SEGUNDOS = 21600`.
  - `clasificar(valida: bool, mensaje: str, *, hubo_red: bool) -> str`
  - `class EstadoCredencial` con `__init__(self, *, latido: float = LATIDO_SEGUNDOS, reloj=time.time)`, `registrar(estado: str, mensaje: str = "") -> None`, `actual() -> dict`, `necesita_comprobar() -> bool`, `invalidar(mensaje: str = "") -> None`.
  - `actual()` devuelve `{"estado": str, "mensaje": str, "comprobado_en": float | None}`.

- [x] **Step 1: Escribir el test que falla**

Crea `tests/test_credencial.py`:

```python
import pytest

from atom_core.credencial import (
    ESTADO_OK,
    ESTADO_SIN_CREDENCIAL,
    ESTADO_SIN_CONEXION,
    EstadoCredencial,
    clasificar,
)


def test_clasificar_valida_es_ok():
    assert clasificar(True, "Sesión válida.", hubo_red=True) == ESTADO_OK


def test_clasificar_rechazo_del_backend_es_sin_credencial():
    assert clasificar(False, "Dispositivo no autorizado", hubo_red=True) == ESTADO_SIN_CREDENCIAL


def test_clasificar_sin_red_es_sin_conexion():
    # Si no se llegó a hablar con el backend no se puede afirmar que la
    # credencial esté mal: eso mandaría al operario a re-emparejar sin motivo.
    assert clasificar(False, "timeout", hubo_red=False) == ESTADO_SIN_CONEXION


def test_estado_arranca_sin_comprobar():
    e = EstadoCredencial(reloj=lambda: 1000.0)
    assert e.actual()["comprobado_en"] is None
    assert e.necesita_comprobar() is True


def test_registrar_guarda_estado_y_momento():
    e = EstadoCredencial(reloj=lambda: 1000.0)
    e.registrar(ESTADO_OK, "Sesión válida.")
    assert e.actual() == {"estado": ESTADO_OK, "mensaje": "Sesión válida.", "comprobado_en": 1000.0}
    assert e.necesita_comprobar() is False


def test_necesita_comprobar_tras_el_latido():
    ahora = {"t": 1000.0}
    e = EstadoCredencial(latido=100.0, reloj=lambda: ahora["t"])
    e.registrar(ESTADO_OK)
    ahora["t"] = 1099.0
    assert e.necesita_comprobar() is False
    ahora["t"] = 1101.0
    assert e.necesita_comprobar() is True


def test_invalidar_fuerza_sin_credencial_y_recomprobacion():
    e = EstadoCredencial(reloj=lambda: 1000.0)
    e.registrar(ESTADO_OK)
    e.invalidar("401 del backend")
    assert e.actual()["estado"] == ESTADO_SIN_CREDENCIAL
    assert e.necesita_comprobar() is True


def test_sin_conexion_no_pisa_un_ok_previo_como_sin_credencial():
    # Perder la red no debe hacer creer que hay que re-emparejar.
    e = EstadoCredencial(reloj=lambda: 1000.0)
    e.registrar(ESTADO_OK)
    e.registrar(ESTADO_SIN_CONEXION, "sin red")
    assert e.actual()["estado"] == ESTADO_SIN_CONEXION
```

- [x] **Step 2: Ejecutar el test y verificar que falla**

Run: `python -m pytest tests/test_credencial.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atom_core.credencial'`.

- [x] **Step 3: Implementación mínima**

Crea `atom_core/credencial.py`:

```python
"""Estado de la credencial del dispositivo en la Raspberry Pi.

La Pi no tiene sesión de usuario: su identidad es el `device_token` del
pairing por QR, que no caduca. Lo único que la mata es que la Suite lo dé por
revocado (Google devolvió `invalid_grant`) o que nunca se emparejara.

Este módulo es deliberadamente puro: no hace red ni lanza hilos. Solo
clasifica el resultado de una comprobación y recuerda cuándo se hizo, para
que quien sí hace red (`app_webview.Api`) tenga un sitio único donde
preguntar "¿puedo trabajar?".
"""

from __future__ import annotations

import time

ESTADO_OK = "ok"
ESTADO_SIN_CREDENCIAL = "sin-credencial"
ESTADO_SIN_CONEXION = "sin-conexion"

# La Pi normalmente está apagada; encendida días seguidos es la excepción.
# Por eso no hay polling: se comprueba al arrancar, antes de cada acción, y
# como mucho una vez cada 6 h.
LATIDO_SEGUNDOS = 21600


def clasificar(valida: bool, mensaje: str = "", *, hubo_red: bool) -> str:
    """Traduce el resultado de una comprobación a uno de los tres estados.

    `hubo_red` es la distinción que importa: sin haber hablado con el backend
    no se puede afirmar que la credencial esté mal, y mandar al operario a
    re-emparejar por un corte de red es peor que no avisar.
    """
    if valida:
        return ESTADO_OK
    if not hubo_red:
        return ESTADO_SIN_CONEXION
    return ESTADO_SIN_CREDENCIAL


class EstadoCredencial:
    """Caché del último estado conocido, con su momento."""

    def __init__(self, *, latido: float = LATIDO_SEGUNDOS, reloj=time.time) -> None:
        self._latido = float(latido)
        self._reloj = reloj
        self._estado = ESTADO_SIN_CREDENCIAL
        self._mensaje = ""
        self._comprobado_en: float | None = None

    def registrar(self, estado: str, mensaje: str = "") -> None:
        self._estado = estado
        self._mensaje = mensaje
        self._comprobado_en = float(self._reloj())

    def invalidar(self, mensaje: str = "") -> None:
        """Un 401 en una llamada real: se sabe ya, sin esperar al latido."""
        self._estado = ESTADO_SIN_CREDENCIAL
        self._mensaje = mensaje
        self._comprobado_en = None

    def actual(self) -> dict:
        return {
            "estado": self._estado,
            "mensaje": self._mensaje,
            "comprobado_en": self._comprobado_en,
        }

    def necesita_comprobar(self) -> bool:
        if self._comprobado_en is None:
            return True
        return (float(self._reloj()) - self._comprobado_en) > self._latido
```

- [x] **Step 4: Ejecutar el test y verificar que pasa**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/test_credencial.py -v`
Expected: 7 passed.

- [x] **Step 5: No romper el resto**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/ -q`
Expected: mismo resultado que antes de la task (si ya había fallos previos, repórtalos como preexistentes, no los arregles).

- [x] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add atom_core/credencial.py tests/test_credencial.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(pi): estado de credencial con clasificacion y latido"
```

---

### Task 3: Cola de subidas pendientes (kiosco)

Repo: `/home/rodrigo_saez/atom-organizer-work`. Persistencia pura en JSON atómico, calcada del patrón ya usado en `atom_core/lotes.py:88-137`. Sin red, sin hilos.

**Nota de alcance:** solo se encola la SUBIDA. "Organizar" (`Api.run_task('split_images', ...)`, `app_webview.py:1413-1451`) es 100 % local — no consulta credencial ni red — así que no necesita cola: basta con que la UI no lo bloquee.

**Files:**
- Create: `atom_core/cola_subidas.py`
- Test: `tests/test_cola_subidas.py`

**Interfaces:**
- Consumes: `atom_core.google_auth.user_data_dir()` (`google_auth.py:105`) para la ruta base.
- Produces:
  - `NOMBRE_COLA = "cola_subidas.json"`
  - `encolar(folder: str, prefix: str, inspeccion_id: int | None = None, *, ruta: Path | None = None) -> dict`
  - `pendientes(*, ruta: Path | None = None) -> list[dict]`
  - `descartar(job_id: str, *, ruta: Path | None = None) -> bool`
  - `marcar_intento(job_id: str, error: str = "", *, ruta: Path | None = None) -> None`
  - Cada job es `{"id": str, "folder": str, "prefix": str, "inspeccion_id": int | None, "creado_en": float, "intentos": int, "ultimo_error": str}`.

- [x] **Step 1: Escribir el test que falla**

Crea `tests/test_cola_subidas.py`:

```python
from atom_core import cola_subidas


def test_encolar_y_leer(tmp_path):
    ruta = tmp_path / "cola.json"
    job = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", 42, ruta=ruta)
    assert job["folder"] == "/datos/vuelo1"
    assert job["prefix"] == "PLANTA/2026"
    assert job["inspeccion_id"] == 42
    assert job["intentos"] == 0
    assert cola_subidas.pendientes(ruta=ruta) == [job]


def test_cola_vacia_si_no_hay_fichero(tmp_path):
    assert cola_subidas.pendientes(ruta=tmp_path / "no-existe.json") == []


def test_fichero_corrupto_se_trata_como_vacia(tmp_path):
    # Un corte de corriente a media escritura no debe impedir arrancar.
    ruta = tmp_path / "cola.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    assert cola_subidas.pendientes(ruta=ruta) == []


def test_encolar_la_misma_carpeta_no_duplica(tmp_path):
    # El operario pulsa "subir" dos veces sin credencial: es un trabajo, no dos.
    ruta = tmp_path / "cola.json"
    a = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", 42, ruta=ruta)
    b = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", 42, ruta=ruta)
    assert a["id"] == b["id"]
    assert len(cola_subidas.pendientes(ruta=ruta)) == 1


def test_misma_carpeta_distinto_prefijo_son_trabajos_distintos(tmp_path):
    ruta = tmp_path / "cola.json"
    cola_subidas.encolar("/datos/vuelo1", "PLANTA_A/2026", None, ruta=ruta)
    cola_subidas.encolar("/datos/vuelo1", "PLANTA_B/2026", None, ruta=ruta)
    assert len(cola_subidas.pendientes(ruta=ruta)) == 2


def test_descartar(tmp_path):
    ruta = tmp_path / "cola.json"
    job = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", None, ruta=ruta)
    assert cola_subidas.descartar(job["id"], ruta=ruta) is True
    assert cola_subidas.pendientes(ruta=ruta) == []
    assert cola_subidas.descartar(job["id"], ruta=ruta) is False


def test_marcar_intento_suma_y_guarda_el_error(tmp_path):
    ruta = tmp_path / "cola.json"
    job = cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", None, ruta=ruta)
    cola_subidas.marcar_intento(job["id"], "sin red", ruta=ruta)
    cola_subidas.marcar_intento(job["id"], "sin red", ruta=ruta)
    p = cola_subidas.pendientes(ruta=ruta)[0]
    assert p["intentos"] == 2
    assert p["ultimo_error"] == "sin red"


def test_orden_de_llegada(tmp_path):
    ruta = tmp_path / "cola.json"
    cola_subidas.encolar("/a", "P/2026", None, ruta=ruta)
    cola_subidas.encolar("/b", "P/2026", None, ruta=ruta)
    assert [j["folder"] for j in cola_subidas.pendientes(ruta=ruta)] == ["/a", "/b"]
```

- [x] **Step 2: Ejecutar el test y verificar que falla**

Run: `python -m pytest tests/test_cola_subidas.py -v`
Expected: FAIL — `ImportError: cannot import name 'cola_subidas'`.

- [x] **Step 3: Implementación mínima**

Crea `atom_core/cola_subidas.py`:

```python
"""Subidas que se aceptaron sin credencial y quedan a la espera.

La Raspberry Pi está en el campo: si el dispositivo aparece revocado, decirle
al operario "no se puede subir" es perder el trabajo del día. Se acepta el
encargo, se deja anotado en disco, y se sube cuando vuelva a haber credencial.

No guarda los ficheros: guarda QUÉ carpeta subir y a dónde. La idempotencia
real (no re-subir lo ya subido) ya la resuelve `cloud_upload.Manifest` y el
estado de lotes de `atom_core/lotes.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

NOMBRE_COLA = "cola_subidas.json"


def _ruta_cola() -> Path:
    from atom_core.google_auth import user_data_dir
    return user_data_dir() / NOMBRE_COLA


def _id_job(folder: str, prefix: str) -> str:
    """Un trabajo se identifica por carpeta resuelta + destino: pulsar 'subir'
    dos veces sobre lo mismo es un trabajo, no dos."""
    clave = f"{Path(folder).resolve()}|{prefix}"
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()[:16]


def _leer(ruta: Path) -> list[dict]:
    try:
        crudo = ruta.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        datos = json.loads(crudo)
    except ValueError:
        # Escritura a medias o disco lleno: se trata como cola vacía en vez de
        # impedir que arranque la app. Se reescribirá limpia al siguiente encolar.
        return []
    if not isinstance(datos, list):
        return []
    return [j for j in datos if isinstance(j, dict) and "id" in j]


def _escribir(ruta: Path, jobs: list[dict]) -> None:
    """Escritura atómica: un corte no debe dejar la cola truncada."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ruta)


def encolar(folder: str, prefix: str, inspeccion_id: int | None = None,
            *, ruta: Path | None = None) -> dict:
    ruta = ruta or _ruta_cola()
    jobs = _leer(ruta)
    job_id = _id_job(folder, prefix)
    for j in jobs:
        if j["id"] == job_id:
            return j
    job = {
        "id": job_id,
        "folder": str(folder),
        "prefix": str(prefix),
        "inspeccion_id": inspeccion_id,
        "creado_en": time.time(),
        "intentos": 0,
        "ultimo_error": "",
    }
    jobs.append(job)
    _escribir(ruta, jobs)
    return job


def pendientes(*, ruta: Path | None = None) -> list[dict]:
    return _leer(ruta or _ruta_cola())


def descartar(job_id: str, *, ruta: Path | None = None) -> bool:
    ruta = ruta or _ruta_cola()
    jobs = _leer(ruta)
    quedan = [j for j in jobs if j["id"] != job_id]
    if len(quedan) == len(jobs):
        return False
    _escribir(ruta, quedan)
    return True


def marcar_intento(job_id: str, error: str = "", *, ruta: Path | None = None) -> None:
    ruta = ruta or _ruta_cola()
    jobs = _leer(ruta)
    for j in jobs:
        if j["id"] == job_id:
            j["intentos"] = int(j.get("intentos", 0)) + 1
            j["ultimo_error"] = error
            _escribir(ruta, jobs)
            return
```

- [x] **Step 4: Ejecutar el test y verificar que pasa**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/test_cola_subidas.py -v`
Expected: 8 passed.

- [x] **Step 5: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add atom_core/cola_subidas.py tests/test_cola_subidas.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(pi): cola en disco de subidas pendientes"
```

---

### Task 4: Cablear estado y cola en `Api` (kiosco)

Repo: `/home/rodrigo_saez/atom-organizer-work`. Aquí se une todo: `cloud_status` deja de mentir, la comprobación se hace de verdad, y `cloud_upload` encola en vez de rechazar.

**Files:**
- Modify: `app_webview.py` (clase `Api`: `__init__`, `cloud_status` en :592, `cloud_verify` en :618, `cloud_upload` en :911 — el bloque de credencial de :945-950)
- Modify: `atom_core/webserver.py` (allowlist `METODOS_EXPUESTOS`, :32)
- Test: `tests/test_app_webview_credencial.py`

**Interfaces:**
- Consumes: `atom_core.credencial` (Task 2) y `atom_core.cola_subidas` (Task 3). De `GoogleAuth`: `verificar() -> tuple[bool, str]` (`google_auth.py:340`), `is_logged_in() -> bool` (:309), `AuthError` (:91).
- Produces, en la clase `Api`:
  - `self._credencial: EstadoCredencial`
  - `cloud_status()` gana las claves `estado: str`, `estado_mensaje: str`, `pendientes: int`.
  - `cloud_comprobar(profunda: bool = False) -> dict` — comprobación síncrona; devuelve `{"estado", "mensaje"}`.
  - `cloud_pendientes() -> dict` — `{"pendientes": [job, ...]}`.
  - `cloud_drenar() -> dict` — `{"lanzados": int}`.

- [x] **Step 1: Escribir el test que falla**

Crea `tests/test_app_webview_credencial.py`. Mira antes `tests/test_app_webview_broker.py` para copiar cómo se instancia `Api` con dobles en este repo, y adapta el arranque si hace falta:

```python
import pytest

from atom_core.credencial import ESTADO_OK, ESTADO_SIN_CREDENCIAL, ESTADO_SIN_CONEXION
from atom_core.google_auth import AuthError


class AuthFalso:
    def __init__(self, resultado=None, excepcion=None, logueado=True):
        self._resultado = resultado
        self._excepcion = excepcion
        self._logueado = logueado
        self.identity = None
        self.validada_en = None
        self.aviso_store = None
        self.broker_only = True

    def is_logged_in(self):
        return self._logueado

    def verificar(self):
        if self._excepcion:
            raise self._excepcion
        return self._resultado


def _api(monkeypatch, auth):
    from app_webview import Api
    api = Api()
    monkeypatch.setattr(api, "_get_auth", lambda: auth)
    return api


def test_credencial_valida_deja_estado_ok(monkeypatch):
    api = _api(monkeypatch, AuthFalso(resultado=(True, "Sesión válida.")))
    assert api.cloud_comprobar()["estado"] == ESTADO_OK
    assert api.cloud_status()["estado"] == ESTADO_OK


def test_rechazo_del_backend_deja_sin_credencial(monkeypatch):
    api = _api(monkeypatch, AuthFalso(excepcion=AuthError("Este dispositivo ya no está autorizado")))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CREDENCIAL


def test_fallo_de_red_deja_sin_conexion(monkeypatch):
    # OSError es lo que sube desde urllib cuando no hay red.
    api = _api(monkeypatch, AuthFalso(excepcion=OSError("Network is unreachable")))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CONEXION


def test_sin_sesion_local_es_sin_credencial(monkeypatch):
    api = _api(monkeypatch, AuthFalso(logueado=False))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CREDENCIAL


def test_subir_sin_credencial_encola_en_vez_de_rechazar(monkeypatch, tmp_path):
    from atom_core import cola_subidas
    ruta = tmp_path / "cola.json"
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: ruta)

    api = _api(monkeypatch, AuthFalso(logueado=False))
    carpeta = tmp_path / "vuelo1"
    carpeta.mkdir()
    r = api.cloud_upload(str(carpeta), prefix="PLANTA/2026")

    assert r["started"] is False
    assert r["encolado"] is True
    assert len(cola_subidas.pendientes(ruta=ruta)) == 1


def test_status_reporta_cuantas_pendientes_hay(monkeypatch, tmp_path):
    from atom_core import cola_subidas
    ruta = tmp_path / "cola.json"
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: ruta)
    cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", None, ruta=ruta)

    api = _api(monkeypatch, AuthFalso(resultado=(True, "ok")))
    assert api.cloud_status()["pendientes"] == 1
```

- [x] **Step 2: Ejecutar el test y verificar que falla**

Run: `python -m pytest tests/test_app_webview_credencial.py -v`
Expected: FAIL — `AttributeError: 'Api' object has no attribute 'cloud_comprobar'`.

- [x] **Step 3: Implementar**

En `app_webview.py`:

a) Imports, junto a los demás de `atom_core`:

```python
from atom_core import cola_subidas
from atom_core.credencial import (
    ESTADO_OK, ESTADO_SIN_CREDENCIAL, ESTADO_SIN_CONEXION,
    EstadoCredencial, clasificar,
)
```

b) En `Api.__init__`, junto al resto de estado de instancia:

```python
self._credencial = EstadoCredencial()
```

c) Método nuevo `cloud_comprobar`, justo antes de `cloud_verify` (:618):

```python
def cloud_comprobar(self, profunda: bool = False) -> dict:
    """Comprueba de verdad si la credencial sirve, y cachea el resultado.

    Síncrona a propósito: la llaman el arranque y el paso previo a cada
    acción, que necesitan la respuesta antes de seguir. `cloud_verify`
    sigue existiendo para la comprobación manual, que va por evento.

    `profunda` está para el latido de 6 h; hoy ambas rutas usan
    `verificar()`, que ya pasa por el broker de la Suite.
    """
    auth = self._get_auth()
    if auth is None or not auth.is_logged_in():
        # Sin token local no hay nada que preguntar: hay que emparejar.
        self._credencial.registrar(ESTADO_SIN_CREDENCIAL, "No hay dispositivo emparejado.")
        return self._credencial.actual()
    try:
        valida, texto = auth.verificar()
        estado = clasificar(valida, texto, hubo_red=True)
        self._credencial.registrar(estado, texto)
    except AuthError as exc:
        # El backend contestó y dijo que no: revocado o token inválido.
        self._credencial.registrar(ESTADO_SIN_CREDENCIAL, str(exc))
    except OSError as exc:
        # No se llegó a hablar con el backend: no acuses a la credencial.
        self._credencial.registrar(ESTADO_SIN_CONEXION, str(exc))
    return self._credencial.actual()
```

Asegúrate de que `AuthError` está importado en `app_webview.py`; si no lo está, añádelo al import de `atom_core.google_auth`.

d) En `cloud_status` (:592), añade al dict que se devuelve en la rama `configured: True` (y también en la rama sin configurar, con `estado` = `ESTADO_SIN_CREDENCIAL`):

```python
        "estado": self._credencial.actual()["estado"],
        "estado_mensaje": self._credencial.actual()["mensaje"],
        "pendientes": len(cola_subidas.pendientes()),
```

e) En `cloud_upload` (:911), sustituye el rechazo por login de las líneas 945-950 por encolado:

```python
    auth = self._get_auth()
    if auth is None:
        return {"started": False, "reason": cloud_config.missing_client_help()}
    if not auth.is_logged_in():
        # En el campo, decir "no se puede subir" es perder el trabajo del día.
        # Se acepta el encargo y se sube cuando vuelva a haber credencial.
        destino, prefijo_norm, error = self._destino(folder, prefix)
        if error:
            return {"started": False, "reason": error}
        job = cola_subidas.encolar(str(folder), prefijo_norm, inspeccion_id)
        self._credencial.registrar(ESTADO_SIN_CREDENCIAL, "No hay dispositivo emparejado.")
        return {
            "started": False,
            "encolado": True,
            "job": job,
            "reason": "Sin sesión: la subida queda en cola y saldrá al volver a emparejar.",
        }
```

Comprueba la firma real de `self._destino` (`app_webview.py:792`) y ajusta el desempaquetado si no devuelve exactamente `(root, prefix, error)`.

f) Métodos nuevos, junto a `cloud_cancel` (:1401):

```python
def cloud_pendientes(self) -> dict:
    return {"pendientes": cola_subidas.pendientes()}


def cloud_drenar(self) -> dict:
    """Lanza las subidas encoladas. Solo tiene sentido con estado `ok`.

    Va de una en una: `cloud_upload` ya rechaza si hay otra subida en curso,
    y el resto de la cola sigue ahí para el siguiente intento.
    """
    if self._credencial.actual()["estado"] != ESTADO_OK:
        return {"lanzados": 0, "reason": "Sin credencial válida."}
    lanzados = 0
    for job in cola_subidas.pendientes():
        r = self.cloud_upload(job["folder"], prefix=job["prefix"],
                              inspeccion_id=job.get("inspeccion_id"))
        if r.get("started"):
            cola_subidas.descartar(job["id"])
            lanzados += 1
            break
        cola_subidas.marcar_intento(job["id"], str(r.get("reason", "")))
    return {"lanzados": lanzados}
```

g) En `atom_core/webserver.py:32`, añade a `METODOS_EXPUESTOS`: `"cloud_comprobar"`, `"cloud_pendientes"`, `"cloud_drenar"`. **No** los añadas a `METODOS_REMOTOS` (:44): son locales, como `cloud_status`.

- [x] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `python -m pytest tests/test_app_webview_credencial.py -v`
Expected: 6 passed.

- [x] **Step 5: No romper el resto**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/ -q`
Expected: sin regresiones respecto al estado previo. Presta atención a `tests/test_app_webview_broker.py` y `tests/test_app_webview_lote_carpeta.py`, que tocan lo mismo.

- [x] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add app_webview.py atom_core/webserver.py tests/test_app_webview_credencial.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(pi): cloud_status con estado real y subida encolada sin credencial"
```

---

### Task 5: Comprobación al arrancar, antes de cada acción y cada 6 h (kiosco)

Repo: `/home/rodrigo_saez/atom-organizer-work`. Sin polling: la Pi normalmente está apagada.

**Files:**
- Modify: `app_webview.py` (arranque de la app y `run_task`/`cloud_upload`)
- Test: `tests/test_app_webview_credencial.py` (ampliar el de la Task 4)

**Interfaces:**
- Consumes: `Api.cloud_comprobar()`, `EstadoCredencial.necesita_comprobar()` (Tasks 2 y 4).
- Produces: `Api.cloud_asegurar_estado() -> dict` — comprueba solo si toca (primera vez o latido vencido) y devuelve el estado; barata de llamar en caliente.

- [x] **Step 1: Ampliar el test**

Añade a `tests/test_app_webview_credencial.py`:

```python
def test_asegurar_estado_no_recomprueba_dentro_del_latido(monkeypatch):
    llamadas = {"n": 0}

    class AuthContador(AuthFalso):
        def verificar(self):
            llamadas["n"] += 1
            return (True, "ok")

    api = _api(monkeypatch, AuthContador())
    api.cloud_asegurar_estado()
    api.cloud_asegurar_estado()
    api.cloud_asegurar_estado()
    assert llamadas["n"] == 1


def test_asegurar_estado_recomprueba_tras_el_latido(monkeypatch):
    from atom_core.credencial import EstadoCredencial
    llamadas = {"n": 0}
    ahora = {"t": 1000.0}

    class AuthContador(AuthFalso):
        def verificar(self):
            llamadas["n"] += 1
            return (True, "ok")

    api = _api(monkeypatch, AuthContador())
    api._credencial = EstadoCredencial(latido=10.0, reloj=lambda: ahora["t"])
    api.cloud_asegurar_estado()
    ahora["t"] = 1100.0
    api.cloud_asegurar_estado()
    assert llamadas["n"] == 2
```

- [x] **Step 2: Ejecutar y verificar que falla**

Run: `python -m pytest tests/test_app_webview_credencial.py -v -k asegurar`
Expected: FAIL — `AttributeError: 'Api' object has no attribute 'cloud_asegurar_estado'`.

- [x] **Step 3: Implementar**

a) Método nuevo en `Api`, junto a `cloud_comprobar`:

```python
def cloud_asegurar_estado(self) -> dict:
    """Estado de la credencial, recomprobando solo si toca.

    Se llama antes de cada acción. La Pi está normalmente apagada, así que
    en vez de sondear en bucle se comprueba al arrancar y, si sigue
    encendida, como mucho una vez cada 6 h.
    """
    if self._credencial.necesita_comprobar():
        return self.cloud_comprobar()
    return self._credencial.actual()
```

b) Al arrancar: en el punto donde hoy se crea la ventana / se arranca el servidor (`app_webview.py`, alrededor de `webview.create_window(..., js_api=api, ...)` en :2027), lanza la comprobación inicial en un hilo para no retrasar el pintado, y avisa a la UI por el canal que ya existe:

```python
def _comprobar_al_arrancar(api) -> None:
    def worker() -> None:
        estado = api.cloud_comprobar()
        api._push_cloud({"kind": "session",
                         "ok": estado["estado"] == ESTADO_OK,
                         "estado": estado["estado"],
                         "text": estado["mensaje"]})
        if estado["estado"] == ESTADO_OK:
            api.cloud_drenar()
    threading.Thread(target=worker, daemon=True).start()
```

Llámala justo después de construir `Api` y de tener el sink de eventos listo. `_push_cloud` está en `app_webview.py:1407`.

c) Antes de cada acción: al principio de `cloud_upload` y de `run_task`, añade `self.cloud_asegurar_estado()`. En `run_task` es solo para refrescar el indicador — **no bloquees organizar por el estado**: es 100 % local y debe funcionar siempre.

- [x] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/test_app_webview_credencial.py -v`
Expected: 8 passed.

- [x] **Step 5: No romper el resto**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/ -q`
Expected: sin regresiones.

- [x] **Step 6: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add app_webview.py tests/test_app_webview_credencial.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(pi): comprobar credencial al arrancar, por accion y cada 6h"
```

---

### Task 6: Componente `AvisoSesion` (webui)

Repo: `/home/rodrigo_saez/atom-organizer-work`, carpeta `webui/`. El cartel a pantalla completa que hoy no existe.

**Files:**
- Create: `webui/src/AvisoSesion.jsx`
- Create: `webui/src/AvisoSesion.test.jsx`
- Modify: `webui/src/App.css` (estilos, al final del fichero)

**Interfaces:**
- Consumes: variables CSS de `webui/src/index.css:12-42` (`--bg`, `--text`, `--orange`, `--u`, `--fs-*`).
- Produces: `export default function AvisoSesion({ estado, mensaje, pendientes = 0, onEmparejar, onCerrar })`. No renderiza nada si `estado === 'ok'`.

- [x] **Step 1: Escribir el test que falla**

Crea `webui/src/AvisoSesion.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AvisoSesion from './AvisoSesion.jsx'

describe('AvisoSesion', () => {
  it('no pinta nada cuando la credencial es válida', () => {
    const { container } = render(<AvisoSesion estado="ok" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('avisa de sesión cerrada y ofrece emparejar', () => {
    const onEmparejar = vi.fn()
    render(<AvisoSesion estado="sin-credencial" onEmparejar={onEmparejar} />)
    expect(screen.getByText(/SESIÓN CERRADA/i)).toBeTruthy()
    screen.getByRole('button', { name: /emparejar/i }).click()
    expect(onEmparejar).toHaveBeenCalled()
  })

  it('distingue el fallo de red y no ofrece emparejar', () => {
    render(<AvisoSesion estado="sin-conexion" onEmparejar={() => {}} />)
    expect(screen.getByText(/SIN CONEXIÓN/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /emparejar/i })).toBeNull()
  })

  it('deja cerrar el aviso para seguir trabajando', () => {
    const onCerrar = vi.fn()
    render(<AvisoSesion estado="sin-credencial" onCerrar={onCerrar} />)
    screen.getByRole('button', { name: /seguir/i }).click()
    expect(onCerrar).toHaveBeenCalled()
  })

  it('dice cuántas subidas quedan en cola', () => {
    render(<AvisoSesion estado="sin-credencial" pendientes={3} />)
    expect(screen.getByText(/3/)).toBeTruthy()
  })
})
```

Si `@testing-library/react` no está instalado, comprueba primero cómo testean los demás `*.test.jsx` del repo y adapta el test a lo que ya haya, en vez de añadir dependencias.

- [x] **Step 2: Ejecutar y verificar que falla**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- AvisoSesion`
Expected: FAIL — no se resuelve `./AvisoSesion.jsx`.

- [x] **Step 3: Implementar el componente**

Crea `webui/src/AvisoSesion.jsx`:

```jsx
// Cartel a pantalla completa para la Raspberry Pi (480x320). Aparece al
// arrancar y ante cualquier fallo de credencial. Es descartable a propósito:
// organizar es 100% local y subir queda en cola, así que el operario tiene
// que poder seguir trabajando sin dispositivo emparejado.
const TEXTOS = {
  'sin-credencial': {
    titulo: 'SESIÓN CERRADA',
    cuerpo: 'Este dispositivo ya no está autorizado en ATOM Suite.',
    ayuda: 'Vuelve a emparejarlo con el QR.',
  },
  'sin-conexion': {
    titulo: 'SIN CONEXIÓN',
    cuerpo: 'No se ha podido hablar con ATOM Suite.',
    ayuda: 'Comprueba la red. La sesión puede seguir siendo válida.',
  },
}

function IconoAviso() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
  )
}

export default function AvisoSesion({ estado, mensaje, pendientes = 0, onEmparejar, onCerrar }) {
  const texto = TEXTOS[estado]
  if (!texto) return null

  return (
    <div className="aviso-sesion" role="alertdialog" aria-label={texto.titulo}>
      <div className="aviso-sesion-icono"><IconoAviso /></div>
      <h1 className="aviso-sesion-titulo">{texto.titulo}</h1>
      <p className="aviso-sesion-cuerpo">{mensaje || texto.cuerpo}</p>
      <p className="aviso-sesion-ayuda">{texto.ayuda}</p>
      {pendientes > 0 && (
        <p className="aviso-sesion-cola">
          {pendientes} subida{pendientes === 1 ? '' : 's'} en cola, saldrá{pendientes === 1 ? '' : 'n'} al recuperar la sesión.
        </p>
      )}
      <div className="aviso-sesion-acciones">
        {estado === 'sin-credencial' && onEmparejar && (
          <button type="button" className="aviso-sesion-btn aviso-sesion-btn-primario" onClick={onEmparejar}>
            Emparejar con QR
          </button>
        )}
        {onCerrar && (
          <button type="button" className="aviso-sesion-btn" onClick={onCerrar}>
            Seguir sin sesión
          </button>
        )}
      </div>
    </div>
  )
}
```

- [x] **Step 4: Estilos**

Añade al final de `webui/src/App.css`. **Solo rem/vh, ningún `px`:**

```css
/* Aviso de sesión — cubre la pantalla de la Pi (480x320). */
.aviso-sesion {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: calc(var(--u) * 0.5);
  padding: calc(var(--u) * 1.2);
  text-align: center;
  background: var(--bg);
  color: var(--text);
}
.aviso-sesion-icono {
  width: 2.6rem;
  height: 2.6rem;
  color: var(--orange);
}
.aviso-sesion-icono svg { width: 100%; height: 100%; }
.aviso-sesion-titulo {
  margin: 0;
  font-size: var(--fs-xl);
  letter-spacing: 0.08em;
  color: var(--orange);
}
.aviso-sesion-cuerpo { margin: 0; font-size: var(--fs-md); }
.aviso-sesion-ayuda { margin: 0; font-size: var(--fs-sm); opacity: 0.75; }
.aviso-sesion-cola { margin: 0; font-size: var(--fs-sm); opacity: 0.9; }
.aviso-sesion-acciones {
  display: flex;
  gap: calc(var(--u) * 0.6);
  margin-top: calc(var(--u) * 0.4);
}
.aviso-sesion-btn {
  min-height: 2.6rem;
  padding: 0 calc(var(--u) * 0.9);
  border: 0.08rem solid currentColor;
  border-radius: 0.6rem;
  background: transparent;
  color: var(--text);
  font-size: var(--fs-md);
}
.aviso-sesion-btn-primario {
  border-color: var(--orange);
  background: var(--orange);
  color: #0a0a0a;
}
```

Comprueba en `webui/src/index.css:12-42` que `--fs-xl`, `--fs-md`, `--fs-sm` y `--u` existen con esos nombres; si alguno no existe, usa el equivalente real en vez de inventarlo.

- [x] **Step 5: Ejecutar los tests y verificar que pasan**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test -- AvisoSesion`
Expected: 5 passed.

- [x] **Step 6: Comprobar que no quedan `px` nuevos**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && grep -n 'px' src/App.css src/AvisoSesion.jsx`
Expected: solo los hits que ya existían antes de esta task (los de `index.css:69-70` están en otro fichero). Ningún `px` en lo añadido.

- [x] **Step 7: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add webui/src/AvisoSesion.jsx webui/src/AvisoSesion.test.jsx webui/src/App.css
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): aviso de sesion cerrada a pantalla completa"
```

---

### Task 7: Cablear el aviso y el gating en el kiosco (webui)

Repo: `/home/rodrigo_saez/atom-organizer-work`, carpeta `webui/`. El estado ya llega del backend local; aquí se usa.

**Files:**
- Modify: `webui/src/bridge.js` (métodos nuevos, junto a `cloudStatus`/`cloudVerify` en :237-238)
- Modify: `webui/src/App.jsx` (estado del kiosco: efecto de :246-254, suscripción `onCloud` de :476, prop `onRefreshStatus` de :602-604)
- Modify: `webui/src/KioskScreen.jsx` (montaje de `InspeccionSelector` en :697-713)
- Test: `webui/src/AvisoSesion.test.jsx` (ampliar)

**Interfaces:**
- Consumes: `AvisoSesion` (Task 6); de `Api`: `cloud_status()` con `estado`/`estado_mensaje`/`pendientes`, `cloud_comprobar()`, `cloud_pendientes()`, `cloud_drenar()` (Tasks 4 y 5).
- Produces: `api.cloudComprobar()`, `api.cloudPendientes()`, `api.cloudDrenar()` en `bridge.js`; prop nueva `credencialOk: boolean` en `KioskScreen`.

- [x] **Step 1: Añadir los métodos al bridge**

En `webui/src/bridge.js`, junto a `cloudStatus`/`cloudVerify` (:237-238):

```js
  cloudComprobar: () => call('cloud_comprobar'),
  cloudPendientes: () => call('cloud_pendientes'),
  cloudDrenar: () => call('cloud_drenar'),
```

- [x] **Step 2: Estado y aviso en App.jsx**

a) Importa `AvisoSesion` y añade el estado del aviso:

```jsx
import AvisoSesion from './AvisoSesion.jsx'
// ...
const [avisoCerrado, setAvisoCerrado] = useState(false)
```

b) En el efecto de arranque del kiosco (:246-254), tras `api.cloudStatus().then(setKioskCloudStatus)`, no hace falta llamar a `cloudComprobar`: el arranque de la app ya lo lanza (Task 5) y el resultado llega por el evento `atom:cloud`.

c) En la suscripción `onCloud` (:476), cuando llegue `kind === 'session'`, refresca el status para recoger `estado`/`pendientes` y reabre el aviso si el estado empeora:

```jsx
if (detalle?.kind === 'session') {
  api.cloudStatus().then((s) => {
    setKioskCloudStatus(s)
    if (s?.estado && s.estado !== 'ok') setAvisoCerrado(false)
  }).catch(() => {})
}
```

d) Renderiza el aviso dentro del bloque del kiosco, por encima de todo:

```jsx
{kiosco && !avisoCerrado && (
  <AvisoSesion
    estado={kioskCloudStatus?.estado}
    mensaje={kioskCloudStatus?.estado_mensaje}
    pendientes={kioskCloudStatus?.pendientes || 0}
    onEmparejar={() => { setAvisoCerrado(true); /* abrir PairScreen */ }}
    onCerrar={() => setAvisoCerrado(true)}
  />
)}
```

Para `onEmparejar`, usa el mismo camino que ya lleva hoy a `PairScreen` desde el menú de cuenta — localízalo en `App.jsx`/`KioskScreen.jsx` y reutilízalo; no dupliques la lógica de pairing.

- [x] **Step 3: Gating en KioskScreen.jsx**

a) Acepta la prop nueva en el destructuring de cabecera (:38-51): `credencialOk = true`.

b) Pásala desde `App.jsx` donde se monta `KioskScreen`: `credencialOk={(kioskCloudStatus?.estado || 'ok') === 'ok'}`.

c) En el montaje de `InspeccionSelector` (:697-713), sustituye por:

```jsx
{credencialOk ? (
  <InspeccionSelector
    inspecciones={inspecciones}
    onElegir={(prefijo) => {
      const elegida = inspecciones.find((i) => i.prefijo === prefijo) || null
      onSelectInspeccion(elegida)
    }}
    onNueva={() => {}}
    ocupado={busy}
    onActualizar={onActualizarInspecciones}
    soloLista
    fasesControladas={fasesKiosco}
  />
) : (
  <div className="kiosk-sistema-error">
    No se puede elegir planta sin sesión: la lista de inspecciones la sirve ATOM Suite.
    Vuelve a emparejar el dispositivo con el QR.
  </div>
)}
```

d) **No toques** los botones "Organizar" ni "Subir en crudo" (:484-511): siguen habilitados a propósito. Organizar es local y subir se encola.

- [x] **Step 4: Ampliar el test**

Añade a `webui/src/AvisoSesion.test.jsx` un test del gating, renderizando `KioskScreen` con `credencialOk={false}` y comprobando que aparece el texto de "No se puede elegir planta" y que los botones Organizar/Subir en crudo **no** están deshabilitados. Si montar `KioskScreen` entero resulta inviable por sus props, dilo en el informe y deja el gating cubierto solo por la verificación manual del paso 6, en vez de escribir un test que no prueba nada.

- [x] **Step 5: Ejecutar toda la suite del webui**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test`
Expected: sin regresiones.

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm run lint`
Expected: sin errores nuevos.

- [x] **Step 6: Build**

Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm run build`
Expected: build correcto. Si peta por heap, reintenta con `NODE_OPTIONS=--max-old-space-size=5120 npm run build`.

- [x] **Step 7: Commit**

```bash
cd /home/rodrigo_saez/atom-organizer-work
git add webui/src/bridge.js webui/src/App.jsx webui/src/KioskScreen.jsx webui/src/AvisoSesion.test.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(kiosco): mostrar aviso de sesion y bloquear solo elegir planta"
```

---

### Task 8: Entrega — informe y despliegue

No hay automatización de despliegue a la Pi y el túnel SSH está caído: esta task **no despliega nada**, prepara y documenta.

**Files:**
- Modify: `docs/superpowers/plans/2026-08-24-raspi-sesion-cerrada.md` (marcar los checkboxes completados)

- [ ] **Step 1: Repasar el estado del repo**

Run: `git log --oneline -8 && git status -sb`
Expected: un commit por task, nada sin commitear salvo lo que se decida dejar.

- [ ] **Step 2: Suite completa de tests**

Run: `cd /home/rodrigo_saez/atom-organizer-work && python -m pytest tests/ -q`
Run: `cd /home/rodrigo_saez/atom-organizer-work/webui && npm test`
Expected: ambas verdes, o los fallos preexistentes identificados como tales.

- [ ] **Step 3: Confirmar que el cambio de la Suite sigue sin commitear**

Run: `cd /home/rodrigo_saez/Atom-suite && git status -sb -- server.js`
Expected: `server.js` modificado y **sin commitear**. El commit, el merge a `produccion` y el deploy los autoriza Rodrigo, no esta task.

- [ ] **Step 4: Redactar el informe de entrega para Rodrigo**

Incluye, en pocas líneas: qué queda listo, qué falta por probar en hardware, y estos pasos manuales, que **nadie ejecuta sin él**:

```
1. Abrir el túnel inverso a la Pi desde el portátil (hoy: nada escuchando en localhost:2222).
2. Llevar el build:  cd webui && npm run build   →  copiar webui/dist/ a la Pi  →  reiniciar el servidor del kiosco.
3. Backend: commit de server.js en dev/saez, y deploy a producción solo con su OK.
   Sin el endpoint en producción, la Pi cae al camino profundo (verificar()) y sigue funcionando.
4. Probar en la Pi: arrancar sin emparejar (debe salir el cartel), pulsar "Subir en crudo"
   (debe encolar), emparejar con el QR (la cola debe drenarse sola).
```

- [ ] **Step 5: Documentar en el Atlas**

Invoca la skill `documentar-sesion`. Nota canónica del kiosco + entrada en `30_Gestion/Proyectos/ATOM/Diario/2026-08-24.md`.

---

## Notas de diseño que el implementador debe respetar

- **El estado `sin-conexion` nunca manda a re-emparejar.** Un corte de red no es una credencial muerta; confundirlos hace que el operario destruya una sesión buena.
- **Organizar no se bloquea jamás.** `run_task` es local (`app_webview.py:1413-1451`); bloquearlo por falta de sesión sería una regresión inventada.
- **El único gating es elegir planta**, porque la lista de inspecciones la sirve la Suite (`GET /api/organizer/inspecciones`) y sin backend no hay nada que enseñar.
- **La cola no copia ficheros.** Guarda carpeta + destino. Re-subir lo ya subido lo evita `cloud_upload.Manifest` (`cloud_upload.py:553-589`) y el estado de lotes de `atom_core/lotes.py`.
- **Ningún guard de auth existente se toca.** Si una task parece necesitarlo, para y pregunta: es señal de que el diseño está mal, no de que haya que abrir el guard.

## Follow-up fuera de este plan

Organizar sin internet **en la Raspberry Pi** debe usar el geojson / modelo digital de la
inspección para nombrar bien los PBs. Eso exige precargar los datos de la inspección mientras hay
red y cachearlos en la Pi. Es un subsistema aparte: spec y plan propios, después de este.
El Organizer de nube no cambia — ese sí va por internet.
