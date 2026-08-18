# ATOM Organizer en Raspberry Pi — diseño

Fecha: 2026-08-18
Rama: `fase2/estadillo-manifest`
Estado: diseño aprobado, pendiente de plan de implementación

## Objetivo

Rebeca debe poder usar el ATOM Organizer **completo** desde una Raspberry Pi
(ARM64, Linux) con una pantalla táctil de **480×320**: organizar en local y
subir al bucket, navegando con el dedo.

No es un modo recortado. Toda la funcionalidad actual debe estar accesible.

## Restricciones dadas

- Hardware: Raspberry Pi 4/5, ARM64, pantalla táctil 480×320 (landscape).
- Alcance: flujo completo (organizar en local + subir), no solo subida.
- Bucket: el mismo que usa Rodrigo, `gs://datos_para_organizar`. **No** se crea
  bucket de prueba; mientras todo sea fase de pruebas, la subida de Rebeca es
  idéntica a la de Rodrigo. `BUCKET_DATOS` (`atom_core/cloud_config.py:33`) NO
  se toca.
- Auth: la que ya hay — OAuth de cuenta Google forzada a `@aerotools.es`
  (`atom_core/cloud_config.py:38`). Rebeca entra con su cuenta; no se
  distribuyen credenciales a la Pi.
- Windows no debe romperse. Es el entorno de producción actual del Organizer.

## Problema central

La AppImage de release es x86_64 y depende de Qt: `pywebview==6.2.1` con backend
Qt sobre `PySide6==6.4.2` (`requirements-webview.txt`). PySide6 6.4.2 es de
finales de 2022 y no tiene wheel `aarch64` fiable; QtWebEngine en ARM es el punto
más probable de fallo irrecuperable. Cualquier camino que conserve pywebview en
la Pi (AppImage ARM, o `pip install` directo en la Pi) hereda ese problema.

Además, la UI asume 1100×760 con `min_size=(900, 600)` (`app_webview.py:1087`) y
tiene un solo media query en todo el proyecto, `max-width: 560px`
(`webui/src/App.css:86`). En 480×320 hoy no cabe.

## Hallazgo que habilita la solución

El acoplamiento a pywebview es mínimo y está encapsulado:

- **Frontend**: `webui/src/bridge.js` es el ÚNICO archivo que toca
  `window.pywebview` (6 referencias). Los 26 call sites de los componentes
  (`App.jsx`, `TaskBlock.jsx`, `EstadilloField.jsx`, `UpdateModal.jsx`) importan
  `{ api }` de ahí. Los eventos push se consumen como `CustomEvent` vía
  `onProgress`/`onUpdate`/`onCloud`, también encapsulados en `bridge.js`.
- **Backend**: 5 puntos atados a pywebview, todos en `app_webview.py`:
  - `evaluate_js`: `:376` (`atom:update`), `:927` (`atom:cloud`),
    `:1040` (`atom:progress`)
  - `create_file_dialog`: `:221` (`pick_folder`), `:234` (`pick_file`)
  - `bind_window` (`:132`) guarda la referencia a la ventana.
  - No hay handlers de `window.closing`/`closed`/`loaded`/`shown`.

La clase `Api` (`app_webview.py:108`) expone 19 métodos al frontend, con mapeo
1:1 en `bridge.js`. Nada de esa lógica está atado a la ventana.

## Diseño

### Pieza 1 — Modo `--server` (elimina Qt de la Raspberry Pi)

El Organizer arranca sin pywebview: sirve `webui/dist` por HTTP en localhost y
Chromium (nativo ARM, incluido en Raspberry Pi OS) la abre a pantalla completa.

- Servidor con `http.server` de la **stdlib**: cero dependencias nuevas. El repo
  ya usa ese módulo para el callback OAuth (`atom_core/google_auth.py:339`).
- Un endpoint genérico `POST /api/<metodo>` que despacha por reflexión sobre la
  misma instancia de `Api`, con allowlist explícita de los 19 métodos públicos.
  No se escriben 19 handlers y **`Api` no se modifica**.
- Los 3 `evaluate_js` se sustituyen por SSE en `GET /events`. Como `bridge.js` ya
  traduce todo a `CustomEvent`, los componentes no cambian.
