# Organizer escritorio — un trabajo, tres destinos · Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unificar `OrganizarScreen` y `BucketScreen` en una sola pantalla `Trabajo` con estado compartido y tres destinos (organizar aquí / subir al bucket / subir y organizar en la nube), sin perder funcionalidad y sin que la UI se congele.

**Architecture:** Refactor por **descomposición, no rewrite**. Las dos pantallas actuales se parten en componentes de paso (`PasoCarpeta`, `PasoInspeccion`, `PasoEstadillo`) y de destino (`PanelOrganizar`, `PanelSubida`), que `TrabajoScreen` orquesta con un único estado. En backend, las dos operaciones síncronas que bloquean la UI (`detect_suffixes`, `cloud_prepare`) ganan variante en hilo con eventos por un canal nuevo `atom:analisis`, replicando el patrón ya existente de `run_task`/`cloud_upload`.

**Tech Stack:** Python 3 + pywebview (`app_webview.py`, `atom_core/`), React 18 + Vite (`webui/`), vitest + @testing-library/react, pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-organizer-escritorio-trabajo-design.md`

## Global Constraints

- **Principio de réplica**: NO rewrite. Cuando un paso diga "mueve `App.jsx:A-B` tal cual", se mueve el JSX/lógica **literalmente**, cambiando solo lo imprescindible para que compile (props en vez de closures). Cero cambios de comportamiento no pedidos.
- **Nunca `px`** en CSS/estilos nuevos: `rem`/`vh`/`vw`. Tokens existentes `--u`, `--radio`, `--fs-*` (`webui/src/index.css:31-58`).
- **Estética**: fondo `#0a0a0a`, naranja `#EE753A`, glass, Space Grotesk. Iconos SVG inline, **nunca emoji**.
- **Clases CSS existentes**: reutilizar `card`, `card-title`, `field`, `field-label`, `field-row`, `field-hint` (+ `hint-ok`/`hint-warn`), `glass-input`, `btn-ghost`, `btn-run`, `check`, `adv-toggle`, `adv-panel`, `subida-panel`. No inventar equivalentes.
- **Compatibilidad de bridge**: los métodos Python existentes **no se borran ni cambian de firma** (hay tests que verifican su existencia: `tests/test_cloud_bucket_tab.py:94`). Las variantes asíncronas se **añaden**.
- **DOCKER: prohibido**. Nada de `docker build`, `docker run`, `docker compose build`, `docker prune`.
- **Suite de tests que debe quedar verde**: `cd webui && npx vitest run` (204 tests) y `python -m pytest` (968 passed). Fallo preexistente **aceptado**, no intentar arreglarlo: `tests/test_dji_resiliencia_parallel.py::test_raw_truncado_no_tumba_el_lote`.
- **Commits**: autor fijado en cada commit — `git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "..."`. **Sin `Co-Authored-By`.** No hacer `push` (lo hace el coordinador al final).

---

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `webui/src/FileField.jsx` | **Crear.** `FileField` extraído de `App.jsx:1823-1846` para poder importarlo desde los nuevos módulos. |
| `webui/src/trabajo/PasoCarpeta.jsx` | **Crear.** Elegir carpeta de origen + aviso de carpeta no vacía. Sin conocimiento de destino. |
| `webui/src/trabajo/PasoInspeccion.jsx` | **Crear.** Elegir inspección existente o nueva (extraído de `BucketScreen`). |
| `webui/src/trabajo/PasoEstadillo.jsx` | **Crear.** Estadillo: elegir, validar, omitir, subir. Preserva el guard `estadSubiendo`. |
| `webui/src/trabajo/PanelOrganizar.jsx` | **Crear.** Destino local: carpeta final, sufijos, renombrar, modo avanzado, botón Ejecutar. |
| `webui/src/trabajo/PanelSubida.jsx` | **Crear.** Destino nube: plan, progreso, cancelar, resultado. |
| `webui/src/trabajo/TrabajoScreen.jsx` | **Crear.** Estado único del trabajo + selector de destino. Orquesta los cinco anteriores. |
| `webui/src/HerramientasScreen.jsx` | **Crear.** Agrupa las dos pantallas hoy en `NAV` como `aerotools` y `otros`. |
| `webui/src/App.jsx` | **Modificar.** `NAV` a tres entradas; borrar `OrganizarScreen`/`BucketScreen` al final (Task 13). |
| `app_webview.py` | **Modificar.** Añadir `detect_suffixes_start`, `cloud_prepare_start`, `analisis_cancel` y el canal `atom:analisis`. |
| `atom_core/suffixes.py` | **Modificar.** `detect_suffixes` acepta `on_progress`/`should_stop` opcionales. |
| `atom_core/cloud_upload.py` | **Modificar.** `build_plan` acepta `on_progress`/`should_stop` opcionales. |
| `webui/src/bridge.js` | **Modificar.** Añadir `onAnalisis`, `detectSuffixesStart`, `cloudPrepareStart`, `analisisCancel`; registrar el canal en el multiplexado SSE. |

**Orden**: Tasks 1-4 son cimientos independientes (extracción + backend). Tasks 5-9 son los componentes de paso, disjuntos entre sí. Task 10 los compone, Task 11 cambia la navegación, Task 12 añade el tercer destino, Task 13 limpia y verifica.

---

### Task 1: Extraer `FileField` a módulo propio

`FileField` vive hoy dentro de `App.jsx` sin exportar, y todos los componentes nuevos lo necesitan.

**Files:**
- Create: `webui/src/FileField.jsx`
- Create: `webui/src/FileField.test.jsx`
- Modify: `webui/src/App.jsx:1823-1846` (borrar la definición local), `webui/src/App.jsx:1-20` (añadir el import)

**Interfaces:**
- Produces: `export default function FileField({ label, value, onPick, onType, placeholder })` — mismas props y mismo DOM que hoy.

- [ ] **Step 1: Escribir el test que falla**

Crea `webui/src/FileField.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FileField from './FileField'

describe('FileField', () => {
  it('muestra la etiqueta y el valor actual', () => {
    render(<FileField label="Carpeta origen" value="/datos/vuelo" onPick={() => {}} />)
    expect(screen.getByText('Carpeta origen')).toBeTruthy()
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
  })

  it('llama a onPick al pulsar Elegir', () => {
    const onPick = vi.fn()
    render(<FileField label="Carpeta origen" value="" onPick={onPick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onPick).toHaveBeenCalledTimes(1)
  })

  it('llama a onType al teclear cuando se le pasa onType', () => {
    const onType = vi.fn()
    render(<FileField label="Carpeta origen" value="" onPick={() => {}} onType={onType} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '/otra' } })
    expect(onType).toHaveBeenCalledWith('/otra')
  })
})
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `cd webui && npx vitest run src/FileField.test.jsx`
Expected: FAIL — `Failed to resolve import "./FileField"`.

- [ ] **Step 3: Crear el módulo moviendo el código literal**

Abre `webui/src/App.jsx:1823-1846`, **corta** la función `FileField` completa y pégala en `webui/src/FileField.jsx` cambiando solo la línea de declaración para exportarla por defecto:

```jsx
export default function FileField({ label, value, onPick, onType, placeholder }) {
  // ...cuerpo IDÉNTICO al que había en App.jsx:1823-1846, sin tocar nada...
}
```

No cambies el JSX, ni las clases CSS, ni el comportamiento del `onType` opcional (si hoy el input es readOnly cuando no hay `onType`, sigue siéndolo).

- [ ] **Step 4: Importarlo en `App.jsx`**

Añade junto a los demás imports de `webui/src/App.jsx` (bloque de líneas 1-20):

```js
import FileField from './FileField'
```

Verifica que no queda ninguna definición duplicada: `grep -n "function FileField" webui/src/App.jsx` no debe devolver nada.

- [ ] **Step 5: Ejecutar los tests**

Run: `cd webui && npx vitest run`
Expected: PASS — los 204 previos + los 3 nuevos. Si alguno de `App.test.jsx` falla, es que el corte se llevó algo de más; compara con `git diff`.

- [ ] **Step 6: Commit**

```bash
git add webui/src/FileField.jsx webui/src/FileField.test.jsx webui/src/App.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "refactor(webui): extraer FileField a modulo propio"
```

---

### Task 2: Arreglar el `ReferenceError` al cerrar sesión en `BucketScreen`

`logout()` de `BucketScreen` llama a `setKioskCloudStatus`, que es un `useState` de `App` (`App.jsx:226`) y no está en su scope. Cerrar sesión desde esa pantalla revienta con `ReferenceError`. Bug preexistente; se arregla ahora porque `PasoInspeccion`/`TrabajoScreen` heredarán ese código.

**Files:**
- Modify: `webui/src/App.jsx:1152-1162` (`logout` de `BucketScreen`)
- Create: `webui/src/test/logoutBucket.test.jsx`

**Interfaces:**
- Consumes: nada.
- Produces: `logout()` deja de referenciar estado del padre.

- [ ] **Step 1: Escribir el test que falla**

Crea `webui/src/test/logoutBucket.test.jsx`. Sigue el patrón de mocks de `webui/src/test/estadilloSubida.test.jsx` (mismo `vi.mock('../bridge')`); abre ese fichero y replica su bloque de mock, añadiendo `cloudLogout` y `cloudStatus`:

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ...aquí el mismo vi.mock('../bridge', ...) de estadilloSubida.test.jsx,
// con cloudStatus devolviendo { configured: true, logged_in: true, email: 'a@b.c' }
// y cloudLogout devolviendo { ok: true } ...

import App from '../App'

describe('BucketScreen · cerrar sesión', () => {
  it('cierra sesión sin lanzar ReferenceError', async () => {
    const errores = []
    const onError = (e) => errores.push(e.error ?? e.reason)
    window.addEventListener('error', onError)
    window.addEventListener('unhandledrejection', onError)

    render(<App />)
    fireEvent.click(await screen.findByText(/Subir al bucket/i))
    fireEvent.click(await screen.findByText(/Cerrar sesión/i))

    await waitFor(() => expect(errores).toHaveLength(0))
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onError)
  })
})
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `cd webui && npx vitest run src/test/logoutBucket.test.jsx`
Expected: FAIL — `setKioskCloudStatus is not defined`.

- [ ] **Step 3: Arreglar `logout`**

En `webui/src/App.jsx`, dentro de `logout()` de `BucketScreen` (~línea 1160), **borra** la línea:

```js
api.cloudStatus().then(setKioskCloudStatus).catch(() => setKioskCloudStatus(null))
```

`BucketScreen` ya refresca su propio estado con `refresh()` (`App.jsx:945`); llama a eso en su lugar:

```js
await refresh()
```

El refresco del indicador del kiosco lo sigue haciendo `App` por su cuenta en `App.jsx:263` y `App.jsx:652`; no hace falta duplicarlo aquí.

- [ ] **Step 4: Ejecutar los tests**

Run: `cd webui && npx vitest run`
Expected: PASS, sin regresiones.

- [ ] **Step 5: Commit**

```bash
git add webui/src/App.jsx webui/src/test/logoutBucket.test.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "fix(webui): cerrar sesion en el bucket lanzaba ReferenceError"
```

---

### Task 3: `detect_suffixes` en hilo, con progreso y cancelación

Hoy `Api.detect_suffixes` (`app_webview.py:475`) hace un `os.walk` de hasta 4000 imágenes de forma síncrona y congela la ventana. Se añade una variante en hilo que emite por un canal nuevo `atom:analisis`. **El método síncrono se queda** (lo verifica `tests/test_cloud_bucket_tab.py`).

**Files:**
- Modify: `atom_core/suffixes.py:35-89` (parámetros opcionales `on_progress`, `should_stop`)
- Modify: `app_webview.py` (nuevo `_push_analisis`, `detect_suffixes_start`, `analisis_cancel`; atributos de estado junto a `self._uploading`, ~línea 220)
- Modify: `webui/src/bridge.js` (nuevo `onAnalisis`, `detectSuffixesStart`, `analisisCancel`; canal en el multiplexado SSE de `bridge.js:122`)
- Create: `tests/test_analisis_asincrono.py`

**Interfaces:**
- Produces:
  - Python: `Api.detect_suffixes_start(origen: str) -> dict` devuelve `{"started": True}` o `{"started": False, "reason": str}`.
  - Python: `Api.analisis_cancel() -> dict` devuelve `{"ok": True}`.
  - Eventos `atom:analisis` con `detail`:
    - `{"kind": "scan", "scope": "suffixes", "done": int}` — cada 250 ficheros escaneados.
    - `{"kind": "done", "scope": "suffixes", "data": {...}}` — `data` es el dict exacto que hoy devuelve `detect_suffixes`.
    - `{"kind": "error", "scope": "suffixes", "text": str}`.
    - `{"kind": "cancelled", "scope": "suffixes"}`.
  - JS: `onAnalisis(handler)` devuelve la función de desuscripción; `api.detectSuffixesStart(origen)`; `api.analisisCancel()`.

- [ ] **Step 1: Escribir los tests que fallan**

Crea `tests/test_analisis_asincrono.py`:

```python
import time
import pytest

