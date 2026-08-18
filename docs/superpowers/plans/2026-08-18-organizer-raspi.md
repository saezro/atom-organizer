# ATOM Organizer en Raspberry Pi — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Rebeca use el ATOM Organizer completo (organizar en local + subir al bucket) desde una Raspberry Pi ARM64 con pantalla táctil de 480×320.

**Architecture:** Se añade un modo `--server` que sirve la misma `webui/dist` por HTTP con la stdlib y habla con la clase `Api` existente vía `POST /api/<metodo>` + SSE, eliminando la dependencia de PySide6/QtWebEngine que no es viable en ARM64. El frontend gana un transporte alternativo dentro de `bridge.js` (único archivo que toca `window.pywebview`), un explorador de carpetas propio que sustituye a los diálogos nativos, y un layout que cabe en 480×320. Windows sigue por la ruta pywebview sin cambios de comportamiento.

**Tech Stack:** Python 3 (`http.server`, `argparse`, `threading` — sin dependencias nuevas), React 19 + Vite 8, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-08-18-organizer-raspi-design.md`

## Global Constraints

- **No romper Windows.** Es el entorno de producción actual. La ruta pywebview (`webview.start(gui="qt")`) debe seguir comportándose exactamente igual. Cualquier tarea que toque `app_webview.py` mantiene el camino existente como default.
- **Cero dependencias nuevas en Python.** El servidor usa `http.server` de la stdlib. Nada de Flask/FastAPI: añadir deps es exactamente el problema que estamos resolviendo en ARM64.
- **Cero dependencias nuevas en el frontend.** `webui/package.json` solo tiene `react` y `react-dom`. Los iconos van como SVG inline, NO con `react-icons`.
- **Nunca `px`** en el CSS que se escriba o modifique: `rem`/`vh`/`vw`/`%`. (Regla de proyecto de Rodrigo.)
- **Dark-first**: fondo `#0a0a0a`, naranja de marca `#EE763C` para CTAs.
- **Bucket**: `gs://datos_para_organizar`, el mismo que usa Rodrigo. `BUCKET_DATOS` (`atom_core/cloud_config.py:33`) NO se toca.
- **Bind por defecto a `127.0.0.1`.** Exponer en LAN es opt-in explícito con un flag, nunca el default.
- **Versión**: `version.py` es la fuente única (`__version__`). No se toca salvo que se prepare release.
- **Tests**: backend `python -m pytest tests/ -v` (la suite está en 749 passed, ~46s — no debe bajar). Frontend `cd webui && npm test`.
- **NO commitear sin que los tests pasen. NO hacer push ni tag sin OK expreso de Rodrigo.**
- **DOCKER: SOLO `dev-fast`, `dev-build` y `docker exec <contenedor>`. PROHIBIDO `docker compose build`, `docker build`, `docker run`, `docker prune`.** (No debería hacer falta Docker en este plan.)

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `atom_core/event_sink.py` (nuevo) | Abstracción de "empujar un evento al frontend". Dos implementaciones: `WebviewSink` (evaluate_js, lo de hoy) y `QueueSink` (cola en memoria para SSE). |
| `atom_core/webserver.py` (nuevo) | Servidor HTTP stdlib: estáticos de `webui/dist`, `POST /api/<metodo>` con allowlist, `GET /events` (SSE). No conoce la lógica de negocio; recibe la instancia de `Api` inyectada. |
| `app_webview.py` (modificar) | Import perezoso de `webview`; flag `--server`/`--host`/`--port`; usar el sink en los 3 push; método nuevo `list_dir`. |
| `webui/src/bridge.js` (modificar) | Transporte dual: pywebview si existe, si no fetch + EventSource. Sigue siendo el único punto de contacto con el shell. |
| `webui/src/FolderPicker.jsx` (nuevo) | Explorador de carpetas/archivos in-app, táctil, sustituye a los diálogos nativos cuando no hay pywebview. |
| `webui/src/App.css` (modificar) | Variables `:root`, layout a 480×320, erradicar `px`. |
| `webui/src/App.jsx` (modificar) | Nav de iconos SVG, cableado del `FolderPicker`. |
| `scripts/raspi/` (nuevo) | Script de arranque (servidor + Chromium kiosk) y README de instalación en la Pi. |

---

### Task 1: Verificación previa en la Raspberry Pi (BLOQUEANTE)

**No escribas código hasta que esta tarea pase.** Si `pyexiv2` no instala en aarch64, el resto del plan no sirve y hay que replantear.

Esta tarea la ejecuta una persona con acceso físico/SSH a la Pi (Rodrigo o Rebeca), no un agente.

**Files:** ninguno (verificación).

- [ ] **Step 1: Comprobar arquitectura y Python de la Pi**

```bash
uname -m          # esperado: aarch64
python3 --version # anotar
lsb_release -a    # anotar versión de Raspberry Pi OS
```

- [ ] **Step 2: Intentar instalar el subset headless en un venv limpio**

```bash
python3 -m venv /tmp/atom-arm-test
/tmp/atom-arm-test/bin/pip install --upgrade pip
/tmp/atom-arm-test/bin/pip install -r requirements-server.txt
```

Anotar CUÁL falla si falla. El sospechoso es `pyexiv2==2.8.1`.

- [ ] **Step 3: Si `pyexiv2` falla, probar la vía del sistema**

```bash
sudo apt-get install -y libexiv2-dev python3-dev build-essential
/tmp/atom-arm-test/bin/pip install pyexiv2==2.8.1
```

- [ ] **Step 4: Comprobar que Chromium existe (lo necesita el modo servidor)**

```bash
which chromium chromium-browser
```

- [ ] **Step 5: Registrar el resultado**

Anotar en la tarea 3809 del módulo /tareas: qué instaló, qué falló, qué versión de `pyexiv2` acabó funcionando. Si `pyexiv2` no hay manera, PARAR y avisar a Rodrigo: habría que evaluar sustituirlo por `piexif`/`exifread` (ya están en el requirements) en la ruta que se use en la Pi, y eso es otro diseño.

---

### Task 2: Sink de eventos (refactor sin cambio de comportamiento)

Hoy hay tres sitios que empujan eventos al frontend con el mismo patrón `if not self._window: return` + `self._window.evaluate_js(js)`: `_push_update` (`app_webview.py:370`), `_push_cloud` (`app_webview.py:921`) y `_flush_push` (`app_webview.py:1005`). Se extrae la operación "despachar un CustomEvent" a un objeto inyectable. Windows sigue usando exactamente el mismo camino.

**Files:**
- Create: `atom_core/event_sink.py`
- Create: `tests/test_event_sink.py`
- Modify: `app_webview.py` (`bind_window` :132, `_push_update` :370-378, `_push_cloud` :921-929, `_flush_push` :1005-1042)

**Interfaces:**
- Produces:
  - `class EventSink` con `dispatch(self, event: str, detail: dict) -> None` y `dispatch_many(self, event: str, details: list[dict]) -> None`
  - `class WebviewSink(EventSink)` — `__init__(self, window)`, usa `window.evaluate_js`
  - `class QueueSink(EventSink)` — `__init__(self, maxsize: int = 1000)`, además `subscribe(self) -> queue.Queue` y `unsubscribe(self, q: queue.Queue) -> None`
  - `Api.bind_sink(self, sink: EventSink) -> None`; `Api._sink` sustituye el uso directo de `self._window` para push (pero `self._window` SIGUE existiendo, lo usan los diálogos nativos)