- `bridge.js` detecta el transporte: si existe `window.pywebview`, funciona como
  hoy (Windows intacto); si no, fetch + SSE.
- Bind solo a `127.0.0.1` por defecto. Exponer en LAN (para abrir desde móvil o
  portátil) queda como flag opt-in, no default.

**Hueco a cubrir**: sin Qt no hay `create_file_dialog`. Se añade un explorador de
carpetas dentro de la webui, apoyado en un método nuevo `list_dir` en `Api`. Se
considera mejora y no parche: un diálogo nativo GTK/Qt en 480×320 manejado con el
dedo sería inusable de todas formas.

### Pieza 2 — UI a 480×320

Todo accesible, no menos funcionalidad:

- Quitar/bajar `min_size=(900, 600)` (`app_webview.py:1087`).
- Rehacer el layout de `webui/src/App.css` en `rem`/`vh`/`vw` — **nunca `px`** —
  con breakpoints reales. Una columna, scroll vertical, un paso por pantalla.
- Las 5 pestañas de texto ("Organizar / SUBIR AL BUCKET / AEROTOOLS / OTROS
  EQUIPOS / CONFIGURACIÓN") no caben en 480 de ancho: pasan a barra de iconos
  con la etiqueta de la pestaña activa visible. Los iconos van como **SVG
  inline**, NO con `react-icons`: `webui/package.json` solo depende de `react` y
  `react-dom`, y añadir esa librería engordaría el bundle que va empaquetado
  dentro del ejecutable a cambio de cinco iconos.
- Targets táctiles ≥ 2.75rem.
- `ProgressModal`, `PreflightModal` y `UpdateModal` a pantalla completa en esa
  resolución.
- Se respeta el Sistema de Diseño de Atom en lo aplicable: dark-first
  (`#0a0a0a`, ya es el `background_color` de la ventana), naranja `#EE763C` para
  CTAs. `App.css` (959 líneas) no tiene bloque `:root` con variables y usa `px`
  en 72 sitios: el rediseño introduce las variables y erradica los `px` del
  layout.

### Pieza 3 — Dependencias en ARM64

Sin Qt, lo que queda por instalar en la Pi es esencialmente
`requirements-server.txt`: el subset headless que ya corre en el Cloud Run Job
(numpy, Pillow, matplotlib, pyexiv2, tifffile, pandas, openpyxl…).

**Riesgo principal: `pyexiv2==2.8.1`** — binding C++ sobre libexiv2, puede no
traer wheel `aarch64` y exigir compilar contra la libexiv2 del sistema.

Los `pywin32`/`pywin32-ctypes`/`pefile` de `requirements.txt` son solo Windows y
no llevan marcadores de entorno: hay que filtrarlos para que `pip install` no
reviente en Linux.

## Orden de trabajo (verificar antes de invertir)

1. **Primero**: `pip install` del subset headless en la Pi real y comprobar
   `pyexiv2`. Si eso no pasa, el resto del diseño no importa.
2. Modo `--server` + transporte dual en `bridge.js`, validado en escritorio Linux
   (misma ruta de código, sin la incógnita ARM).
3. Explorador de carpetas en la webui (sustituye a los diálogos nativos).
4. Rediseño de layout a 480×320.
5. Prueba end-to-end en la Pi con Rebeca: organizar y subir a
   `gs://datos_para_organizar`.

## Riesgos aceptados

- **Rendimiento**: organizar miles de fotos en una Pi 4 (Pillow + exiv2 +
  matplotlib) será notablemente más lento que en un portátil. Funcional, no
  rápido. Aceptado explícitamente.
- **Densidad de información**: a 480×320 se ve un paso por pantalla, con scroll.
  No se verán cinco cosas a la vez. Aceptado explícitamente.
- `pyexiv2` en aarch64 sigue sin verificar; es el único bloqueante duro
  identificado.

## No incluido (YAGNI)

- Bucket de prueba separado y `BUCKET_DATOS` configurable por env.
- Build de AppImage ARM64 en `release.yml` (hereda el problema de Qt).
- Autoupdate en la Pi: en Linux el updater solo avisa, no instala
  (`atom_core/updater.py:15-16, 100`). Se deja igual.
- Tocar `gui.py` (GUI Qt legacy, fuera de este flujo).