from app_webview import Api


class SinkFalso:
    def __init__(self):
        self.eventos = []

    def dispatch(self, event, detail):
        self.eventos.append((event, detail))

    def dispatch_many(self, mensajes):
        for event, detail in mensajes:
            self.dispatch(event, detail)


def _esperar(sink, kind, timeout=5.0):
    """Espera a que llegue un evento atom:analisis con ese kind."""
    fin = time.time() + timeout
    while time.time() < fin:
        for event, detail in list(sink.eventos):
            if event == "atom:analisis" and detail.get("kind") == kind:
                return detail
        time.sleep(0.01)
    raise AssertionError(f"no llego el evento {kind}: {sink.eventos}")


@pytest.fixture
def api_con_sink():
    api = Api()
    sink = SinkFalso()
    api._sink = sink
    return api, sink


def test_detect_suffixes_start_devuelve_al_instante_y_emite_done(tmp_path, api_con_sink):
    api, sink = api_con_sink
    (tmp_path / "DJI_0001_T.JPG").write_bytes(b"x")
    (tmp_path / "DJI_0001_W.JPG").write_bytes(b"x")

    assert api.detect_suffixes_start(str(tmp_path)) == {"started": True}
    done = _esperar(sink, "done")
    assert done["scope"] == "suffixes"
    assert done["data"]["thermal"] == "_T"


def test_detect_suffixes_start_rechaza_dos_analisis_a_la_vez(tmp_path, api_con_sink):
    api, _ = api_con_sink
    api._analizando = True
    r = api.detect_suffixes_start(str(tmp_path))
    assert r["started"] is False
    assert "curso" in r["reason"]


def test_detect_suffixes_start_emite_error_si_la_carpeta_no_existe(tmp_path, api_con_sink):
    api, sink = api_con_sink
    api.detect_suffixes_start(str(tmp_path / "no-existe"))
    done = _esperar(sink, "done")
    assert done["data"]["ok"] is False


def test_analisis_cancel_corta_el_escaneo(tmp_path, api_con_sink):
    api, sink = api_con_sink
    for i in range(600):
        (tmp_path / f"DJI_{i:04d}_T.JPG").write_bytes(b"x")
    api._cancel_analisis = True   # cancelado antes de arrancar: corta en el primer chequeo
    api.detect_suffixes_start(str(tmp_path))
    _esperar(sink, "cancelled")
    assert api._analizando is False


def test_detect_suffixes_sincrono_sigue_existiendo(tmp_path, api_con_sink):
    """El metodo viejo no se toca: hay tests y llamadas que dependen de el."""
    api, _ = api_con_sink
    (tmp_path / "DJI_0001_T.JPG").write_bytes(b"x")
    r = api.detect_suffixes(str(tmp_path))
    assert r["thermal"] == "_T"
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `python -m pytest tests/test_analisis_asincrono.py -v`
Expected: FAIL — `AttributeError: 'Api' object has no attribute 'detect_suffixes_start'`.

- [ ] **Step 3: Añadir los hooks opcionales a `atom_core/suffixes.py`**

Cambia la firma y el bucle de `detect_suffixes` (`atom_core/suffixes.py:35`). Solo se añaden dos parámetros opcionales; el comportamiento por defecto es idéntico:

```python
def detect_suffixes(origen: str, max_scan: int = 4000, *,
                    on_progress=None, should_stop=None) -> dict:
    if not origen or not os.path.isdir(origen):
        return {"ok": False, "error": f"No existe la carpeta: {origen}",
                "thermal": "", "rgb": "", "tokens": {}, "total": 0, "no_suffix": 0}

    tokens: dict[str, int] = {}
    total = 0
    no_suffix = 0
    parado = False
    for root, _dirs, files in os.walk(origen):
        for f in files:
            if should_stop is not None and should_stop():
                parado = True
                break
            if not f.endswith(_IMG_EXT):
                continue
            total += 1
            stem = f.rsplit(".", 1)[0]
            tok = _stem_suffix(stem)
            if tok is None:
                no_suffix += 1
            else:
                tokens[tok] = tokens.get(tok, 0) + 1
            if on_progress is not None and total % 250 == 0:
                on_progress(total)
            if total >= max_scan:
                break
        if parado or total >= max_scan:
            break
    # ...resto IDÉNTICO (cálculo de thermal/rgb y return)...
```

Nota: `parado` sirve para romper también el bucle exterior de `os.walk`; no cambies el `return` final.

- [ ] **Step 4: Añadir el canal y el método en `app_webview.py`**

Junto a `self._uploading` / `self._cancel_upload` (`app_webview.py:220-221`) añade:

```python
self._analizando = False
self._cancel_analisis = False
```

Junto a `_push_cloud` (`app_webview.py:1760`) añade el helper del canal nuevo:

```python
def _push_analisis(self, detail: dict) -> None:
    if not self._sink:
        return
    self._sink.dispatch("atom:analisis", detail)
```

Y junto a `detect_suffixes` (`app_webview.py:475`), **sin tocar el método existente**:

```python
def detect_suffixes_start(self, origen: str) -> dict:
    """Igual que `detect_suffixes` pero en un hilo: el `os.walk` de una
    carpeta de vuelo grande congelaba la ventana. El resultado llega por
    `atom:analisis` (kind `done`), el avance por kind `scan`."""
    if self._analizando:
        return {"started": False, "reason": "Ya hay un análisis en curso."}
    self._analizando = True

    def worker() -> None:
        try:
            from atom_core.suffixes import detect_suffixes
            data = detect_suffixes(
                origen,
                on_progress=lambda n: self._push_analisis(
                    {"kind": "scan", "scope": "suffixes", "done": n}),
                should_stop=lambda: self._cancel_analisis,
            )
            if self._cancel_analisis:
                self._push_analisis({"kind": "cancelled", "scope": "suffixes"})
            else:
                self._push_analisis({"kind": "done", "scope": "suffixes", "data": data})
        except Exception as exc:  # noqa: BLE001 - llega a la UI como error
            self._push_analisis({"kind": "error", "scope": "suffixes", "text": str(exc)})
        finally:
            self._analizando = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True}

def analisis_cancel(self) -> dict:
    """Pide parar el análisis en curso (escaneo de sufijos o plan de subida)."""
    self._cancel_analisis = True
    return {"ok": True}
```

