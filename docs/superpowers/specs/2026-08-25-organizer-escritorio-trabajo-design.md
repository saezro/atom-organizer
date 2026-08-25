# Organizer escritorio — un trabajo, tres destinos
2026-08-25

## Problema
- 5 tabs, selección de carpeta/inspección/estadillo duplicada en `OrganizarScreen` y `BucketScreen`.
- Dos operaciones síncronas congelan la UI: `detect_suffixes` y `cloud_prepare`.
- El operario necesita elegir libremente entre organizar en local o subir a la nube.

## Decisiones cerradas
1. Se soportan AMBOS caminos (local y cloud); el operario elige. Descartado cloud-first puro y solo-local.
2. Navegación pasa de 5 tabs a `Trabajo` | `Herramientas` | ⚙.
3. El Job de Cloud Run lo dispara la Suite vía API+PAT, nunca el Organizer (regla de la casa: API+PAT, no credenciales de Cloud Run en el PC del operario, no polling desde el Organizer).

## Navegación
- `NAV` actual: `webui/src/App.jsx:23-28` (`organizar`, `bucket`, `aerotools`, `otros`, `config`).
- `Trabajo` (nuevo): sustituye a `OrganizarScreen` (`webui/src/App.jsx:714-863`) + `BucketScreen` (`webui/src/App.jsx:864-1667`), estado único (carpeta, inspección, estadillo, sufijos) y un solo `pickFolder`.
- `Herramientas`: agrupa las pantallas hoy registradas como `aerotools`/`otros` en `NAV` (`webui/src/App.jsx:26-27`). Sin cambios de lógica.
- ⚙: `ConfigScreen` (`webui/src/App.jsx:1668-1842`), sin cambios.

## Los tres destinos
| Destino | Qué hace |
|---|---|
| Organizar aquí | pide carpeta final → `run_task('split_images')` (`app_webview.py:1771`, igual que hoy) |
| Subir al bucket | `cloud_prepare` (`app_webview.py:1094`) + `cloud_upload` (`app_webview.py:1143`), igual que hoy |
| Subir y organizar en la nube | `cloud_upload` (`app_webview.py:1143`) + aviso a la Suite por API+PAT |

## Descomposición de componentes
| Pieza nueva | Sale de |
|---|---|
| `PasoCarpeta` | `FileField`/`pickOrigen`/`pickDestino` de `OrganizarScreen` (`webui/src/App.jsx:714-863`) y `pickFolder` de `BucketScreen` (`webui/src/App.jsx:864-1667`) |
| `PasoInspeccion` | selección de inspección/prefijo de `BucketScreen` (`webui/src/App.jsx:864-1667`) |
| `PasoEstadillo` | `EstadilloField` + flujo `estadillo_subir` de `OrganizarScreen`/`BucketScreen` (`webui/src/App.jsx:1254-1287`) |
| `PanelOrganizar` | cuerpo de `handleRun`/`run_task('split_images')` de `OrganizarScreen` (`webui/src/App.jsx:770-785`) |
| `PanelSubida` | `cloudPrepare`/`subir` de `BucketScreen` (`webui/src/App.jsx:864-1667`) |

## Rendimiento
| Operación | archivo:línea | Problema | Fix |
|---|---|---|---|
| `detect_suffixes` | `app_webview.py:475` delega a `atom_core/suffixes.py:35` (`os.walk` en línea 55) | `os.walk` completo síncrono, congela el modal | mover a hilo (patrón `threading.Thread(...).start()` de `run_task`, `app_webview.py:1780-1783`) + eventos `atom:progress`, "analizando…" cancelable |
| `cloud_prepare` | `app_webview.py:1094-1140`, `build_plan` recorre `rglob("*")` en `atom_core/cloud_upload.py:199` | `rglob` entero síncrono al elegir carpeta, con la pantalla esperando | mismo patrón de hilo + evento, análogo a `cloud_upload` (`app_webview.py:1143`, ya emite `atom:cloud` vía `dispatch`, `app_webview.py:1763`) |

Patrón de referencia ya usado en el propio `app_webview.py` para operaciones largas: `run_task` (`app_webview.py:1771-1783`, hilo + `atom:progress`) y `cloud_upload`/`atom:cloud` (`app_webview.py:1143`, `1763`).

## Estilo
- Adoptar en escritorio los tokens `--u`/`--radio`/`--fs-*` ya definidos en `webui/src/index.css:31-58` (merge `raspi/modo-servidor`).
- **No hay deuda de `px`**: el "~80 px sueltos" que se asumia al diseñar es falso. Verificado sobre `webui/src` entero: 2 unidades de diseño (`index.css:71-72`, trama de puntos del fondo) y 3 px calculados en runtime desde coordenadas de puntero (`pulsacion.jsx:72-74`, radio de la onda) que **son correctos y no se tocan**. El resto de coincidencias son comentarios o el identificador `pxDeRem`.
- El trabajo de estilo se reduce por tanto a **aplicar los tokens** en los componentes nuevos de `Trabajo`, no a purgar unidades.
- Estética = login de Atom Suite: fondo `#0a0a0a`, naranja `#EE753A`, glass, Space Grotesk. Iconos SVG inline, nunca emoji.

## Multiplataforma
- pywebview corre en Windows y Linux (WebKitGTK); el kiosco de la Pi ya comparte `webui/src/bridge.js`. No se crea UI nueva.
- `pi.html` sigue siendo entry aparte del kiosco.

## Fuera de alcance
- Blocker de los `.TMC` (ThermoViewer solo Windows).
- Quitar el organizado local.
- Sync de plantas Drive↔disco.

## Invariantes a preservar
- `estadillo_subir` no tiene mutex propio (a diferencia de `cloud_upload`); el front compensa con el flag síncrono `estadSubiendo` antes del `await api.estadilloSubir(...)` (`webui/src/App.jsx:900`, uso en `1254-1287`, guardas en `1326-1334` y `1487-1493`). El rediseño debe mantener esa protección.
- Tests que deben seguir verdes: vitest en `webui/` (204 pasando) y pytest (968 passed). 1 fallo preexistente aceptado: `test_dji_resiliencia_parallel::test_raw_truncado_no_tumba_el_lote`.
- Principio de réplica: NO rewrite. Descomponer, no reescribir; cero funcionalidad perdida; cualquier mejora es incremental sobre el comportamiento actual.