- [ ] **Step 1: Write the failing test**

Crear `tests/test_event_sink.py`:

```python
import json
import queue

from atom_core.event_sink import WebviewSink, QueueSink


class _FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, js):
        self.scripts.append(js)


def test_webview_sink_emite_customevent_con_el_detalle():
    win = _FakeWindow()
    sink = WebviewSink(win)
    sink.dispatch("atom:cloud", {"kind": "log", "text": "hola"})
    assert len(win.scripts) == 1
    js = win.scripts[0]
    assert "atom:cloud" in js
    assert json.dumps({"kind": "log", "text": "hola"}) in js


def test_webview_sink_agrupa_varios_en_un_solo_evaluate_js():
    # El batching existe porque evaluate_js de Qt es SINCRONO y bloquea el
    # hilo del pipeline. Un solo viaje para N eventos es el punto entero.
    win = _FakeWindow()
    sink = WebviewSink(win)
    sink.dispatch_many("atom:progress", [{"kind": "log", "text": "a"},
                                         {"kind": "log", "text": "b"}])
    assert len(win.scripts) == 1
    assert win.scripts[0].count("dispatchEvent") == 2


def test_webview_sink_traga_la_excepcion_si_la_ventana_murio():
    class _Muerta:
        def evaluate_js(self, js):
            raise RuntimeError("ventana cerrada")

    WebviewSink(_Muerta()).dispatch("atom:update", {"kind": "error"})  # no revienta


def test_queue_sink_entrega_a_cada_suscriptor():
    sink = QueueSink()
    a = sink.subscribe()
    b = sink.subscribe()
    sink.dispatch("atom:cloud", {"kind": "done", "ok": True})
    assert a.get_nowait() == ("atom:cloud", {"kind": "done", "ok": True})
    assert b.get_nowait() == ("atom:cloud", {"kind": "done", "ok": True})


def test_queue_sink_descarta_lo_viejo_si_nadie_consume():
    # Un navegador cerrado no debe hacer crecer la memoria sin limite.
    sink = QueueSink(maxsize=2)
    q = sink.subscribe()
    for i in range(5):
        sink.dispatch("atom:progress", {"kind": "progress", "value": i})
    assert q.qsize() == 2
    assert q.get_nowait()[1]["value"] == 3  # se quedan los 2 ultimos


def test_queue_sink_unsubscribe_deja_de_recibir():
    sink = QueueSink()
    q = sink.subscribe()
    sink.unsubscribe(q)
    sink.dispatch("atom:cloud", {"kind": "log"})
    assert q.empty()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_event_sink.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'atom_core.event_sink'`

- [ ] **Step 3: Write minimal implementation**

Crear `atom_core/event_sink.py`:

```python
"""Empuje de eventos Python -> JS, desacoplado del shell que los transporta.

Existe porque el Organizer corre en dos shells distintos: pywebview/Qt en
Windows (donde el transporte es `evaluate_js`) y un navegador contra el modo
`--server` en Raspberry Pi (donde es SSE). La clase `Api` no debe saber en
cual de los dos esta.
"""

from __future__ import annotations

import json
import queue
import threading


class EventSink:
    """Interfaz. `event` es el nombre del CustomEvent ('atom:progress'...)."""

    def dispatch(self, event: str, detail: dict) -> None:
        raise NotImplementedError

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        for d in details:
            self.dispatch(event, d)


class WebviewSink(EventSink):
    """Transporte historico: ejecuta el dispatchEvent dentro de la ventana."""

    def __init__(self, window) -> None:
        self._window = window

    def _run(self, js: str) -> None:
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass  # ventana cerrada a mitad de proceso

    def dispatch(self, event: str, detail: dict) -> None:
        self._run(f"window.dispatchEvent(new CustomEvent({json.dumps(event)},"
                  f"{{detail:{json.dumps(detail)}}}));")

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        if not details:
            return
        # UN solo viaje para N eventos: `evaluate_js` de Qt es sincrono y cada
        # llamada para el hilo del pipeline hasta que Chromium responde.
        self._run("".join(
            f"window.dispatchEvent(new CustomEvent({json.dumps(event)},"
            f"{{detail:{json.dumps(d)}}}));"
            for d in details
        ))


class QueueSink(EventSink):
    """Transporte del modo servidor: reparte a las colas de los suscriptores SSE.

    Cada respuesta SSE abierta es un suscriptor. Si nadie consume (navegador
    cerrado sin cerrar la conexion) se descarta lo mas viejo en vez de crecer
    sin limite: son eventos de progreso, el ultimo es el que importa.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._maxsize = maxsize
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def dispatch(self, event: str, detail: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            while True:
                try:
                    q.put_nowait((event, detail))
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_event_sink.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Cablear el sink en `Api` sin cambiar comportamiento**

En `app_webview.py`:

1. Añadir el import: `from atom_core.event_sink import WebviewSink`
2. En `__init__`, junto a `self._window = None`, añadir `self._sink = None`
3. Sustituir `bind_window` (`:132-133`) por:

```python
    def bind_window(self, window) -> None:
        self._window = window
        self._sink = WebviewSink(window)

    def bind_sink(self, sink) -> None:
        """Modo servidor: no hay ventana, solo un canal de eventos."""
        self._sink = sink