Ojo: `detect_suffixes_start` debe poner `self._cancel_analisis = False` al arrancar **salvo** que ya viniera activado. Para que el test `test_analisis_cancel_corta_el_escaneo` funcione tal como está escrito, **no** lo reinicies dentro de `detect_suffixes_start`; el reinicio lo hace la UI llamando a nada — simplemente el flag se limpia al empezar un análisis nuevo desde `TrabajoScreen` (Task 10) mediante una llamada explícita. Añade por tanto este método público justo debajo:

```python
def analisis_reset(self) -> dict:
    """Limpia la bandera de cancelación antes de un análisis nuevo."""
    self._cancel_analisis = False
    return {"ok": True}
```

- [ ] **Step 5: Ejecutar los tests de Python**

Run: `python -m pytest tests/test_analisis_asincrono.py -v`
Expected: PASS, los 5.

- [ ] **Step 6: Cablear el bridge JS**

En `webui/src/bridge.js`, junto a `onCloud` (`bridge.js:315-343`):

```js
export function onAnalisis(handler) {
  const wrapped = (e) => handler(e.detail)
  window.addEventListener('atom:analisis', wrapped)
  return () => window.removeEventListener('atom:analisis', wrapped)
}
```

En el multiplexado SSE (`bridge.js:122`) añade el canal a la lista:

```js
for (const canal of ['atom:progress', 'atom:update', 'atom:cloud', 'atom:analisis']) {
```

Y en el objeto `api`, junto a `detectSuffixes` (`bridge.js:198`):

```js
  detectSuffixesStart: (origen) => call('detect_suffixes_start', origen),
  analisisCancel: () => call('analisis_cancel'),
  analisisReset: () => call('analisis_reset'),
```

- [ ] **Step 7: Ejecutar la suite completa**

Run: `python -m pytest -q && cd webui && npx vitest run`
Expected: pytest 973 passed + el fallo preexistente aceptado de `test_dji_resiliencia_parallel`; vitest sin regresiones.

- [ ] **Step 8: Commit**

```bash
git add atom_core/suffixes.py app_webview.py webui/src/bridge.js tests/test_analisis_asincrono.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat: detectar sufijos en hilo con progreso y cancelacion"
```

---

### Task 4: `cloud_prepare` en hilo, con progreso y cancelación

`Api.cloud_prepare` (`app_webview.py:1094`) llama a `build_plan`, que hace `sorted(root.rglob("*"))` del árbol entero antes de devolver nada (`atom_core/cloud_upload.py:182-211`). Con una carpeta de vuelo grande la pantalla se queda esperando. Mismo tratamiento que Task 3, sobre el canal `atom:analisis` ya creado.

**Files:**
- Modify: `atom_core/cloud_upload.py:182-211` (`build_plan` acepta `on_progress`/`should_stop`)
- Modify: `app_webview.py` (nuevo `cloud_prepare_start`, junto a `cloud_prepare`)
- Modify: `webui/src/bridge.js` (nuevo `cloudPrepareStart`)
- Modify: `tests/test_analisis_asincrono.py` (añadir los tests del plan)

**Interfaces:**
- Consumes: de Task 3 — `self._analizando`, `self._cancel_analisis`, `_push_analisis`, `api.analisisCancel`, `onAnalisis`.
- Produces:
  - Python: `Api.cloud_prepare_start(folder: str, prefix: str | None = None) -> dict` → `{"started": True}` / `{"started": False, "reason": str}`.
  - Eventos `atom:analisis` con `scope: "plan"`: `{"kind": "scan", "scope": "plan", "done": int}`, `{"kind": "done", "scope": "plan", "data": {...}}` (`data` = dict exacto que hoy devuelve `cloud_prepare`), `{"kind": "error", "scope": "plan", "text": str}`, `{"kind": "cancelled", "scope": "plan"}`.
  - JS: `api.cloudPrepareStart(folder, prefix)`.

- [ ] **Step 1: Escribir los tests que fallan**

Añade al final de `tests/test_analisis_asincrono.py`:

```python
def test_cloud_prepare_start_emite_el_plan_por_evento(tmp_path, api_con_sink, monkeypatch):
    api, sink = api_con_sink
    (tmp_path / "DJI_0001_T.JPG").write_bytes(b"x" * 10)
    monkeypatch.setattr(api, "_destino", lambda f, p: (tmp_path, "EMPRESA--PLANTA--2026--TIPO", None))
    monkeypatch.setattr(api, "_get_auth", lambda: None)

    assert api.cloud_prepare_start(str(tmp_path), "EMPRESA--PLANTA--2026--TIPO") == {"started": True}
    done = _esperar(sink, "done")
    assert done["scope"] == "plan"
    assert done["data"]["ok"] is True
    assert done["data"]["files"] == 1


def test_cloud_prepare_start_rechaza_si_ya_hay_analisis(tmp_path, api_con_sink):
    api, _ = api_con_sink
    api._analizando = True
    r = api.cloud_prepare_start(str(tmp_path), "X")
    assert r["started"] is False


def test_cloud_prepare_sincrono_sigue_existiendo(api_con_sink):
    api, _ = api_con_sink
    assert callable(api.cloud_prepare)
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `python -m pytest tests/test_analisis_asincrono.py -v`
Expected: FAIL — `'Api' object has no attribute 'cloud_prepare_start'`.

- [ ] **Step 3: Hooks opcionales en `build_plan`**

En `atom_core/cloud_upload.py:182`, añade los dos parámetros y el chequeo dentro del bucle. Ojo: hoy hace `sorted(root.rglob("*"))`, que materializa la lista entera antes de iterar; se mantiene el `sorted` (el orden importa para la reanudación) pero se emite progreso durante la construcción de `items`:

```python
def build_plan(root: Path, prefix: str = "", *,
               suffixes: Iterable[str] | None = None,
               on_progress=None, should_stop=None) -> UploadPlan:
    # ...validaciones y `allowed`/`prefix` IDÉNTICOS...
    items: list[UploadItem] = []
    for path in sorted(root.rglob("*")):
        if should_stop is not None and should_stop():
            break
        if not path.is_file():
            continue
        if path.name.lower() in IGNORED_NAMES:
            continue
        if allowed and path.suffix.lower() not in allowed:
            continue
        rel = path.relative_to(root).as_posix()
        remote = f"{prefix}/{rel}" if prefix else rel
        items.append(UploadItem(local=path, remote=remote,
                                size=path.stat().st_size))
        if on_progress is not None and len(items) % 250 == 0:
            on_progress(len(items))

    return UploadPlan(root=root, items=items, prefix=prefix)
```

- [ ] **Step 4: Añadir `cloud_prepare_start`**

En `app_webview.py`, justo debajo de `cloud_prepare` (que **no se toca**). Para no duplicar la lógica del dict de salida, extrae el cuerpo actual de `cloud_prepare` a un helper privado `_construir_plan(folder, prefix, on_progress, should_stop)` que ambos llaman; `cloud_prepare` pasa `None` en los dos hooks y devuelve el dict, y el nuevo método lo ejecuta en hilo:

```python
def cloud_prepare_start(self, folder: str, prefix: str | None = None) -> dict:
    """Igual que `cloud_prepare` pero en un hilo: el `rglob` de la carpeta
    entera dejaba la pantalla esperando. El plan llega por `atom:analisis`."""
    if self._analizando:
        return {"started": False, "reason": "Ya hay un análisis en curso."}
    self._analizando = True

    def worker() -> None:
        try:
            data = self._construir_plan(
                folder, prefix,
                on_progress=lambda n: self._push_analisis(
                    {"kind": "scan", "scope": "plan", "done": n}),
                should_stop=lambda: self._cancel_analisis,
            )
            if self._cancel_analisis:
                self._push_analisis({"kind": "cancelled", "scope": "plan"})
            else:
                self._push_analisis({"kind": "done", "scope": "plan", "data": data})
        except Exception as exc:  # noqa: BLE001 - llega a la UI como error
            self._push_analisis({"kind": "error", "scope": "plan", "text": str(exc)})
        finally:
            self._analizando = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True}
```

Al refactorizar `cloud_prepare` para que delegue en `_construir_plan`, **no cambies el dict que devuelve** (claves `ok, prefix, files, bytes, bucket, existing, pendientes, bytes_pendientes, ya_subidos, inventario, error`) ni la lógica de `_inventario_cacheado`/`_inventario_precalentar`: hay tests que dependen de ese contrato.

- [ ] **Step 5: Ejecutar los tests de Python**

Run: `python -m pytest tests/test_analisis_asincrono.py tests/test_cloud_upload.py -v`
Expected: PASS. Los `test_build_plan_*` existentes (`tests/test_cloud_upload.py:190-224`) deben seguir verdes sin cambios: los parámetros nuevos son opcionales.

- [ ] **Step 6: Cablear el bridge**

En `webui/src/bridge.js`, junto a `cloudPrepare` (`bridge.js:253`):

```js
  cloudPrepareStart: (folder, prefix) => call('cloud_prepare_start', folder, prefix ?? null),
