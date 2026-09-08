# LEDGER — Mejoras post v3.4.83 (2026-09-08)

## Clarified
- **Colisión de destino**: SOLO AVISAR. No renombrar ni abortar; se sigue pisando como hoy, pero se detecta y se reporta (log + resumen final).
- **Auto-escalado workers**: techo por RAM libre REAL en el momento de subir (~600 MB/worker, con margen), en vez de `arranque*2` a ciegas.
- Alcance = los 5 items priorizados. Fuera: `manifiesto.cerrar()` (conexiones de hilos) y "qué fase tardó más" (cosméticos).
- NO commit / push / release / deploy sin OK explícito de Rodrigo.

## Items
- [x] **1. Aviso de colisión de `ruta_salida_original`** — hoy `manifiesto.py:32` solo tiene UNIQUE en `ruta_origen` y `apply.py:611` hace `os.replace` silencioso. Detectar y reportar, sin cambiar la escritura.
- [x] **2. ETA + img/s reales en la UI** — `ProgressModal.jsx:36-41` usa `etaText()` heurístico; consumir `eta_segundos` / `img_por_segundo` que ya llegan por el evento SSE `stats` (`apply.py:157-165`).
- [x] **3. Desglose del índice + fix NaN** — `ProgressModal.jsx:21-29` (`statsLine`) ignora `rgb_extra`/`sin_asignar`/`sin_timestamp`/`vuelos` y usa `s.done`, que no existe en el payload de índice → `NaN` en pantalla.
- [x] **4. Techo de workers por RAM libre** — `paralelismo.py:206` (`maximo = arranque*2`) + `_subir` (66-79).
- [x] **5. Test del EXIF en el `*_T.JPG` girado** — `apply.py::_copiar_jpg_destino`; el test actual (`tests/test_apply_termicas.py:316`) usa imagen SIN EXIF, la rama `exif=` no se ejercita.

## Verificación de cierre
- [x] `.venv/bin/python -m pytest -q` en verde (baseline hoy: 1399 passed).
- [x] `npm test` (vitest) en `webui/` en verde.
- [~] Revisión del diff completo por Rodrigo antes de cualquier commit.

## Hallazgos durante el trabajo
- [x] **6. BUG en v3.4.83: la copia girada del `*_T.JPG` pierde el XMP DJI.** Descubierto por el test del item 5 (`tests/test_apply_termicas.py:344`, FAIL real). `apply.py:591-599` arrastra solo `img.info["exif"]` → GPS y `DateTimeOriginal` sobreviven, pero `GimbalYawDegree` (bloque XMP crudo tras el EOI) se pierde ('37.5' → '0'). El camino RGB (`ci.rotate_and_save`) sí lo preserva. Ya está publicado en producción.

## Verificado con R-JPEG real (no solo fixture)
`CALAMOCHA/.../DJI_20260611131401_0039_T.JPG`: XMP NO se duplica (1→1). El XMP real de DJI viene como **APP1 estructurado** y en destino queda **pegado tras EOI** — cambia de forma; la app lo lee igual (`leer_bloque_xmp` busca por texto), un lector externo con parser de segmentos podría no verlo. APP3/4/5 ausentes en destino: comportamiento YA conocido del resave PIL, por eso el giro cuelga de `gen_thumbnails`. Yaw y origen intactos.

## Pendiente de decisión de Rodrigo (no tocado)
- [~] Loggear la excepción real en `exif.py::extraer_bloque_xmp_crudo` (hoy un error de I/O es indistinguible de "no hay XMP").
- [~] `paralelismo.py`: si el sistema subreporta RAM disponible de forma persistente, el techo se queda clavado y nunca sube (regresión de rendimiento, no de corrección).
- [~] `ProgressModal.jsx`: fase de índice detectada por `s.vuelos != null` (frágil si otra fase añade `vuelos`).