```

4. `_push_update` (`:370-378`) pasa a:

```python
    def _push_update(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:update", detail)
```

5. `_push_cloud` (`:921-929`) pasa a:

```python
    def _push_cloud(self, detail: dict) -> None:
        if not self._sink:
            return
        self._sink.dispatch("atom:cloud", detail)
```

6. En `_flush_push` (`:1005-1042`) **conservar íntegro** el docstring y toda la lógica de compactado de `progress`; sustituir SOLO el bloque final (construcción de `js` + `try/except evaluate_js`) por:

```python
        if self._sink:
            self._sink.dispatch_many("atom:progress", compactados)
```

7. En `_push` (`:979-1003`), cambiar la guarda inicial `if not self._window:` por `if not self._sink:` (el resto del método no se toca).

- [ ] **Step 6: Verificar que no se rompió nada**

Run: `python -m pytest tests/ -v`
Expected: PASS, 749 + 6 = 755 tests. Si algún test de los que leen `app_webview.py` como texto (`tests/test_cloud_bucket_tab.py`, `tests/test_progress_stats.py`, `tests/test_inspecciones.py`) falla porque buscaba un literal que ya no está, ajustar ESE test al nuevo literal — no revertir el refactor.

- [ ] **Step 7: Commit**

```bash
git add atom_core/event_sink.py tests/test_event_sink.py app_webview.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "refactor(push): extraer el empuje de eventos a un EventSink inyectable"
```

---

### Task 3: Import perezoso de pywebview y flag `--server`

En la Pi, `import webview` (`app_webview.py:27`) mata el proceso antes de llegar a nada, porque arrastra PySide6. Hay que poder importar `app_webview` y arrancarlo sin Qt instalado.

**Files:**
- Modify: `app_webview.py` (:27 import, `pick_folder` :217, `pick_file` :230, `main` :1066-1103)
- Create: `tests/test_server_mode_args.py`

**Interfaces:**
- Consumes: `Api.bind_sink` de la Task 2.
- Produces: `main()` acepta `--server`, `--host` (default `127.0.0.1`), `--port` (default `8765`). Función `_import_webview()` que importa perezosamente y devuelve el módulo.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_server_mode_args.py`:

```python
import sys
import types

import app_webview


def test_app_webview_se_importa_sin_pywebview(monkeypatch):
    # En la Raspberry Pi no hay PySide6/QtWebEngine. Importar el modulo NO
    # puede depender de que `webview` exista.
    monkeypatch.setitem(sys.modules, "webview", None)
    assert hasattr(app_webview, "Api")


def test_parser_acepta_server_host_y_port():
    parser = app_webview._build_parser()
    args = parser.parse_args(["--server", "--port", "9000"])
    assert args.server is True
    assert args.port == 9000
    assert args.host == "127.0.0.1"  # nunca 0.0.0.0 por defecto


def test_parser_sin_flags_mantiene_el_modo_ventana():
    args = app_webview._build_parser().parse_args([])
    assert args.server is False
    assert args.dev is False


def test_import_webview_da_error_claro_si_no_esta(monkeypatch):
    def _boom(name, *a, **kw):
        raise ImportError("No module named 'webview'")

    monkeypatch.setattr(app_webview.importlib, "import_module", _boom)
    try:
        app_webview._import_webview()
    except SystemExit as exc:
        assert "--server" in str(exc)  # le dice al usuario la salida
    else:
        raise AssertionError("deberia haber salido con SystemExit")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server_mode_args.py -v`
Expected: FAIL — `_build_parser` y `_import_webview` no existen.

- [ ] **Step 3: Write minimal implementation**

En `app_webview.py`:

1. Sustituir `import webview` (`:27`) por `import importlib` y añadir:

```python
def _import_webview():
    """Importa pywebview solo cuando de verdad se va a abrir una ventana.

    En Raspberry Pi (ARM64) no hay wheel de PySide6 6.4.2, asi que el import
    revienta. Como el modo `--server` no necesita ventana, el import no puede
    estar en la cabecera del modulo o el proceso muere antes de arrancar.
    """
    try:
        return importlib.import_module("webview")
    except ImportError as exc:
        sys.exit(
            f"[app_webview] No se pudo cargar pywebview/Qt: {exc}\n"
            "Si estas en Raspberry Pi u otro ARM64, arranca en modo servidor:\n"
            "    python app_webview.py --server\n"
            "y abre http://127.0.0.1:8765 en Chromium."
        )
```

2. `pick_folder`/`pick_file` referencian `webview.FOLDER_DIALOG`/`webview.OPEN_DIALOG`. Dentro de cada método, antes de usarlo: `webview = _import_webview()`. (En modo servidor estos métodos no se llaman — el frontend usa el `FolderPicker` de la Task 8 — pero si alguien los llama, el error es claro en vez de un `NameError`.)

3. Extraer el parser a su propia función:

```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ATOM Organizer (UI React/pywebview)")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Cargar el dev server de Vite (localhost:5173) con HMR.",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="No abrir ventana: servir la UI por HTTP (Raspberry Pi / ARM64).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interfaz del modo servidor. Por defecto solo local; usa 0.0.0.0 "
             "SOLO si quieres abrirla desde otro equipo de la red.",
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="Puerto del modo servidor (por defecto 8765).")
    return parser
```

4. `main()` usa `_build_parser()`, y antes de `webview.create_window(...)` hace `webview = _import_webview()`. El bloque de `--server` se cablea en la Task 4; de momento, si `args.server` es verdadero, `sys.exit("modo servidor aun no implementado")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server_mode_args.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Verificar la suite completa**

Run: `python -m pytest tests/ -v`
Expected: PASS, sin regresiones.

- [ ] **Step 6: Commit**

```bash
git add app_webview.py tests/test_server_mode_args.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(server): import perezoso de pywebview y flags --server/--host/--port"
```

---

### Task 4: Servidor HTTP — estáticos y `POST /api/<metodo>`

**Files:**
- Create: `atom_core/webserver.py`
- Create: `tests/test_webserver_api.py`
- Modify: `app_webview.py` (`main`, rama `args.server`)

**Interfaces:**
- Consumes: `QueueSink` (Task 2), `_build_parser` (Task 3).
- Produces:
  - `METODOS_EXPUESTOS: frozenset[str]` — allowlist de los 19 métodos públicos
  - `crear_servidor(api, dist_dir, host, port, sink) -> http.server.ThreadingHTTPServer`
  - `servir(api, dist_dir, host, port, sink) -> None` (bloqueante)

- [ ] **Step 1: Write the failing test**

Crear `tests/test_webserver_api.py`:

```python
import json
import urllib.error
import urllib.request

import pytest

from atom_core.event_sink import QueueSink
from atom_core.webserver import METODOS_EXPUESTOS, crear_servidor


class _ApiFalsa:
    def __init__(self):
        self.llamadas = []

    def ping(self, who="?"):
        self.llamadas.append(("ping", who))
        return {"ok": True, "msg": f"pong {who}"}

    def cloud_status(self):
        return {"configured": True, "bucket": "datos_para_organizar"}

    def revienta(self):
        raise ValueError("fallo interno")

    def _privado(self):
        return "no deberia salir"


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html>ATOM</html>", encoding="utf-8")
    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    import threading
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(base, metodo, args):
    req = urllib.request.Request(
        f"{base}/api/{metodo}",
        data=json.dumps({"args": args}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_sirve_el_index(servidor):
    _, _, base = servidor
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        assert b"ATOM" in r.read()


def test_llama_a_un_metodo_de_la_api_con_argumentos(servidor):
    _, api, base = servidor
    assert _post(base, "ping", ["rebeca"])["result"]["msg"] == "pong rebeca"
    assert api.llamadas == [("ping", "rebeca")]


def test_metodo_sin_argumentos(servidor):
    _, _, base = servidor
    assert _post(base, "cloud_status", [])["result"]["bucket"] == "datos_para_organizar"


def test_metodo_privado_no_es_alcanzable(servidor):
    _, _, base = servidor
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "_privado", [])
    assert exc.value.code == 404


def test_metodo_fuera_de_la_allowlist_no_es_alcanzable(servidor):
    # Aunque exista en el objeto: la allowlist manda, no `hasattr`.
    _, _, base = servidor
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(base, "revienta", [])
    assert exc.value.code == 404


def test_excepcion_del_metodo_se_devuelve_como_error_no_como_500_mudo(servidor, tmp_path):
    api = _ApiFalsa()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, QueueSink())
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        # `ping` con un argumento de mas: TypeError dentro del metodo
        req = urllib.request.Request(
            f"{base}/api/ping",
            data=json.dumps({"args": ["a", "b", "c"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        cuerpo = json.loads(exc.value.read())
        assert "error" in cuerpo
    finally:
        srv.shutdown()


def test_la_allowlist_cubre_los_metodos_reales_de_la_api():
    # Si alguien anade un metodo publico a `Api` y olvida exponerlo, que se vea.
    import app_webview
    publicos = {
        n for n in dir(app_webview.Api)
        if not n.startswith("_") and callable(getattr(app_webview.Api, n))
    } - {"bind_window", "bind_sink"}
    assert publicos == set(METODOS_EXPUESTOS), (
        f"faltan por exponer: {publicos - set(METODOS_EXPUESTOS)}; "
        f"sobran en la allowlist: {set(METODOS_EXPUESTOS) - publicos}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_webserver_api.py -v`
Expected: FAIL — `No module named 'atom_core.webserver'`

- [ ] **Step 3: Write minimal implementation**

Crear `atom_core/webserver.py`:

```python
"""Modo servidor: sirve la webui por HTTP en vez de meterla en una ventana Qt.

Existe para la Raspberry Pi (ARM64), donde PySide6/QtWebEngine no es viable.
Usa solo la stdlib a proposito: anadir dependencias es justo el problema que
este modo resuelve.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# Allowlist explicita. NO se usa `hasattr` para decidir que es alcanzable: un
# metodo nuevo debe entrar aqui a mano y de forma consciente.
METODOS_EXPUESTOS = frozenset({
    "ping",
    "pick_folder", "pick_file",
    # OJO: `list_dir` se anade en la Task 7, cuando el metodo exista. La
    # allowlist y los metodos reales de `Api` se validan con un test.
    "folder_is_empty", "read_estadillo_info", "detect_suffixes",
    "read_config", "write_config",
    "app_version", "check_update", "download_update", "install_update",
    "start_update_check",
    "cloud_status", "cloud_verify", "cloud_login", "cloud_logout",
    "cloud_inspecciones", "cloud_prepare", "cloud_upload", "cloud_cancel",
    "estadillo_validar", "estadillo_subir", "estadillo_existente",
    "run_organize", "run_task",
})


def _handler_factory(api, dist_dir: str, sink):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=dist_dir, **kw)

        def log_message(self, fmt, *args):
            pass  # el log de acceso por request no aporta nada aqui

        def _json(self, code: int, payload: dict) -> None:
            cuerpo = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_POST(self):
            if not self.path.startswith("/api/"):
                return self._json(404, {"error": "ruta desconocida"})
            metodo = self.path[len("/api/"):].strip("/")
            if metodo not in METODOS_EXPUESTOS:
                return self._json(404, {"error": f"metodo no expuesto: {metodo}"})
            try:
                largo = int(self.headers.get("Content-Length") or 0)
                cuerpo = json.loads(self.rfile.read(largo) or b"{}")
                args = cuerpo.get("args") or []
                resultado = getattr(api, metodo)(*args)
            except Exception as exc:  # noqa: BLE001 — el front necesita el motivo
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._json(200, {"result": resultado})

    return Handler


def crear_servidor(api, dist_dir: str, host: str, port: int, sink) -> ThreadingHTTPServer:
    if not os.path.isdir(dist_dir):
        raise FileNotFoundError(f"No existe el build del front: {dist_dir}")
    mimetypes.add_type("application/javascript", ".js")
    servidor = ThreadingHTTPServer((host, port), _handler_factory(api, dist_dir, sink))
    servidor.daemon_threads = True
    return servidor


def servir(api, dist_dir: str, host: str, port: int, sink) -> None:
    servidor = crear_servidor(api, dist_dir, host, port, sink)
    print(f"[atom] UI en http://{host}:{servidor.server_address[1]}  (Ctrl-C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        servidor.shutdown()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_webserver_api.py -v`
Expected: PASS (7 tests). Si el último test falla, es que la allowlist y los métodos reales de `Api` no cuadran: **corrige la allowlist**, no el test.

- [ ] **Step 5: Cablear la rama `--server` en `main()`**

Sustituir el `sys.exit("modo servidor aun no implementado")` de la Task 3 por:

```python
    if args.server:
        from atom_core.event_sink import QueueSink
        from atom_core.webserver import servir

        api = Api()
        sink = QueueSink()
        api.bind_sink(sink)
        servir(api, str(DIST_INDEX.parent), args.host, args.port, sink)
        return
```

(Va ANTES de `resolve_target`/`create_window`, y antes de cualquier uso de `webview`.)

- [ ] **Step 6: Probar a mano**

```bash
cd webui && npm run build && cd ..
python app_webview.py --server &
curl -s -X POST http://127.0.0.1:8765/api/ping -H 'Content-Type: application/json' -d '{"args":["prueba"]}'
curl -s http://127.0.0.1:8765/ | head -c 200
kill %1
```
Expected: el `ping` devuelve `{"result": {"ok": true, ...}}` y el `/` devuelve el HTML del build.

- [ ] **Step 7: Commit**

```bash
git add atom_core/webserver.py tests/test_webserver_api.py app_webview.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(server): servidor HTTP stdlib con estaticos y dispatch a la Api"
```

---

### Task 5: SSE en `GET /events`

Los tres canales de eventos (`atom:progress`, `atom:update`, `atom:cloud`) viajan por una sola conexión SSE.

**Files:**
- Modify: `atom_core/webserver.py` (`do_GET`)
- Create: `tests/test_webserver_sse.py`

**Interfaces:**
- Consumes: `QueueSink.subscribe()`/`unsubscribe()` (Task 2), `crear_servidor` (Task 4).
- Produces: `GET /events` devuelve `text/event-stream`; cada evento se serializa como `event: <nombre>\ndata: <json del detail>\n\n`.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_webserver_sse.py`:

```python
import json
import threading
import urllib.request

import pytest

from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor


class _ApiFalsa:
    def ping(self, who="?"):
        return {"ok": True}


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    sink = QueueSink()
    srv = crear_servidor(_ApiFalsa(), str(tmp_path), "127.0.0.1", 0, sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, sink, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_events_entrega_lo_que_se_empuja_por_el_sink(servidor):
    _, sink, base = servidor
    stream = urllib.request.urlopen(f"{base}/events", timeout=5)
    assert stream.headers["Content-Type"].startswith("text/event-stream")

    # Se empuja DESPUES de abrir el stream: la suscripcion ya esta viva.
    def empujar():
        import time
        time.sleep(0.3)
        sink.dispatch("atom:cloud", {"kind": "done", "ok": True, "uploaded": 7})

    threading.Thread(target=empujar, daemon=True).start()

    lineas = []
    for _ in range(6):
        linea = stream.readline().decode().strip()
        if linea:
            lineas.append(linea)
        if any(l.startswith("data:") for l in lineas):
            break
    stream.close()

    assert any(l == "event: atom:cloud" for l in lineas), lineas
    datos = [json.loads(l[len("data:"):]) for l in lineas if l.startswith("data:")]
    assert datos[0]["uploaded"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_webserver_sse.py -v`
Expected: FAIL — `/events` devuelve 404 (lo trata como fichero estático inexistente).

- [ ] **Step 3: Write minimal implementation**

En `atom_core/webserver.py`, dentro de la clase `Handler`, añadir:

```python
        def do_GET(self):
            if self.path.rstrip("/") == "/events":
                return self._sse()
            return super().do_GET()

        def _sse(self) -> None:
            """Un solo stream para los tres canales de eventos.

            Sustituye a `evaluate_js`: el navegador no puede recibir un push que
            el shell le inyecte, asi que se invierte el sentido y es el cliente
            quien mantiene la conexion abierta.
            """
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            cola = sink.subscribe()
            try:
                while True:
                    try:
                        evento, detalle = cola.get(timeout=15)
                    except Exception:
                        # Comentario keep-alive: sin trafico, un proxy o el
                        # propio navegador cerrarian la conexion en silencio.
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    payload = (f"event: {evento}\n"
                               f"data: {json.dumps(detalle)}\n\n").encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # el navegador cerro la pestana
            finally:
                sink.unsubscribe(cola)
```

Nota: `sink` está capturado por el closure de `_handler_factory`, ya se le pasa como parámetro.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_webserver_sse.py -v`
Expected: PASS

- [ ] **Step 5: Verificar la suite completa**

Run: `python -m pytest tests/ -v`
Expected: PASS sin regresiones.

- [ ] **Step 6: Commit**

```bash
git add atom_core/webserver.py tests/test_webserver_sse.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(server): canal SSE en /events para los tres canales de eventos"
```

---

### Task 6: `bridge.js` con transporte dual

**Files:**
- Modify: `webui/src/bridge.js` (`whenBridgeReady` :11-28, `call` :30-35)
- Create: `webui/src/bridge.test.js`

**Interfaces:**
- Consumes: `POST /api/<metodo>` (Task 4), `GET /events` (Task 5).
- Produces: `export function isServerMode(): boolean` — `true` cuando no hay `window.pywebview` (lo consume el `FolderPicker` de la Task 8). El resto de la API pública de `bridge.js` (`api`, `onProgress`, `onUpdate`, `onCloud`, `whenBridgeReady`) NO cambia de firma.

- [ ] **Step 1: Write the failing test**

Crear `webui/src/bridge.test.js`:

```javascript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

describe('bridge en modo servidor (sin pywebview)', () => {
  beforeEach(() => {
    vi.resetModules()
    delete window.pywebview
    // EventSource no existe en jsdom: se simula.
    class FakeEventSource {
      constructor(url) {
        this.url = url
        this.listeners = {}
        FakeEventSource.ultima = this
      }
      addEventListener(tipo, fn) { this.listeners[tipo] = fn }
      close() { this.cerrada = true }
      emitir(tipo, detail) { this.listeners[tipo]?.({ data: JSON.stringify(detail) }) }
    }
    window.EventSource = FakeEventSource
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: { ok: true, msg: 'pong' } }),
    }))
  })

  afterEach(() => { vi.restoreAllMocks() })

  it('isServerMode es true si no hay pywebview', async () => {
    const { isServerMode } = await import('./bridge.js')
    expect(isServerMode()).toBe(true)
  })

  it('una llamada de la api va por POST /api/<metodo> con los argumentos', async () => {
    const { api } = await import('./bridge.js')
    const res = await api.ping('rebeca')
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/ping',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body)
    expect(body).toEqual({ args: ['rebeca'] })
    expect(res.msg).toBe('pong')
  })

  it('un error del backend se propaga como excepcion', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      json: async () => ({ error: 'ValueError: ruta invalida' }),
    }))
    const { api } = await import('./bridge.js')
    await expect(api.pickFolder()).rejects.toThrow(/ruta invalida/)
  })

  it('los eventos SSE se reemiten como CustomEvent y onCloud los recibe', async () => {
    const { onCloud } = await import('./bridge.js')
    const visto = []
    onCloud((d) => visto.push(d))
    window.EventSource.ultima.emitir('atom:cloud', { kind: 'done', uploaded: 3 })
    expect(visto).toEqual([{ kind: 'done', uploaded: 3 }])
  })
})

describe('bridge en modo pywebview (Windows)', () => {
  beforeEach(() => {
    vi.resetModules()
    window.pywebview = { api: { ping: vi.fn(async () => ({ ok: true, msg: 'pong qt' })) } }
    globalThis.fetch = vi.fn()
  })

  it('isServerMode es false y NO se usa fetch', async () => {
    const { api, isServerMode } = await import('./bridge.js')
    expect(isServerMode()).toBe(false)
    const res = await api.ping('rodrigo')
    expect(res.msg).toBe('pong qt')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webui && npx vitest run src/bridge.test.js`
Expected: FAIL — `isServerMode` no existe y las llamadas se quedan colgadas esperando a pywebview.

- [ ] **Step 3: Write minimal implementation**

En `webui/src/bridge.js`:

1. Añadir arriba, después del comentario de cabecera:

```javascript
// El Organizer corre en dos shells: pywebview/Qt (Windows) y un navegador
// contra el modo `--server` (Raspberry Pi, donde Qt no es viable en ARM64).
// La deteccion es por presencia: si `window.pywebview` no aparece, es servidor.
// No se puede decidir al importar el modulo porque en Qt la inyeccion es
// asincrona; por eso `whenBridgeReady` da un plazo antes de rendirse.
const ESPERA_PYWEBVIEW_MS = 1500
let modoServidor = null

export function isServerMode() {
  return modoServidor === true
}
```

2. Sustituir `whenBridgeReady` para que resuelva también cuando decide que es modo servidor:

```javascript
export function whenBridgeReady() {
  return new Promise((resolve) => {
    if (window.pywebview?.api) { modoServidor = false; return resolve() }
    let done = false
    let timer = null
    const finish = (servidor) => {
      if (done) return
      done = true
      modoServidor = servidor
      if (timer !== null) clearInterval(timer)
      clearTimeout(plazo)
      window.removeEventListener('pywebviewready', alListo)
      if (servidor) conectarEventos()
      resolve()
    }
    const alListo = () => finish(false)
    window.addEventListener('pywebviewready', alListo, { once: true })
    timer = setInterval(() => { if (window.pywebview?.api) finish(false) }, 100)
    // Si en este plazo no aparecio, no va a aparecer: es un navegador normal.
    const plazo = setTimeout(() => finish(true), ESPERA_PYWEBVIEW_MS)
  })
}

let fuenteEventos = null

function conectarEventos() {
  if (fuenteEventos) return
  fuenteEventos = new EventSource('/events')
  for (const canal of ['atom:progress', 'atom:update', 'atom:cloud']) {
    fuenteEventos.addEventListener(canal, (e) => {
      window.dispatchEvent(new CustomEvent(canal, { detail: JSON.parse(e.data) }))
    })
  }
}
```

3. Sustituir `call`:

```javascript
async function call(method, ...args) {
  await whenBridgeReady()
  if (modoServidor) {
    const r = await fetch(`/api/${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args }),
    })
    const cuerpo = await r.json()
    if (!r.ok) throw new Error(cuerpo.error || `Error llamando a «${method}»`)
    return cuerpo.result
  }
  const fn = window.pywebview.api[method]
  if (!fn) throw new Error(`El bridge no expone «${method}»`)
  return fn(...args)
}
```

**No tocar** el objeto `api` ni `onProgress`/`onUpdate`/`onCloud`: siguen igual, y por eso ningún componente cambia.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webui && npx vitest run src/bridge.test.js`
Expected: PASS (5 tests)

- [ ] **Step 5: Verificar el resto del frontend**

Run: `cd webui && npm test && npm run lint && npm run build`
Expected: todo PASS. El build es necesario porque `dist/` es lo que sirve el modo servidor.

- [ ] **Step 6: Commit**

```bash
git add webui/src/bridge.js webui/src/bridge.test.js
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(bridge): transporte dual pywebview/HTTP+SSE"
```

---

### Task 7: `Api.list_dir` para el explorador in-app

Sin Qt no hay `create_file_dialog`. El frontend necesita poder listar el disco.

**Files:**
- Modify: `app_webview.py` (añadir `list_dir` junto a `pick_folder`, ~`:217`)
- Create: `tests/test_list_dir.py`

**Interfaces:**
- Produces: `Api.list_dir(self, path: str | None = None) -> dict` con forma
  `{"ok": True, "path": str, "parent": str|None, "dirs": [{"name": str, "path": str}], "files": [{"name": str, "path": str, "size": int}]}`
  o `{"ok": False, "error": str}`. Con `path=None` devuelve el `$HOME` del usuario.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_list_dir.py`:

```python
import os

import app_webview


def test_lista_carpetas_y_ficheros_ordenados(tmp_path):
    (tmp_path / "zeta").mkdir()
    (tmp_path / "alfa").mkdir()
    (tmp_path / "b.txt").write_text("hola", encoding="utf-8")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    res = app_webview.Api().list_dir(str(tmp_path))

    assert res["ok"] is True
    assert [d["name"] for d in res["dirs"]] == ["alfa", "zeta"]
    assert [f["name"] for f in res["files"]] == ["a.txt", "b.txt"]
    assert res["files"][1]["size"] == 4
    assert res["path"] == str(tmp_path)


def test_expone_el_padre_para_poder_subir(tmp_path):
    hija = tmp_path / "hija"
    hija.mkdir()
    res = app_webview.Api().list_dir(str(hija))
    assert res["parent"] == str(tmp_path)


def test_la_raiz_no_tiene_padre():
    res = app_webview.Api().list_dir(os.path.abspath(os.sep))
    assert res["ok"] is True
    assert res["parent"] is None


def test_sin_ruta_arranca_en_el_home():
    res = app_webview.Api().list_dir(None)
    assert res["ok"] is True
    assert res["path"] == os.path.expanduser("~")


def test_ruta_inexistente_devuelve_error_no_excepcion(tmp_path):
    res = app_webview.Api().list_dir(str(tmp_path / "no-existe"))
    assert res["ok"] is False
    assert "error" in res


def test_una_entrada_ilegible_no_tumba_el_listado(tmp_path, monkeypatch):
    (tmp_path / "buena").mkdir()
    real = os.stat

    def _stat_selectivo(ruta, *a, **kw):
        if "mala" in str(ruta):
            raise PermissionError("denegado")
        return real(ruta, *a, **kw)

    (tmp_path / "mala.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(os, "stat", _stat_selectivo)
    res = app_webview.Api().list_dir(str(tmp_path))
    assert res["ok"] is True
    assert [d["name"] for d in res["dirs"]] == ["buena"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_list_dir.py -v`
Expected: FAIL — `Api` no tiene `list_dir`.

- [ ] **Step 3: Write minimal implementation**

En `app_webview.py`, junto a `pick_folder`:

```python
    def list_dir(self, path: str | None = None) -> dict:
        """Lista un directorio para el explorador de la UI.

        En modo servidor no hay dialogo nativo de ficheros (eso lo daba Qt), y
        en una pantalla de 480x320 manejada con el dedo tampoco seria usable.
        El explorador vive en la webui y esto es lo que lo alimenta.
        """
        destino = os.path.abspath(os.path.expanduser(path or "~"))
        if not os.path.isdir(destino):
            return {"ok": False, "error": f"No es una carpeta: {destino}"}
        dirs, files = [], []
        try:
            entradas = sorted(os.listdir(destino), key=str.lower)
        except OSError as exc:
            return {"ok": False, "error": f"No se pudo leer: {exc}"}
        for nombre in entradas:
            if nombre.startswith("."):
                continue  # ocultos fuera: ruido en una pantalla diminuta
            completo = os.path.join(destino, nombre)
            try:
                if os.path.isdir(completo):
                    dirs.append({"name": nombre, "path": completo})
                else:
                    files.append({"name": nombre, "path": completo,
                                  "size": os.stat(completo).st_size})
            except OSError:
                continue  # permisos, enlace roto, unidad desconectada
        padre = os.path.dirname(destino)
        return {
            "ok": True,
            "path": destino,
            "parent": None if padre == destino else padre,
            "dirs": dirs,
            "files": files,
        }
```

Añadir `list_dir` a `METODOS_EXPUESTOS` en `atom_core/webserver.py` (la Task 4 lo dejó fuera a propósito, porque el método aún no existía) y a `bridge.js`:

```javascript
  listDir: (path) => call('list_dir', path ?? null),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_list_dir.py tests/test_webserver_api.py -v`
Expected: PASS (el test de allowlist de la Task 4 confirma que `list_dir` está expuesto).

- [ ] **Step 5: Commit**

```bash
git add app_webview.py tests/test_list_dir.py webui/src/bridge.js
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(api): list_dir para el explorador de carpetas de la UI"
```

---

### Task 8: `FolderPicker` — explorador táctil in-app

**Files:**
- Create: `webui/src/FolderPicker.jsx`
- Create: `webui/src/FolderPicker.test.jsx`
- Modify: `webui/src/App.jsx` (los sitios que llaman a `api.pickFolder()`/`api.pickFile()`)

**Interfaces:**
- Consumes: `api.listDir(path)` (Task 7), `isServerMode()` (Task 6).
- Produces: `<FolderPicker mode="folder"|"file" startPath={string|null} onPick={(path) => void} onCancel={() => void} />`

- [ ] **Step 1: Write the failing test**

Crear `webui/src/FolderPicker.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('./bridge.js', () => ({
  isServerMode: () => true,
  api: {
    listDir: vi.fn(async (path) => {
      if (!path || path === '/home/rebeca') {
        return {
          ok: true, path: '/home/rebeca', parent: '/home',
          dirs: [{ name: 'VUELOS', path: '/home/rebeca/VUELOS' }],
          files: [{ name: 'estadillo.xlsx', path: '/home/rebeca/estadillo.xlsx', size: 12 }],
        }
      }
      return { ok: true, path, parent: '/home/rebeca', dirs: [], files: [] }
    }),
  },
}))

import FolderPicker from './FolderPicker.jsx'

describe('FolderPicker', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lista las carpetas de la ruta inicial', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    expect(await screen.findByText('VUELOS')).toBeInTheDocument()
  })

  it('en modo carpeta no ofrece ficheros como elegibles', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    expect(screen.queryByText('estadillo.xlsx')).not.toBeInTheDocument()
  })

  it('en modo fichero si los muestra y devuelve la ruta al tocarlo', async () => {
    const onPick = vi.fn()
    render(<FolderPicker mode="file" startPath={null} onPick={onPick} onCancel={() => {}} />)
    await userEvent.click(await screen.findByText('estadillo.xlsx'))
    expect(onPick).toHaveBeenCalledWith('/home/rebeca/estadillo.xlsx')
  })

  it('navegar dentro de una carpeta la lista', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await userEvent.click(await screen.findByText('VUELOS'))
    await waitFor(() => expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS'))
  })

  it('el boton de elegir devuelve la carpeta ACTUAL, no la seleccionada', async () => {
    const onPick = vi.fn()
    render(<FolderPicker mode="folder" startPath={null} onPick={onPick} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    await userEvent.click(screen.getByRole('button', { name: /usar esta carpeta/i }))
    expect(onPick).toHaveBeenCalledWith('/home/rebeca')
  })

  it('cancelar avisa al padre', async () => {
    const onCancel = vi.fn()
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={onCancel} />)
    await screen.findByText('VUELOS')
    await userEvent.click(screen.getByRole('button', { name: /cancelar/i }))
    expect(onCancel).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webui && npx vitest run src/FolderPicker.test.jsx`
Expected: FAIL — no existe `FolderPicker.jsx`.

- [ ] **Step 3: Write minimal implementation**

Crear `webui/src/FolderPicker.jsx`:

```jsx
import { useCallback, useEffect, useState } from 'react'
import { api } from './bridge.js'

// Sustituye al dialogo nativo de ficheros, que solo existia via Qt. Aparte de
// no estar disponible en modo servidor, un dialogo GTK en una pantalla de
// 480x320 manejada con el dedo seria inutilizable: aqui las filas son targets
// grandes y la navegacion es un nivel cada vez.
export default function FolderPicker({ mode = 'folder', startPath = null, onPick, onCancel }) {
  const [estado, setEstado] = useState({ cargando: true, datos: null, error: null })

  const cargar = useCallback(async (ruta) => {
    setEstado((s) => ({ ...s, cargando: true, error: null }))
    try {
      const datos = await api.listDir(ruta)
      if (!datos.ok) return setEstado({ cargando: false, datos: null, error: datos.error })
      setEstado({ cargando: false, datos, error: null })
    } catch (e) {
      setEstado({ cargando: false, datos: null, error: String(e.message || e) })
    }
  }, [])

  useEffect(() => { cargar(startPath) }, [cargar, startPath])

  const { cargando, datos, error } = estado

  return (
    <div className="picker">
      <div className="picker-ruta" title={datos?.path}>{datos?.path || '…'}</div>

      {error && <div className="picker-error">{error}</div>}

      <ul className="picker-lista">
        {datos?.parent && (
          <li>
            <button className="picker-fila" onClick={() => cargar(datos.parent)}>
              .. subir
            </button>
          </li>
        )}
        {datos?.dirs.map((d) => (
          <li key={d.path}>
            <button className="picker-fila picker-dir" onClick={() => cargar(d.path)}>
              {d.name}
            </button>
          </li>
        ))}
        {mode === 'file' && datos?.files.map((f) => (
          <li key={f.path}>
            <button className="picker-fila picker-file" onClick={() => onPick(f.path)}>
              {f.name}
            </button>
          </li>
        ))}
      </ul>

      {cargando && <div className="picker-cargando">Cargando…</div>}

      <div className="picker-acciones">
        <button className="btn-sec" onClick={onCancel}>Cancelar</button>
        {mode === 'folder' && (
          <button className="btn-cta" disabled={!datos}
                  onClick={() => onPick(datos.path)}>
            Usar esta carpeta
          </button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webui && npx vitest run src/FolderPicker.test.jsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Cablearlo en `App.jsx`**

Localizar los call sites de `api.pickFolder()` y `api.pickFile()` en `App.jsx`/`TaskBlock.jsx`/`EstadilloField.jsx` (los identificó el mapeo: 26 call sites en total, los de picker son un subconjunto). Sustituir el patrón directo por:

```jsx
// En modo servidor no hay dialogo nativo: se abre el explorador propio.
const elegirCarpeta = async (aplicar) => {
  if (isServerMode()) return setPicker({ mode: 'folder', aplicar })
  const ruta = await api.pickFolder()
  if (ruta) aplicar(ruta)
}
```

y renderizar `{picker && <FolderPicker mode={picker.mode} startPath={null} onPick={(p) => { picker.aplicar(p); setPicker(null) }} onCancel={() => setPicker(null)} />}` dentro del `ModalWrapper`/overlay que ya use la app para modales.

- [ ] **Step 6: Verificar**

Run: `cd webui && npm test && npm run lint && npm run build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webui/src/FolderPicker.jsx webui/src/FolderPicker.test.jsx webui/src/App.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(ui): explorador de carpetas propio para el modo servidor"
```

---

### Task 9: Layout 480×320 — variables y navegación

`App.css` son 959 líneas sin `:root` y con 72 `px`. Esta tarea introduce las variables y arregla la navegación; la Task 10 se ocupa del contenido.

**Files:**
- Modify: `webui/src/App.css` (bloque nuevo al principio; `.seg`/`.seg-btn`)
- Modify: `webui/src/App.jsx` (`NAV` :15-21, bloque `<nav>` :316-333)
- Create: `webui/src/NavIcon.jsx`

**Interfaces:**
- Produces: `<NavIcon id="organizar|bucket|aerotools|otros|config" />` — SVG inline, 1.5rem, `currentColor`.

- [ ] **Step 1: Añadir el bloque de variables al principio de `App.css`**

```css
/* Escala tipografica y de espaciado en rem para que la MISMA UI sirva en un
   portatil de 1100x760 y en la pantalla de 480x320 de la Raspberry Pi: basta
   con mover --u y --fs-base en el breakpoint, sin tocar cada regla. */
:root {
  --u: 0.5rem;
  --fs-base: 1rem;
  --fs-sm: 0.875rem;
  --fs-lg: 1.125rem;
  --radio: 0.75rem;
  --toque-min: 2.75rem;       /* target tactil minimo */
  --bg: #0a0a0a;
  --bg-elev: #141414;
  --texto: #f5f5f5;
  --texto-tenue: #a3a3a3;
  --marca: #EE763C;
  --borde: #2a2a2a;
}

/* Pantalla de la Raspberry Pi: 480x320. Todo encoge de golpe desde aqui. */
@media (max-width: 34rem), (max-height: 26rem) {
  :root {
    --u: 0.3125rem;
    --fs-base: 0.8125rem;
    --fs-sm: 0.6875rem;
    --fs-lg: 0.9375rem;
    --radio: 0.5rem;
  }
}
```

- [ ] **Step 2: Crear `webui/src/NavIcon.jsx`**

```jsx
// SVG inline a proposito: `webui/package.json` solo depende de react y
// react-dom, y meter `react-icons` por cinco iconos engorda el bundle que va
// empaquetado dentro del ejecutable.
const TRAZOS = {
  organizar: 'M3 7h18M3 12h18M3 17h12',
  bucket: 'M12 3v12m0 0l-4-4m4 4l4-4M4 19h16',
  aerotools: 'M12 2l9 6-9 6-9-6 9-6zm0 12l9-6M3 8l9 6',
  otros: 'M4 6h6v6H4zM14 6h6v6h-6zM9 16h6v4H9z',
  config: 'M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2 1-2 4-2-1-2 1-1 2h-4l-1-2-2-1-2 1-2-4 2-1v-2l-2-1 2-4 2 1 2-1 1-2h4l1 2 2 1 2-1 2 4-2 1v2z',
}

export default function NavIcon({ id }) {
  return (
    <svg viewBox="0 0 24 24" width="1.5rem" height="1.5rem" fill="none"
         stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true">
      <path d={TRAZOS[id]} />
    </svg>
  )
}
```

- [ ] **Step 3: Cambiar el `<nav>` de `App.jsx`**

Ampliar `NAV` con una etiqueta corta y renderizar icono + etiqueta solo de la activa:

```jsx
const NAV = [
  { id: 'organizar', label: 'Organizar', corto: 'Organizar' },
  { id: 'bucket', label: 'SUBIR AL BUCKET', corto: 'Bucket' },
  { id: 'aerotools', label: 'AEROTOOLS', corto: 'Aerotools' },
  { id: 'otros', label: 'OTROS EQUIPOS', corto: 'Equipos' },
  { id: 'config', label: 'CONFIGURACIÓN', corto: 'Config' },
]
```

```jsx
      <nav className="seg">
        {NAV.map((n) => (
          <button
            key={n.id}
            className={'seg-btn' + (section === n.id ? ' active' : '')}
            onClick={() => setSection(n.id)}
            title={n.label}
            aria-label={n.label}
          >
            <NavIcon id={n.id} />
            <span className="seg-txt">{n.corto}</span>
          </button>
        ))}
      </nav>
```

- [ ] **Step 4: Reglas CSS de la nav**

```css
.seg { display: flex; gap: var(--u); }
.seg-btn {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: calc(var(--u) / 2);
  min-height: var(--toque-min); min-width: var(--toque-min);
  flex: 1; padding: var(--u); border-radius: var(--radio);
  font-size: var(--fs-sm); color: var(--texto-tenue);
  background: var(--bg-elev); border: 0.0625rem solid var(--borde);
}
.seg-btn.active { color: var(--marca); border-color: var(--marca); }

/* En 480 de ancho no caben cinco etiquetas: solo se lee la de la pestana
   activa, el resto quedan como iconos. */
@media (max-width: 34rem) {
  .seg-btn .seg-txt { display: none; }
  .seg-btn.active .seg-txt { display: inline; }
}
```

- [ ] **Step 5: Verificar visualmente a 480×320**

```bash
cd webui && npm run build && cd ..
python app_webview.py --server &
chromium --window-size=480,320 --app=http://127.0.0.1:8765 &
```
Comprobar: la barra de navegación cabe entera sin scroll horizontal, y los cinco botones son tocables.

- [ ] **Step 6: Comprobar que no quedan `px` nuevos**

Run: `grep -n "[0-9]px" webui/src/App.css | head -40`
Expected: los `px` que queden son de reglas antiguas (Task 10 los ataca); **ninguno** en las reglas añadidas aquí.

- [ ] **Step 7: Commit**

```bash
git add webui/src/App.css webui/src/App.jsx webui/src/NavIcon.jsx
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(ui): variables rem y navegacion por iconos para pantallas diminutas"
```

---

### Task 10: Layout 480×320 — contenido y modales

**Files:**
- Modify: `webui/src/App.css` (resto de reglas: `.block-grid`, contenedores, modales)

- [ ] **Step 1: Sustituir los `px` de layout por unidades relativas**

Recorrer los 72 `px` de `App.css`. Criterio:
- Anchos/altos/paddings/gaps → `rem` o `var(--u)`.
- Bordes de 1px → `0.0625rem` (aceptable dejarlos si el linter del proyecto no se queja, pero preferir la conversión).
- `media (max-width: 560px)` (`:86`) → `34rem`.

- [ ] **Step 2: Una sola columna y scroll vertical**

```css
/* A 480x320 no hay sitio para dos columnas ni para ver varias fases a la vez:
   una columna, un paso por pantalla y scroll. El header se queda fijo para no
   perder el contexto de en que pestana estas. */
@media (max-width: 34rem), (max-height: 26rem) {
  .block-grid { grid-template-columns: 1fr; gap: var(--u); }
  .app-header { position: sticky; top: 0; z-index: 2; background: var(--bg); }
  .app-main { padding: var(--u); overflow-y: auto; }
  button, .glass-input, input, select { min-height: var(--toque-min); }
}
```

- [ ] **Step 3: Modales a pantalla completa**

```css
/* Un modal centrado con margenes en 480x320 deja una ventana util ridicula:
   a esta resolucion ocupan todo. */
@media (max-width: 34rem), (max-height: 26rem) {
  .modal, .picker {
    position: fixed; inset: 0;
    width: 100%; height: 100%;
    max-width: none; max-height: none;
    border-radius: 0;
    display: flex; flex-direction: column;
  }
  .picker-lista { flex: 1; overflow-y: auto; }
  .picker-fila { min-height: var(--toque-min); width: 100%; text-align: left; }
}
```

(Ajustar los selectores reales a los que usen `ProgressModal.jsx`, `PreflightModal.jsx` y `UpdateModal.jsx` — leerlos antes de escribir la regla.)

- [ ] **Step 4: Verificar a 480×320 con las tres pantallas**

Con el servidor levantado y Chromium a 480×320, recorrer: Organizar (con un run de prueba corto), Subir al bucket (login + selección de inspección), Configuración. Ninguna debe producir scroll **horizontal**.

- [ ] **Step 5: Verificar que Windows no cambió**

Run: `cd webui && npm test && npm run lint && npm run build`
Expected: PASS. Las reglas nuevas van todas dentro de media queries, así que a 1100×760 el aspecto es el de siempre.

- [ ] **Step 6: Commit**

```bash
git add webui/src/App.css
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "feat(ui): layout de una columna y modales a pantalla completa en 480x320"
```

---

### Task 11: Arranque en la Raspberry Pi y documentación

**Files:**
- Create: `scripts/raspi/arrancar.sh`
- Create: `scripts/raspi/README.md`

- [ ] **Step 1: Script de arranque**

Crear `scripts/raspi/arrancar.sh`:

```bash
#!/usr/bin/env bash
# Arranca el ATOM Organizer en la Raspberry Pi: servidor HTTP local + Chromium
# a pantalla completa. No usa pywebview/Qt (no hay wheel para ARM64).
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PUERTO="${ATOM_PUERTO:-8765}"

cd "$RAIZ"
if [ ! -d webui/dist ]; then
  echo "Falta webui/dist. Compila el front antes: cd webui && npm ci && npm run build" >&2
  exit 1
fi

venv/bin/python app_webview.py --server --port "$PUERTO" &
SERVIDOR=$!
trap 'kill $SERVIDOR 2>/dev/null || true' EXIT

# Esperar a que el puerto responda antes de abrir el navegador.
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PUERTO/" >/dev/null; then break; fi
  sleep 0.25
done

NAVEGADOR="$(command -v chromium || command -v chromium-browser)"
"$NAVEGADOR" --kiosk --app="http://127.0.0.1:$PUERTO" \
             --disable-features=TranslateUI --noerrdialogs --incognito

wait $SERVIDOR
```

```bash
chmod +x scripts/raspi/arrancar.sh
```

- [ ] **Step 2: README de instalación**

Crear `scripts/raspi/README.md` con: clonar el repo, `python3 -m venv venv`, instalar `requirements-server.txt` (más lo que la Task 1 haya descubierto sobre `pyexiv2`), compilar el front una vez, y ejecutar `scripts/raspi/arrancar.sh`. Incluir la nota de que el autoupdate en Linux solo avisa y no instala (`atom_core/updater.py:15-16, 100`), así que actualizar es `git pull` + rebuild del front.

- [ ] **Step 3: Prueba end-to-end en la Pi con Rebeca**

Verificar en la Pi real: arranca, se ve entero a 480×320, login de Google con su cuenta `@aerotools.es`, elegir carpeta con el explorador, **organizar un lote pequeño** (10-20 fotos, no un vuelo entero), y **subir a `gs://datos_para_organizar`**. Anotar tiempos.

- [ ] **Step 4: Commit**

```bash
git add scripts/raspi/
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "docs(raspi): script de arranque y guia de instalacion en Raspberry Pi"
```

---

## Cierre

- [ ] `python -m pytest tests/ -v` en verde (749 de partida + ~24 nuevos).
- [ ] `cd webui && npm test && npm run lint && npm run build` en verde.
- [ ] Comprobar que en Windows la app sigue abriendo y organizando igual (regresión manual, es el entorno de producción).
- [ ] Documentar la sesión (skill `documentar-sesion`): Diario del día + nota `ATOM Organizer` en el Atlas.
- [ ] Marcar la tarea 3809 (`atom-tareas hecha 3809`) cuando Rebeca lo haya probado.
- [ ] **NO hacer push ni tag sin OK expreso de Rodrigo.**