```

- [ ] **Step 7: Suite completa**

Run: `python -m pytest -q && cd webui && npx vitest run`
Expected: sin regresiones (salvo el fallo preexistente aceptado).

- [ ] **Step 8: Commit**

```bash
git add atom_core/cloud_upload.py app_webview.py webui/src/bridge.js tests/test_analisis_asincrono.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat: preparar el plan de subida en hilo con progreso y cancelacion"
```

---

### Task 5: `PasoCarpeta`

Elegir la carpeta de trabajo. Hoy esta lógica está duplicada: `pickOrigen`/`pickDestino` en `OrganizarScreen` (`App.jsx:729-756`) y `pickCarpeta` en `BucketScreen` (`App.jsx:1163`).

**Files:**
- Create: `webui/src/trabajo/PasoCarpeta.jsx`
- Create: `webui/src/trabajo/PasoCarpeta.test.jsx`

**Interfaces:**
- Consumes: `FileField` de Task 1.
- Produces:
```js
export default function PasoCarpeta({
  label,          // string, p.ej. "Carpeta del vuelo"
  value,          // string, ruta actual
  onChange,       // (path: string) => void — se llama tras elegir carpeta
  disabled,       // bool
  avisoNoVacia,   // bool — si true, comprueba con api.folderIsEmpty y avisa
})
```
  Cuando `avisoNoVacia` y la carpeta elegida no está vacía, pinta un `<span className="field-hint hint-warn">` con el número de ficheros. `onChange` se llama **siempre** con la ruta, vacía o no; decidir si eso bloquea es del padre.

- [ ] **Step 1: Escribir el test que falla**

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: { pickFolder: vi.fn(), folderIsEmpty: vi.fn() },
}))
import { api } from '../bridge'
import PasoCarpeta from './PasoCarpeta'

beforeEach(() => vi.clearAllMocks())

describe('PasoCarpeta', () => {
  it('propaga la carpeta elegida', async () => {
    api.pickFolder.mockResolvedValue('/datos/vuelo')
    api.folderIsEmpty.mockResolvedValue({ empty: true })
    const onChange = vi.fn()
    render(<PasoCarpeta label="Carpeta del vuelo" value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('/datos/vuelo'))
  })

  it('no llama a onChange si el operario cancela el diálogo', async () => {
    api.pickFolder.mockResolvedValue(null)
    const onChange = vi.fn()
    render(<PasoCarpeta label="X" value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(api.pickFolder).toHaveBeenCalled())
    expect(onChange).not.toHaveBeenCalled()
  })

  it('avisa si la carpeta no está vacía y se pidió el aviso', async () => {
    api.pickFolder.mockResolvedValue('/datos/llena')
    api.folderIsEmpty.mockResolvedValue({ empty: false, count: 12 })
    render(<PasoCarpeta label="Carpeta final" value="" onChange={() => {}} avisoNoVacia />)
    fireEvent.click(screen.getByRole('button'))
    expect(await screen.findByText(/12/)).toBeTruthy()
  })

  it('sin avisoNoVacia no consulta folderIsEmpty', async () => {
    api.pickFolder.mockResolvedValue('/datos/vuelo')
    render(<PasoCarpeta label="X" value="" onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(api.pickFolder).toHaveBeenCalled())
    expect(api.folderIsEmpty).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/trabajo/PasoCarpeta.test.jsx`
Expected: FAIL — no se resuelve `./PasoCarpeta`.

- [ ] **Step 3: Implementar**

```jsx
import { useState } from 'react'
import { api } from '../bridge'
import FileField from '../FileField'

export default function PasoCarpeta({ label, value, onChange, disabled, avisoNoVacia }) {
  const [noVacia, setNoVacia] = useState(null)

  async function elegir() {
    const path = await api.pickFolder()
    if (!path) return
    onChange(path)
    if (!avisoNoVacia) return
    try {
      const r = await api.folderIsEmpty(path)
      setNoVacia(r?.empty ? null : { count: r?.count ?? 0 })
    } catch {
      setNoVacia(null)
    }
  }

  return (
    <>
      <FileField label={label} value={value} onPick={disabled ? () => {} : elegir} />
      {noVacia && (
        <span className="field-hint hint-warn">
          La carpeta ya tiene {noVacia.count} ficheros. Elige una vacía para no mezclar vuelos.
        </span>
      )}
    </>
  )
}
```

- [ ] **Step 4: Ejecutar los tests**

Run: `cd webui && npx vitest run src/trabajo/PasoCarpeta.test.jsx`
Expected: PASS, los 4.

- [ ] **Step 5: Commit**

```bash
git add webui/src/trabajo/PasoCarpeta.jsx webui/src/trabajo/PasoCarpeta.test.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): componente PasoCarpeta"
```

---

### Task 6: `PasoInspeccion`

Extraer de `BucketScreen` el bloque de elegir inspección (`App.jsx:1430-1480`) y su estado asociado (`catalogo`, `eleccion`, `nueva`, `prefijo` derivado, `cargarInspecciones`).

**Files:**
- Create: `webui/src/trabajo/PasoInspeccion.jsx`
- Create: `webui/src/trabajo/PasoInspeccion.test.jsx`

**Interfaces:**
- Consumes: `InspeccionSelector` (componente ya existente que usa `BucketScreen`; localízalo con `grep -rn "InspeccionSelector" webui/src` e **impórtalo tal cual**, no lo reescribas).
- Produces:
```js
export default function PasoInspeccion({
  ready,        // bool
  prefijo,      // string — prefijo elegido, '' si ninguno
  onChange,     // (prefijo: string, elegida: object|null) => void
  disabled,     // bool
})
```
  `elegida` es el objeto de inspección del catálogo (tiene `.id`), o `null` si el operario tecleó una nueva. El padre necesita `elegida?.id` para `cloudUpload`.

- [ ] **Step 1: Escribir el test que falla**

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: { cloudInspecciones: vi.fn() },
}))
import { api } from '../bridge'
import PasoInspeccion from './PasoInspeccion'

beforeEach(() => vi.clearAllMocks())

describe('PasoInspeccion', () => {
  it('carga el catálogo al montar', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' })
    render(<PasoInspeccion ready prefijo="" onChange={() => {}} />)
    await waitFor(() => expect(api.cloudInspecciones).toHaveBeenCalled())
  })

  it('avisa cuando el catálogo falla', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: false, error: 'sin conexión' })
    render(<PasoInspeccion ready prefijo="" onChange={() => {}} />)
    expect(await screen.findByText(/sin conexión/)).toBeTruthy()
  })

  it('una vez elegida muestra el prefijo y deja cambiarlo', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' })
    const onChange = vi.fn()
    render(<PasoInspeccion ready prefijo="ACME--PLANTA--2026--TERMO" onChange={onChange} />)
    expect(await screen.findByDisplayValue('ACME--PLANTA--2026--TERMO')).toBeTruthy()
    fireEvent.click(screen.getByText(/Cambiar/i))
    expect(onChange).toHaveBeenCalledWith('', null)
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/trabajo/PasoInspeccion.test.jsx`
Expected: FAIL — módulo no encontrado.

- [ ] **Step 3: Implementar moviendo el código existente**

Mueve a `PasoInspeccion.jsx`, **literalmente**: la constante `NUEVA` (`App.jsx:862`), el estado `catalogo`/`eleccion`/`nueva` (`App.jsx:867-869`), el derivado `prefijo` (`App.jsx:943`), `cargarInspecciones()` (`App.jsx:970`), el handler `elegir(valor)` (`App.jsx:1173`) y el JSX del bloque de inspección (`App.jsx:1430-1480`).

Cambios mínimos para que funcione como componente controlado:
- El `prefijo` ahora viene por prop; el estado interno sigue siendo `eleccion`/`nueva`, y cada vez que cambian se llama `onChange(prefijoCalculado, elegidaDelCatalogo)`.
- `elegir('')` (botón "Cambiar") llama `onChange('', null)`.
- El `useEffect` que carga el catálogo depende de `[ready]`, igual que hoy (`App.jsx:978-986`, la parte de `cargarInspecciones`).

**No** te lleves aquí la lógica de estadillo ni la de plan: eso es de Task 7 y 9.

- [ ] **Step 4: Ejecutar los tests**

Run: `cd webui && npx vitest run src/trabajo/PasoInspeccion.test.jsx`
Expected: PASS, los 3.

- [ ] **Step 5: Commit**

```bash
git add webui/src/trabajo/PasoInspeccion.jsx webui/src/trabajo/PasoInspeccion.test.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): componente PasoInspeccion"
```

---

### Task 7: `PasoEstadillo`

Extraer de `BucketScreen` todo lo del estadillo: elegir ficheros, validar, checkbox de omitir, subir esperando el evento. **Es el bloque con más trampas**: preserva íntegro el guard anti doble-click y el puente promesa↔evento.

**Files:**
- Create: `webui/src/trabajo/PasoEstadillo.jsx`
- Create: `webui/src/trabajo/PasoEstadillo.test.jsx`

**Interfaces:**
- Consumes: `EstadilloField` de `webui/src/EstadilloField.jsx` (ya es un módulo propio, se importa tal cual).
- Produces:
```js
export default function PasoEstadillo({
  prefijo,       // string — inspección elegida; sin él no se puede validar ni subir
  disabled,      // bool
  onEstado,      // ({ rutas, listo, subiendo, subir }) => void
})
```
  `onEstado` se llama en cada cambio relevante, con `rutas: string[]` (las rutas de estadillo elegidas, que `PanelOrganizar` necesita), `listo: bool`, `subiendo: bool` y `subir: () => Promise<void>`. `listo` es `estadCheck?.ok === true || omitirEstadillo`. `subir` es la función que el padre debe `await` antes de subir imágenes: resuelve cuando llega el evento `done` del estadillo, rechaza si falla. Si `omitirEstadillo` está marcado o no hay ficheros, `subir()` resuelve de inmediato sin llamar al bridge.

- [ ] **Step 1: Escribir el test que falla**

Los tests existentes de `webui/src/test/estadilloSubida.test.jsx:109-240` cubren este comportamiento a nivel de `BucketScreen`. **Léelos primero** y replica sus casos a nivel de componente:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    estadilloValidar: vi.fn(),
    estadilloSubir: vi.fn(),
    estadilloExistente: vi.fn(),
    pickFile: vi.fn(),
  },
  onCloud: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:cloud', w)
    return () => window.removeEventListener('atom:cloud', w)
  },
}))
import { api } from '../bridge'
import PasoEstadillo from './PasoEstadillo'

function emitirCloud(detail) {
  act(() => { window.dispatchEvent(new CustomEvent('atom:cloud', { detail })) })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.estadilloExistente.mockResolvedValue({ existe: false })
})

describe('PasoEstadillo', () => {
  it('valida el estadillo al elegirlo y reporta listo', async () => {
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 3 })
    const onEstado = vi.fn()
    const { rerender } = render(
      <PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
    // el componente expone su onChange vía EstadilloField; simula la elección
    // usando el mismo camino que estadilloSubida.test.jsx usa para BucketScreen.
    await waitFor(() => expect(onEstado).toHaveBeenCalled())
    rerender(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
  })

  it('marca listo si se omite el estadillo', async () => {
    const onEstado = vi.fn()
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
    fireEvent.click(await screen.findByLabelText(/omitir/i))
    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.listo).toBe(true)
    })
  })

  it('subir() resuelve cuando llega el evento done del estadillo', async () => {
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    api.estadilloSubir.mockResolvedValue({ started: true })
    let estado = null
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    // sin ficheros elegidos, subir() resuelve solo
    await expect(estado.subir()).resolves.toBeUndefined()
  })

  it('subir() rechaza si el backend dice que no arrancó', async () => {
    api.estadilloSubir.mockResolvedValue({ started: false, reason: 'ya hay una subida' })
    let estado = null
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    // este caso requiere ficheros elegidos; usa el mismo helper que
    // estadilloSubida.test.jsx para poblarlos antes de llamar a subir().
  })

  it('un evento error del estadillo rehabilita el paso', async () => {
    let estado = null
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    emitirCloud({ scope: 'estadillo', kind: 'error', error: 'formato inválido' })
    expect(await screen.findByText(/formato inválido/)).toBeTruthy()
    await waitFor(() => expect(estado.subiendo).toBe(false))
  })
})
```

Los dos tests marcados con comentario necesitan poblar `estadRutas`: mira cómo lo hace `webui/src/test/estadilloSubida.test.jsx` (mockea `api.pickFile`/`readEstadilloInfo` y hace click en el botón de `EstadilloField`) y usa exactamente ese camino. **No dejes comentarios "TODO" en el test final: complétalos.**

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/trabajo/PasoEstadillo.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implementar moviendo el código existente**

Mueve a `PasoEstadillo.jsx`, **literalmente**, desde `BucketScreen`:
- estado `estadRutas`, `estadCheck`, `estadComprobando`, `estadSubiendo`, `estadResult`, `omitirEstadillo`, `estadPrevio` (`App.jsx:890-909`)
- refs `estadPromesaRef`, `autoOmitAplicadoRef`, `estadCheckTokenRef` (`App.jsx:914-925`)
- `cambiarEstadRutas` (`App.jsx:927`), `comprobarEstadillo` (`App.jsx:1236`), `subirEstadillo` (`App.jsx:1259`), `subirEstadilloEsperando` (`App.jsx:1304`)
- los `useEffect` de `App.jsx:991-1002` (validar al cambiar ficheros), `1008-1025` (estadillo previo) y `1034-1043` (auto-omitir)
- la rama `d.scope === 'estadillo'` del listener `onCloud` (`App.jsx:1047-1079`) — aquí va en su **propio** `useEffect(() => onCloud(...), [])`, atendiendo solo a `scope === 'estadillo'` e ignorando el resto
- el JSX de `App.jsx:1482-1535`

**Invariante crítico**: `setEstadSubiendo(true)` sigue ejecutándose **síncronamente antes** del `await api.estadilloSubir(...)` (`App.jsx:1268`). El backend `estadillo_subir` no tiene mutex propio; este flag es la única protección contra doble envío. No lo conviertas en `useRef`, no lo muevas dentro del `try`, no lo hagas depender del evento `start`.

Publica el estado hacia arriba con:

```js
useEffect(() => {
  onEstado({
    rutas: estadRutas,
    listo: estadCheck?.ok === true || omitirEstadillo,
    subiendo: estadSubiendo,
    subir: async () => {
      if (omitirEstadillo || estadRutas.length === 0) return
      await subirEstadilloEsperando()
    },
  })
})
```

(sin array de dependencias a propósito: el padre necesita una `subir` que capture el estado más reciente en cada render).

- [ ] **Step 4: Ejecutar los tests**

Run: `cd webui && npx vitest run src/trabajo/PasoEstadillo.test.jsx && npx vitest run src/test/estadilloSubida.test.jsx`
Expected: PASS ambos — los de `BucketScreen` siguen verdes porque `BucketScreen` aún no se ha tocado.

- [ ] **Step 5: Commit**

```bash
git add webui/src/trabajo/PasoEstadillo.jsx webui/src/trabajo/PasoEstadillo.test.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): componente PasoEstadillo con el guard anti doble-envio"
```

---

### Task 8: `PanelOrganizar`

Destino local. Extrae de `OrganizarScreen` todo lo que no es "elegir carpeta de origen": carpeta final, sufijos con autodetección, renombrar, modo avanzado y el botón de ejecutar. Ahora la autodetección usa la variante en hilo de Task 3.

**Files:**
- Create: `webui/src/trabajo/PanelOrganizar.jsx`
- Create: `webui/src/trabajo/PanelOrganizar.test.jsx`

**Interfaces:**
- Consumes: `PasoCarpeta` (Task 5); `api.detectSuffixesStart`/`api.analisisReset`/`onAnalisis` (Task 3); `Field`, `initialState`, `buildParams` de `webui/src/TaskBlock.jsx:62,4,21`; `SPLIT_ADVANCED` de `webui/src/schema.js`.
- Produces:
```js
export default function PanelOrganizar({
  origen,      // string — carpeta de trabajo, la elige TrabajoScreen
  estadillos,  // string[] — rutas del estadillo, las gestiona PasoEstadillo
  ready,       // bool
  running,     // bool
  onRun,       // (task, params, advanced) => void — el `run` de App
})
```

- [ ] **Step 1: Escribir el test que falla**

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    pickFolder: vi.fn(),
    pickFile: vi.fn(),
    folderIsEmpty: vi.fn().mockResolvedValue({ empty: true }),
    detectSuffixesStart: vi.fn().mockResolvedValue({ started: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
  },
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
}))
import { api } from '../bridge'
import PanelOrganizar from './PanelOrganizar'

function emitirAnalisis(detail) {
  act(() => { window.dispatchEvent(new CustomEvent('atom:analisis', { detail })) })
}

beforeEach(() => vi.clearAllMocks())

describe('PanelOrganizar', () => {
  it('arranca la autodetección de sufijos cuando llega el origen', async () => {
    const { rerender } = render(
      <PanelOrganizar origen="" estadillos={[]} ready running={false} onRun={() => {}} />)
    rerender(
      <PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    await waitFor(() => expect(api.detectSuffixesStart).toHaveBeenCalledWith('/datos/vuelo'))
  })

  it('rellena los sufijos con el resultado del evento done', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'done', scope: 'suffixes', data: { ok: true, thermal: '_T', rgb: '_W' } })
    await waitFor(() => expect(screen.getByDisplayValue('_T')).toBeTruthy())
    expect(screen.getByDisplayValue('_W')).toBeTruthy()
  })

  it('muestra el avance mientras escanea y permite cancelar', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'scan', scope: 'suffixes', done: 500 })
    expect(await screen.findByText(/500/)).toBeTruthy()
    fireEvent.click(screen.getByText(/Cancelar/i))
    expect(api.analisisCancel).toHaveBeenCalled()
  })

  it('el botón Ejecutar está desactivado sin carpeta final', () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    expect(screen.getByText(/Ejecutar/i).disabled).toBe(true)
  })

  it('ejecuta split_images con los parámetros correctos', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    const onRun = vi.fn()
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx']} ready running={false} onRun={onRun} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    fireEvent.click(screen.getByText(/Ejecutar/i))
    expect(onRun).toHaveBeenCalledWith(
      'split_images',
      expect.objectContaining({ origen: '/datos/vuelo', destino: '/datos/final', estadillo: ['/e.xlsx'] }),
      expect.anything(),
    )
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/trabajo/PanelOrganizar.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implementar**

Mueve desde `OrganizarScreen` (`App.jsx:714-856`), literalmente: el estado `destino`, `destinoFull`, `rename`, `showAdvanced`, `adv`, `detected`; `setAdvField`, `pickAdv`, `handleRun`; y el JSX de la carpeta final, sufijos, checkbox y panel avanzado. `ADV_FIELDS` (`App.jsx:21`) se mueve también, o se importa desde donde lo dejes — pero no lo dupliques.

Cambios respecto al original, solo estos:
- `origen` y `estadillos` vienen por props; se van del estado local.
- `destinoFull` lo aporta `PasoCarpeta` con `avisoNoVacia`; usa ese componente para la carpeta final en vez de `FileField` suelto.
- La autodetección deja de ser `await api.detectSuffixes(path)` dentro de `pickOrigen`. Ahora:

```js
useEffect(() => {
  if (!origen) return
  setEscaneados(0)
  api.analisisReset()
  api.detectSuffixesStart(origen)
  return onAnalisis((d) => {
    if (d.scope !== 'suffixes') return
    if (d.kind === 'scan') setEscaneados(d.done)
    if (d.kind === 'cancelled') { setEscaneados(0); setDetected(null) }
    if (d.kind === 'error') { setEscaneados(0); setDetected({ ok: false, error: d.text }) }
    if (d.kind === 'done') {
      setEscaneados(0)
      setDetected(d.data)
      if (d.data?.ok) {
        setAdv((s) => ({ ...s, end_thermo_files: d.data.thermal || '', end_rgb_files: d.data.rgb || '' }))
      }
    }
  })
}, [origen])
```

Y mientras `escaneados > 0`, pinta bajo los sufijos:

```jsx
<span className="field-hint">
  Analizando la carpeta… {escaneados} imágenes
  {' '}<button type="button" className="btn-ghost" onClick={() => api.analisisCancel()}>Cancelar</button>
</span>
```

`canRun` mantiene la condición original (`App.jsx:763`) sustituyendo `origen` local por la prop.

- [ ] **Step 4: Ejecutar los tests**

Run: `cd webui && npx vitest run src/trabajo/PanelOrganizar.test.jsx`
Expected: PASS, los 5.

- [ ] **Step 5: Commit**

```bash
git add webui/src/trabajo/PanelOrganizar.jsx webui/src/trabajo/PanelOrganizar.test.jsx webui/src/App.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): panel del destino local con autodeteccion en hilo"
```

---

### Task 9: `PanelSubida`

Destino nube. Extrae de `BucketScreen` la cuenta de Google, el plan de subida, el progreso y el resultado. El plan pasa a pedirse con la variante en hilo de Task 4.

**Files:**
- Create: `webui/src/trabajo/PanelSubida.jsx`
- Create: `webui/src/trabajo/PanelSubida.test.jsx`

**Interfaces:**
- Consumes: `api.cloudPrepareStart`/`onAnalisis` (Task 4); `cloudUploadConfirmando` (hoy a nivel de módulo en `App.jsx:156-171` — **muévelo** a `webui/src/trabajo/cloudUploadConfirmando.js` y expórtalo, para poder testearlo y reutilizarlo).
- Produces:
```js
export default function PanelSubida({
  carpeta,         // string
  prefijo,         // string
  inspeccionId,    // number | undefined
  estadilloListo,  // bool — de PasoEstadillo
  subirEstadillo,  // () => Promise<void> — de PasoEstadillo
  ready,           // bool
  onAntesDeSubir,  // opcional: () => Promise<void>, corre tras el estadillo y antes de las imágenes
  onSubidaOk,      // opcional: (done: object) => void, al recibir el evento done con ok:true
})
```
- Create también: `webui/src/trabajo/cloudUploadConfirmando.js` con el contenido literal de `App.jsx:156-171`, exportado (`export default async function cloudUploadConfirmando(carpeta, prefijo, inspeccionId) {...}`), y `App.jsx` pasa a importarlo en vez de definirlo.

- [ ] **Step 1: Escribir el test que falla**

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    cloudStatus: vi.fn().mockResolvedValue({ configured: true, logged_in: true, email: 'a@b.c' }),
    cloudVerify: vi.fn().mockResolvedValue({ ok: true, text: 'sesión válida' }),
    cloudLogin: vi.fn(), cloudLogout: vi.fn(),
    cloudPrepareStart: vi.fn().mockResolvedValue({ started: true }),
    cloudUpload: vi.fn().mockResolvedValue({ started: true }),
    cloudCancel: vi.fn().mockResolvedValue({ ok: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
  },
  onCloud: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:cloud', w)
    return () => window.removeEventListener('atom:cloud', w)
  },
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
}))
import { api } from '../bridge'
import PanelSubida from './PanelSubida'

const emitir = (canal, detail) =>
  act(() => { window.dispatchEvent(new CustomEvent(canal, { detail })) })

const props = {
  carpeta: '/datos/vuelo', prefijo: 'ACME--P--2026--T', inspeccionId: 7,
  estadilloListo: true, subirEstadillo: vi.fn().mockResolvedValue(undefined), ready: true,
}

beforeEach(() => vi.clearAllMocks())

describe('PanelSubida', () => {
  it('pide el plan en hilo al tener carpeta y prefijo', async () => {
    render(<PanelSubida {...props} />)
    await waitFor(() =>
      expect(api.cloudPrepareStart).toHaveBeenCalledWith('/datos/vuelo', 'ACME--P--2026--T'))
  })

  it('habilita Subir solo cuando el plan llega ok', async () => {
    render(<PanelSubida {...props} />)
    expect(screen.getByText(/Subir al bucket/i).disabled).toBe(true)
    emitir('atom:analisis', { kind: 'done', scope: 'plan', data: { ok: true, files: 30, bytes: 100 } })
    await waitFor(() => expect(screen.getByText(/Subir al bucket/i).disabled).toBe(false))
  })

  it('sube el estadillo antes que las imágenes', async () => {
    render(<PanelSubida {...props} />)
    emitir('atom:analisis', { kind: 'done', scope: 'plan', data: { ok: true, files: 30, bytes: 100 } })
    await waitFor(() => expect(screen.getByText(/Subir al bucket/i).disabled).toBe(false))
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(props.subirEstadillo).toHaveBeenCalled())
    await waitFor(() => expect(api.cloudUpload).toHaveBeenCalled())
  })

  it('no sube imágenes si el estadillo falla', async () => {
    const subirEstadillo = vi.fn().mockRejectedValue(new Error('estadillo inválido'))
    render(<PanelSubida {...props} subirEstadillo={subirEstadillo} />)
    emitir('atom:analisis', { kind: 'done', scope: 'plan', data: { ok: true, files: 30, bytes: 100 } })
    await waitFor(() => expect(screen.getByText(/Subir al bucket/i).disabled).toBe(false))
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    expect(await screen.findByText(/estadillo inválido/)).toBeTruthy()
    expect(api.cloudUpload).not.toHaveBeenCalled()
  })

  it('durante la subida ofrece cancelar', async () => {
    render(<PanelSubida {...props} />)
    emitir('atom:cloud', { kind: 'start', files: 30, bytes: 100, prefix: 'ACME--P--2026--T' })
    emitir('atom:cloud', { kind: 'stats', done: 10, total: 30 })
    fireEvent.click(await screen.findByText(/Cancelar subida/i))
    expect(api.cloudCancel).toHaveBeenCalled()
  })

  it('avisa a onSubidaOk cuando la subida termina bien', async () => {
    const onSubidaOk = vi.fn()
    render(<PanelSubida {...props} onSubidaOk={onSubidaOk} />)
    emitir('atom:cloud', { kind: 'done', ok: true, uploaded: 30, cancelled: false })
    await waitFor(() => expect(onSubidaOk).toHaveBeenCalledWith(expect.objectContaining({ ok: true })))
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/trabajo/PanelSubida.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implementar**

Mueve desde `BucketScreen`, literalmente: el estado `status`, `sesion`, `comprobando`, `busy`, `plan`, `uploading`, `stats`, `desde`, `ahora`, `lines`, `result`, `prepararRef`; `refresh`, `comprobarSesion`, `login`, `logout` (**con el arreglo de Task 2 ya aplicado**), `subir`; el listener `onCloud` **sin** la rama `scope === 'estadillo'` (esa vive ahora en `PasoEstadillo`); el `setInterval` del cronómetro (`App.jsx:1313-1317`); y el JSX de cuenta de Google (`App.jsx:1369-1428`), plan (`1560-1577`), progreso (`1582-1622`), log (`1624-1632`), resultado (`1634-1653`) y botón (`1655`).

Cambios respecto al original, solo estos:
- `carpeta`/`prefijo`/`inspeccionId` vienen por props; fuera del estado local.
- `preparar()` deja de ser `setPlan(await api.cloudPrepare(...))`. Ahora, en un `useEffect` con dependencias `[carpeta, prefijo]`:

```js
useEffect(() => {
  if (!carpeta || !prefijo) return
  setPlan(null)
  api.analisisReset()
  api.cloudPrepareStart(carpeta, prefijo)
  return onAnalisis((d) => {
    if (d.scope !== 'plan') return
    if (d.kind === 'scan') setEscaneados(d.done)
    if (d.kind === 'cancelled') { setEscaneados(0); setPlan(null) }
    if (d.kind === 'error') { setEscaneados(0); setPlan({ ok: false, error: d.text }) }
    if (d.kind === 'done') { setEscaneados(0); setPlan(d.data) }
  })
}, [carpeta, prefijo])
```

- `puedeSubir` conserva la condición original (`App.jsx:1328-1335`), sustituyendo `estadCheck?.ok === true || omitirEstadillo` por la prop `estadilloListo`.
- `subir()` conserva su estructura (`App.jsx:1208-1234`), sustituyendo el bloque del estadillo por `await subirEstadillo()` dentro del mismo `try/catch` (con el mismo mensaje de error: `No se ha subido el estadillo: … No se ha subido ninguna imagen.`), y llamando a `onAntesDeSubir?.()` justo después.
- En el `case 'done'` del listener, tras `setResult(d)`, añade `if (d.ok && !d.cancelled) onSubidaOk?.(d)`.
- `setUploading(true)` sigue siendo **síncrono antes** del `await cloudUploadConfirmando(...)` (`App.jsx:1225-1226`). No lo muevas.

- [ ] **Step 4: Ejecutar los tests**

Run: `cd webui && npx vitest run src/trabajo/PanelSubida.test.jsx`
Expected: PASS, los 6.

- [ ] **Step 5: Commit**

```bash
git add webui/src/trabajo/PanelSubida.jsx webui/src/trabajo/PanelSubida.test.jsx webui/src/trabajo/cloudUploadConfirmando.js webui/src/App.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): panel del destino nube con plan en hilo"
```

---

### Task 10: `TrabajoScreen` — estado único y selector de destino

La pieza que justifica todo el refactor: se elige carpeta, inspección y estadillo **una vez**, y luego el destino.

**Files:**
- Create: `webui/src/trabajo/TrabajoScreen.jsx`
- Create: `webui/src/trabajo/TrabajoScreen.test.jsx`
- Modify: `webui/src/App.css` (estilos del selector de destino, al final del fichero)

**Interfaces:**
- Consumes: `PasoCarpeta` (T5), `PasoInspeccion` (T6), `PasoEstadillo` (T7), `PanelOrganizar` (T8), `PanelSubida` (T9).
- Produces: `export default function TrabajoScreen({ ready, running, onRun })` — mismas props que recibía `OrganizarScreen` (`App.jsx:664`).

**Estado único** (todo en `TrabajoScreen`, nada duplicado en los hijos):

| Estado | Tipo | Origen |
|---|---|---|
| `carpeta` | `string` | `PasoCarpeta` |
| `prefijo` | `string` | `PasoInspeccion` |
| `elegida` | `object \| null` | `PasoInspeccion` (para `elegida?.id`) |
| `estadillo` | `{listo, subiendo, subir}` | `PasoEstadillo` vía `onEstado` |
| `destino` | `'local' \| 'bucket' \| 'nube' \| null` | selector |

**Reglas de la UI:**
- Los tres pasos se muestran siempre, en orden. El selector de destino aparece **solo** cuando hay `carpeta`.
- `PasoInspeccion` y `PasoEstadillo` solo son necesarios para los destinos `bucket` y `nube`; con destino `local` se muestran igualmente (el estadillo se usa para organizar) pero la inspección queda opcional.
- Al cambiar de destino **no** se pierde nada de lo elegido: ése es el punto del rediseño.

- [ ] **Step 1: Escribir el test que falla**

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    pickFolder: vi.fn().mockResolvedValue('/datos/vuelo'),
    folderIsEmpty: vi.fn().mockResolvedValue({ empty: true }),
    cloudInspecciones: vi.fn().mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' }),
    cloudStatus: vi.fn().mockResolvedValue({ configured: true, logged_in: true, email: 'a@b.c' }),
    cloudVerify: vi.fn().mockResolvedValue({ ok: true }),
    estadilloExistente: vi.fn().mockResolvedValue({ existe: false }),
    detectSuffixesStart: vi.fn().mockResolvedValue({ started: true }),
    cloudPrepareStart: vi.fn().mockResolvedValue({ started: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
  },
  onCloud: () => () => {},
  onAnalisis: () => () => {},
}))
import TrabajoScreen from './TrabajoScreen'

beforeEach(() => vi.clearAllMocks())

describe('TrabajoScreen', () => {
  it('no ofrece destino hasta que hay carpeta', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    expect(screen.queryByText(/Organizar aquí/i)).toBeNull()
  })

  it('ofrece los tres destinos al elegir carpeta', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    expect(await screen.findByText(/Organizar aquí/i)).toBeTruthy()
    expect(screen.getByText(/Subir al bucket/i)).toBeTruthy()
    expect(screen.getByText(/Subir y organizar en la nube/i)).toBeTruthy()
  })

  it('la carpeta elegida sobrevive al cambio de destino', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
    fireEvent.click(screen.getByText(/Organizar aquí/i))
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
  })

  it('la carpeta se pide una sola vez, no una por destino', async () => {
    const { api } = await import('../bridge')
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(api.cloudPrepareStart).not.toHaveBeenCalled()) // aún sin inspección
    expect(api.pickFolder).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/trabajo/TrabajoScreen.test.jsx`
Expected: FAIL.

- [ ] **Step 3: Implementar**

```jsx
import { useState } from 'react'
import PasoCarpeta from './PasoCarpeta'
import PasoInspeccion from './PasoInspeccion'
import PasoEstadillo from './PasoEstadillo'
import PanelOrganizar from './PanelOrganizar'
import PanelSubida from './PanelSubida'

const DESTINOS = [
  { id: 'local',  titulo: 'Organizar aquí',                 detalle: 'Se organiza en este ordenador, en la carpeta que elijas.' },
  { id: 'bucket', titulo: 'Subir al bucket',                detalle: 'Las imágenes van a la nube tal cual; se organizan después.' },
  { id: 'nube',   titulo: 'Subir y organizar en la nube',   detalle: 'Se suben y ATOM las organiza sin ocupar este ordenador.' },
]

export default function TrabajoScreen({ ready, running, onRun }) {
  const [carpeta, setCarpeta] = useState('')
  const [prefijo, setPrefijo] = useState('')
  const [elegida, setElegida] = useState(null)
  const [estadillo, setEstadillo] = useState({ rutas: [], listo: false, subiendo: false, subir: async () => {} })
  const [destino, setDestino] = useState(null)

  return (
    <div className="card">
      <PasoCarpeta
        label="Carpeta del vuelo"
        value={carpeta}
        onChange={setCarpeta}
        disabled={running}
      />
      <PasoInspeccion
        ready={ready}
        prefijo={prefijo}
        onChange={(p, e) => { setPrefijo(p); setElegida(e) }}
        disabled={running || estadillo.subiendo}
      />
      <PasoEstadillo
        prefijo={prefijo}
        disabled={running}
        onEstado={setEstadillo}
      />

      {carpeta && (
        <div className="field">
          <span className="field-label">¿Qué hacemos con este trabajo?</span>
          <div className="destinos">
            {DESTINOS.map((d) => (
              <button
                key={d.id}
                type="button"
                className={`destino${destino === d.id ? ' destino-activo' : ''}`}
                aria-pressed={destino === d.id}
                onClick={() => setDestino(d.id)}
              >
                <span className="destino-titulo">{d.titulo}</span>
                <span className="destino-detalle">{d.detalle}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {destino === 'local' && (
        <PanelOrganizar
          origen={carpeta}
          estadillos={estadillo.rutas}
          ready={ready}
          running={running}
          onRun={onRun}
        />
      )}

      {(destino === 'bucket' || destino === 'nube') && (
        <PanelSubida
          carpeta={carpeta}
          prefijo={prefijo}
          inspeccionId={elegida?.id}
          estadilloListo={estadillo.listo}
          subirEstadillo={estadillo.subir}
          ready={ready}
        />
      )}
    </div>
  )
}
```

Nota: `estadillo.rutas` viene del `onEstado` de `PasoEstadillo` (Task 7).

- [ ] **Step 4: Estilos del selector**

Al final de `webui/src/App.css`, usando los tokens y **sin `px`**:

```css
.destinos {
  display: grid;
  gap: calc(var(--u) * 2);
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
}
.destino {
  display: flex;
  flex-direction: column;
  gap: calc(var(--u) * 0.5);
  padding: calc(var(--u) * 2);
  text-align: left;
  border: 0.0625rem solid rgba(255, 255, 255, 0.12);
  border-radius: var(--radio);
  background: rgba(255, 255, 255, 0.04);
  color: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.destino:hover { background: rgba(255, 255, 255, 0.07); }
.destino-activo {
  border-color: #EE753A;
  background: rgba(238, 117, 58, 0.12);
}
.destino-titulo { font-size: var(--fs-1); font-weight: 600; }
.destino-detalle { font-size: var(--fs-0); opacity: 0.7; }
```

Antes de escribirlo, comprueba los nombres reales de los tokens `--fs-*` en `webui/src/index.css:31-58` y usa los que existan; si no hay `--fs-1`/`--fs-0`, ajusta a los que haya. **No inventes tokens nuevos.**

- [ ] **Step 5: Ejecutar los tests**

Run: `cd webui && npx vitest run src/trabajo/`
Expected: PASS todo el directorio.

- [ ] **Step 6: Commit**

```bash
git add webui/src/trabajo/TrabajoScreen.jsx webui/src/trabajo/TrabajoScreen.test.jsx webui/src/trabajo/PasoEstadillo.jsx webui/src/trabajo/PasoEstadillo.test.jsx webui/src/App.css
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): pantalla Trabajo con estado unico y tres destinos"
```

---

### Task 11: Navegación de cinco pestañas a tres

**Files:**
- Modify: `webui/src/App.jsx:23-28` (`NAV`) y el `switch`/render de pantalla que lo consume
- Create: `webui/src/HerramientasScreen.jsx`
- Create: `webui/src/HerramientasScreen.test.jsx`

**Interfaces:**
- Consumes: `TrabajoScreen` (T10); las dos pantallas hoy tras `aerotools` y `otros` en `NAV` (localiza sus componentes con `grep -n "aerotools\|otros" webui/src/App.jsx`).
- Produces: `NAV` con exactamente tres entradas: `{ id: 'trabajo', label: 'Trabajo' }`, `{ id: 'herramientas', label: 'Herramientas' }`, `{ id: 'config', label: '⚙' }` — usa para el icono un SVG inline, **no el carácter ⚙** (regla: iconos SVG, nunca emoji).

- [ ] **Step 1: Escribir el test que falla**

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// reutiliza el bloque vi.mock('./bridge', ...) de src/test/estadilloSubida.test.jsx
import App from './App'

describe('Navegación', () => {
  it('tiene exactamente tres pestañas', async () => {
    render(<App />)
    const tabs = await screen.findAllByRole('tab')
    expect(tabs).toHaveLength(3)
  })

  it('arranca en Trabajo', async () => {
    render(<App />)
    expect(await screen.findByText(/Carpeta del vuelo/i)).toBeTruthy()
  })

  it('Herramientas agrupa las dos pantallas de herramientas', async () => {
    render(<App />)
    fireEvent.click(await screen.findByText('Herramientas'))
    expect(await screen.findByText(/AEROTOOLS/i)).toBeTruthy()
    expect(screen.getByText(/OTROS EQUIPOS/i)).toBeTruthy()
  })
})
```

Ajusta los textos de la tercera aserción a los rótulos reales de esas pantallas (míralos en `App.jsx` antes de escribir el test) y añade el `fireEvent` al import.

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd webui && npx vitest run src/Navegacion.test.jsx`
Expected: FAIL — hay 5 pestañas.

- [ ] **Step 3: Crear `HerramientasScreen`**

Renderiza una debajo de otra las dos pantallas existentes, cada una con su cabecera. No cambies su lógica ni sus props: solo las envuelve.

```jsx
export default function HerramientasScreen(props) {
  return (
    <>
      {/* las dos pantallas que hoy están tras NAV 'aerotools' y 'otros',
          invocadas con las MISMAS props que reciben hoy en App.jsx */}
    </>
  )
}
```

- [ ] **Step 4: Cambiar `NAV` y el render**

En `webui/src/App.jsx:23-28`, deja `NAV` con las tres entradas. En el punto donde hoy se decide qué pantalla pintar, mapea: `trabajo` → `<TrabajoScreen ready={ready} running={running} onRun={run} />`, `herramientas` → `<HerramientasScreen ... />`, `config` → `<ConfigScreen ready={ready} />`. El estado inicial de la pestaña activa pasa a `'trabajo'`.

`OrganizarScreen` y `BucketScreen` quedan definidos pero sin usar: se borran en Task 13, no ahora (así los tests viejos siguen dando cobertura durante la transición).

- [ ] **Step 5: Ejecutar la suite entera**

Run: `cd webui && npx vitest run`
Expected: los tests que navegaban a las pestañas viejas fallarán. **Actualízalos** para que naveguen a `Trabajo`, no los borres: `src/App.test.jsx:59` (`SUBIR AL BUCKET · elegir la carpeta del vuelo`) y `src/test/estadilloSubida.test.jsx:109-240`. Su comportamiento esperado no cambia, solo el camino para llegar.

- [ ] **Step 6: Commit**

```bash
git add webui/src/App.jsx webui/src/HerramientasScreen.jsx webui/src/HerramientasScreen.test.jsx webui/src/Navegacion.test.jsx webui/src/App.test.jsx webui/src/test/estadilloSubida.test.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(webui): navegacion de cinco pestanas a Trabajo/Herramientas/ajustes"
```

---

### Task 12: Tercer destino — "Subir y organizar en la nube"

> ⚠️ **BLOQUEADA a la espera del OK de Rodrigo.** Ejecuta las Tasks 1-11 y 13 primero; ésta se hace cuando él confirme el cambio en la Suite. El plan la deja documentada para no perderla.

**Hallazgo que corrige la premisa del spec:** la spec asumía "aviso a la Suite por API+PAT". La realidad del código es distinta:
- El Organizer **ya habla** con la Suite (`atom_core/run_reporter.py`), pero autentica con el **Google id_token del operario**, no con PAT. No hay ningún PAT guardado en el PC del operario, y meterlo sería justo lo que la decisión 3 quería evitar.
- La Suite **ya tiene** `POST /api/organizer/lanzar` (`Atom-suite/server.js:3208`), que hace exactamente lo que queremos (resuelve origen/destino en el bucket desde `inspecciones_pv`, valida estadillo, crea `organizer_operaciones` y llama a `lanzarOrganizerJob`). Pero está protegida por `guardConsultaRuns` (`server.js:2833`): **sesión-cookie + `esAtom`**. El Organizer no puede llamarla.

**Decisión propuesta (pendiente de OK):** no introducir PAT. Añadir en la Suite una ruta hermana `POST /api/organizer/lanzar-desde-subida` protegida por `requireIngestOrganizer` (`server.js:2271`) — el mismo middleware de Google id_token que ya usan `/api/organizer/runs`, `/fin`, `/logs`, `/giros`, y por tanto el mismo canal que el Organizer ya tiene montado y probado. Cuerpo: `{ inspeccion_id }`; internamente delega en el mismo handler que `/lanzar`.

**Por qué necesita OK:** toca el repo `Atom-suite`, no éste, y su despliegue es a producción.

**Files (repo Atom-suite, tras el OK):**
- Modify: `Atom-suite/server.js` — extraer el cuerpo del handler de `:3208` a una función reutilizable y registrar la ruta nueva con `requireIngestOrganizer`.
- Test en la suite de Atom-suite siguiendo sus convenciones.

**Files (este repo):**
- Modify: `atom_core/run_reporter.py` — método `lanzar_organizacion(inspeccion_id)` calcado de `subida()` (`run_reporter.py:331-354`), apuntando a `/api/organizer/lanzar-desde-subida`.
- Modify: `app_webview.py` — método `cloud_organizar(inspeccion_id)` que lo llama y devuelve `{ok, operacion_id}` o `{ok: False, error}`.
- Modify: `webui/src/bridge.js` — `cloudOrganizar: (inspeccionId) => call('cloud_organizar', inspeccionId)`.
- Modify: `webui/src/trabajo/TrabajoScreen.jsx` — con `destino === 'nube'`, pasar a `PanelSubida` un `onSubidaOk` que llame `api.cloudOrganizar(elegida?.id)` y muestre "Enviado a la nube. El seguimiento está en la Suite."

**Comportamiento si la Suite aún no tiene la ruta:** `cloud_organizar` recibirá 404. Debe devolver `{ok: False, error: "Esta versión de la Suite todavía no organiza en la nube."}` y la UI mostrarlo como `hint-warn` — **sin** perder el hecho de que la subida sí terminó bien. Fail-open, igual que hace `_reportar_subida` hoy.

---

### Task 13: Retirar las pantallas viejas y verificar

**Files:**
- Modify: `webui/src/App.jsx` — borrar `OrganizarScreen` (`:714-856`) y `BucketScreen` (`:864-1666`) y todo lo que quede huérfano

- [ ] **Step 1: Comprobar que nadie las usa**

Run: `grep -n "OrganizarScreen\|BucketScreen" webui/src/`
Expected: solo sus propias definiciones (tras Task 11 ya no se renderizan).

- [ ] **Step 2: Borrarlas**

Borra ambas funciones enteras. Después, busca lo que quede sin usar: la constante `NUEVA` (`App.jsx:862`) si `PasoInspeccion` se llevó la suya, `ADV_FIELDS` (`App.jsx:21`) si se movió, `cloudUploadConfirmando` (`App.jsx:156-171`) que ahora vive en su módulo. Elimina también los imports que dejen de usarse.

- [ ] **Step 3: Auditar referencias colgando**

Run:
```bash
grep -rn "estadSubiendo\|prepararRef\|autoOmitAplicadoRef\|estadCheckTokenRef\|cloudPrepare\b\|detectSuffixes\b" webui/src app_webview.py atom_core | grep -v node_modules
```
Clasifica cada resultado: (a) uso legítimo en los componentes nuevos, (b) método síncrono del bridge que se conserva a propósito, (c) resto muerto → bórralo. Reporta la clasificación en el mensaje de cierre.

- [ ] **Step 4: Suite completa**

Run: `python -m pytest -q`
Expected: 976 passed aprox., 1 failed — y ese 1 debe ser **exactamente** `tests/test_dji_resiliencia_parallel.py::test_raw_truncado_no_tumba_el_lote`. Cualquier otro fallo es una regresión introducida por este plan.

Run: `cd webui && npx vitest run`
Expected: todo verde.

- [ ] **Step 5: Build de producción**

Run: `cd webui && NODE_OPTIONS=--max-old-space-size=5120 npm run build`
Expected: build sin errores. (El `NODE_OPTIONS` evita el OOM conocido de esta máquina.)

- [ ] **Step 6: Commit**

```bash
git add webui/src/App.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "refactor(webui): retirar OrganizarScreen y BucketScreen"
```

---

## Verificación final del plan

- [ ] `python -m pytest -q` → solo el fallo preexistente aceptado.
- [ ] `cd webui && npx vitest run` → verde.
- [ ] `cd webui && NODE_OPTIONS=--max-old-space-size=5120 npm run build` → verde.
- [ ] `grep -rnE '[0-9.]+px' webui/src --include='*.jsx' --include='*.css'` → ningún `px` **nuevo** respecto al estado inicial (2 de diseño en `index.css:71-72` + 3 calculados en `pulsacion.jsx:72-74`).
- [ ] `grep -rn "😀\|📁\|⚙️" webui/src` → sin emoji en la UI.
- [ ] Task 12 sigue pendiente del OK de Rodrigo y así se le reporta.
