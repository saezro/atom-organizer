# LEDGER — Regresión de rendimiento fase RGB (Organizer 3.4.81)

## Clarified
- **¿Qué versión corrió el organizado lento?** → 3.4.81 (la de hoy, con motor plan→apply + controlador adaptativo).
- **¿Revertir u optimizar?** → Optimizar el motor nuevo (conservar manifiesto/reanudable).
- **Plataforma**: Organizer de Windows (.exe PyInstaller), PC de oficina.
- **Síntoma**: antes TODO el organizado 20-30 min y RGB ~3 min; ahora solo RGB 30 min (~10x).
- **Alcance**: "arréglalas todas" y "optimizado a tope" → también giros/rotación y térmica, no solo RGB.

## Requisitos
- [x] Diagnóstico: causa raíz = `phases.py:1010/1013` llamaba a `aplicar_rgb`/`aplicar_termicas` SIN `controlador` -> camino secuencial (`apply.py:321`). El `ControladorAdaptativo` no se instanciaba en producción.
- [x] Fix causa raíz: controlador POR FASE en `phases.py` (RGB -> ProcessPoolExecutor; térmicas -> ThreadPoolExecutor con `maximo=utils.max_io_workers()`)
- [x] Aforo/controlador: arranca en `utils.workers_para_lote()` (no en 1), techo `arranque*2` (`paralelismo.py:135-141`). Rampa revisándose en auditoría.
- [x] Descartado el doble trabajo: `_TASKS["split_images"]` apunta solo a `organizar_plan_apply` (`organize.py:356`).
- [~] Manifiesto SQLite: 2 commits por imagen — descartado, el benchmark demostró que no es el cuello de botella. Original: 2 commits por imagen (`manifiesto.py:128-144`), sin batching. Revisar además seguridad de hilos de la conexión (el apply paralelo la usa desde varios hilos)
- [x] Pool único por fase confirmado (`apply.py:351`, `apply.py:576`, `indice.py:406`). El patrón "pool por carpeta" solo vive en `pipeline.py` = código muerto.
- [~] Escaneo de gimbal-yaw serie: NO aplica, es del motor viejo (`pipeline.py:2034-2045`), código muerto en `organizar_plan_apply`. El índice vivo ya lee EXIF con `ThreadPoolExecutor` (`indice.py:406`).
- [~] Rotación: ya va con decode único dentro del motor nuevo (`apply.py:130-206`); el camino viejo con doble decode es código muerto.
- [x] Logging de workers por fase en el motor NUEVO — `paralelismo._trazar_ventana` emite `[paralelismo] RGB|Termicas workers X->Y ... cpu_ociosa/ram` una línea por ventana (5 s) al log de la app
- [x] Tests: la suite sigue verde (1375 passed)
- [x] Medición antes/después: KL19, RGB de ~1,4 a ~4,2 img/s (ver «Validación en Windows»)
- [x] Release v3.4.82 validada en Windows; v3.4.83 publicada 2026-09-08 (pendiente su validación → tarea 3871)

## Fuera de alcance (otras tareas ya abiertas)
- #3869 output deja `.organizado` visible + agrupa mal en 2 vuelos
- #3870 autoupdater no abre la app tras actualizar
- Cablear métricas de progreso en `webui/src/ProgressModal.jsx` (backend ya hecho)

- [x] Aplicados los hallazgos de la revisión completa del motor nuevo (detalle ítem a ítem abajo) (auditorías en curso: indice.py / apply.py / manifiesto+cierre+paralelismo)

## Hallazgos de la revisión completa (2026-09-08) y su estado

### ✅ Aplicados en esta sesión
- [x] `phases.py:1010/1013` — controlador por fase (causa raíz del 10x)
- [x] `indice.py` `_construir_fila` — pasa `timestamp=dato.timestamp` a `nombre_destino`; `pipeline.nombre_destino` acepta `timestamp=None`. Elimina una SEGUNDA pasada EXIF completa, en serie, sobre todo el dataset.
- [x] `indice.py` `construir_indice` — pool de hilos dimensionado con `utils.max_io_workers()` (era `workers_para_lote()`, para procesos)

### ⏳ Pendientes, por prioridad
- [x] 🔴 `apply.py` `_al_terminar` (RGB) — todo el cuerpo en try/finally: `aforo.ajustar()`/`aforo.liberar()` ahora se ejecutan pase lo que pase. **APLICADO**.
- [x] (original) 🔴 `apply.py:338-349` (`_al_terminar`, RGB) — `aforo.ajustar()`/`aforo.liberar()` van FUERA de try/finally: si `_cerrar_fila` lanza (sqlite busy, emit, controlador), el permiso NUNCA vuelve al semáforo. `concurrent.futures` se traga la excepción del callback -> el aforo pierde capacidad en silencio y, acumulando, `aforo.adquirir()` bloquea para siempre = RUN COLGADO o arrastrándose con 1-2 en vuelo. El patrón correcto ya existe en térmicas (`apply.py:566-574`, `_con_aforo`). ES LA PRIORIDAD 1: encaja con "tarda un huevo" en Windows local.
- [x] 🔴 `apply.py` submit RGB — `marcar_en_curso`+`submit` en try/except: si el pool está roto, se cierra la fila como fallida y se devuelve el permiso, sin abortar el lote. Mismo blindaje en térmicas. **APLICADO**.
- [x] (original) 🔴 `apply.py:351-358` — `submit`/`marcar_en_curso` sin try/except: un `BrokenProcessPool` (worker muerto por RAM) aborta el lote entero y deja la fila 'en_curso' sin liberar aforo.
- [x] 🟠 térmicas — `resultado`/`completadas`/`convertidas` bajo `lock_contadores`; `resultado` de RGB bajo `lock_resultado`. **APLICADO**.
- [x] (original) 🟠 `apply.py:536-554` (térmicas) — contadores `resultado["fallido"]`/`completadas["n"]` mutados sin lock desde varios hilos.
- [x] 🟠 `progress_bar` — nuevo `_EmisorProgreso`: solo emite cuando cambia el porcentaje ENTERO (miles de señales Qt -> 100 como mucho). **APLICADO**.
- [x] (original) 🟠 `apply.py:330,349,574` — `progress_bar.emit` por CADA imagen, sin throttling (las stats sí lo tienen).
- [x] 🔴 `paralelismo.py` — `_lector_recursos_psutil` con try/except (devuelve `_RECURSOS_DESCONOCIDOS` = CPU ociosa 0 / RAM infinita: lectura neutra, el controlador sigue guiándose por throughput) y `revisar()` blindado ante un lector que lance. **APLICADO**.
- [x] (original) 🔴 `paralelismo.py:104-112,173` — `import psutil` sin `try/except`: si falta en el .exe congelado, tumba la fase entera a mitad de run. `utils.py:1289-1302` ya tiene el patrón defensivo a copiar.
- [x] 🔴 `cierre.verificar` — las comprobaciones existe+tamaño van a un `ThreadPoolExecutor` (`utils.max_io_workers()`); `executor.map` conserva el orden, así que la lista de problemas es idéntica. **APLICADO**.
- [x] (original) 🔴 `cierre.py:205-220` — `verificar()` hace `existe_ruta`+`tamano_de` por fichero, secuencial (2 round-trips por ruta, hasta 3 rutas por fila). En GCS/SMB puede tardar más que el propio apply. Paralelizar con ThreadPoolExecutor y/o fusionar en una sola lectura de metadata.
- [~] (NO aplica al caso de Rodrigo: usa LOCAL en Windows) 🔴 `indice.py:140-165` — con origen `gs://` solo el GPS pasa por `abrir_para_lectura`; timestamp/modelo/yaw fallan en silencio -> TODAS las imágenes a SIN_ORDENAR. Bloquea el rumbo cloud-first. (Corrección, no rendimiento.)
- [~] DESCARTADO por medición 🟠 `manifiesto.py:128-145` — 2 commits por imagen. Batching con `executemany` cada N filas. Coste: se pierde la granularidad de 'en_curso' (benigno: esas filas se reprocesan).
- [x] 🟠 `cierre.py` escaneos — nuevo `_agrupar_por_vuelo(filas)`: agrupa en memoria lo ya leído. `verificar` pasa de 2 escaneos completos + N consultas por vuelo a 1 escaneo; `_emitir_csv_criterio`, de 1+N a 1. **APLICADO**.
- [x] (original) 🟠 `cierre.py:44,92,190,222` — el manifiesto se recorre 2-3 veces enteras + consultas por vuelo repetidas.
- [~] movido a `LEDGER-organizado-residuales.md`: 🟠 `paralelismo.py:135-139` + `apply.py:351` — `maximo = arranque*2` puede pasarse del presupuesto de RAM (la regla 1 solo corrige a posteriori) y en Windows cada worker de proceso relanza el bootloader de PyInstaller. Validar con medición real.
- [~] movido a `LEDGER-organizado-residuales.md`: 🟠 `indice.py:332` — sin detección de colisión de `ruta_salida_original`; el manifiesto solo tiene UNIQUE en `ruta_origen`, así que dos imágenes distintas pueden pisarse en destino.
- [~] movido a `LEDGER-organizado-residuales.md`: 🟢 `manifiesto.py:177-181` — conexiones de hilos de pool sin `close()` explícito (se confía en el GC).
- [x] Logging de workers por fase: línea `[paralelismo] <fase>: N proceso(s)/hilo(s) en vuelo (máximo M), T imagen(es).` al arrancar el camino paralelo de RGB y de térmicas. **APLICADO**.

### ✅ Descartado (no tocar)
- Thread-safety de sqlite3: CORRECTO por diseño (`manifiesto.py:83-97`, `threading.local()`, una conexión por hilo + WAL + busy_timeout).
- `_AforoDinamico`: lógica de encoger/crecer simétrica y sin fugas (`apply.py:239-255`).
- Todo `pipeline.py` (split/compress/rotación/yaw en serie/pool por carpeta): CÓDIGO MUERTO en `organizar_plan_apply`. No optimizar.
- Doble trabajo viejo+nuevo: no ocurre (`organize.py:356`).
- Primer `cpu_percent()`=0.0: inofensivo (la regla 2 corta antes de usarlo).

### Estado de tests
- Tras el fix de la causa raíz: **1369 passed**, 0 fallos.
- Tras los fixes del índice: pendiente (tester lanzado, sin resultado todavía).


## Medición: batching del manifiesto — DESCARTADO (2026-09-08)
Benchmark real sobre el `Manifiesto` (SQLite WAL, `synchronous=NORMAL`), 2.000 filas
con los 2 commits por imagen que hace el apply (`marcar_en_curso` + `marcar_hecha`):

    2 commits x 2000 filas: 1,12 s -> 0,560 ms/imagen

Sobre un run de 5.000 imágenes son ~3 s. Aun multiplicando x10 el coste por el
antivirus de Windows serían ~28 s de un run de 20-30 min: **no es el cuello de
botella**. El batching con `executemany` obligaba a perder la granularidad de
'en_curso' (crash = filas reprocesadas) a cambio de nada medible, así que se
descarta. Si algún día el manifiesto vive en red, reabrir la decisión.

## Cierre 2026-09-08
- [x] Tests de regresión (3): test_paralelismo_adaptativo.py:147 y :164, test_apply_rgb.py:349.
- [x] Telemetría: `_AforoDinamico` integra tareas en vuelo -> `[paralelismo] <fase>: N imgs en T — X img/s — media M en vuelo (pico P, máximo Q) = Z % del potencial`; `phases.py` cierra con `[tiempos] Organizado completo: ... (Índice/RGB/Térmicas/Cierre)`.
- [x] Medición t_img RGB 48 MP (venv, VM 4 vCPU): 1,3 s sin giro / 2,3 s con giro. 2.500 imgs en serie ≈ 29 min = lo que vio Rodrigo. Con 7-15 workers → 3-6 min.
- [x] Commit conjunto (incluye métricas de progreso de la otra sesión, OK de Rodrigo "dale a todo"): d312701. Tag v3.4.82 pusheado.
- [~] aplazado: Validación de Rodrigo en Windows con las nuevas líneas de log — está en casa, no tiene acceso al PC de oficina; requiere release nueva (pendiente OK)

## Validación en Windows (2026-09-08, v3.4.82)
- [x] Regresión resuelta: KL19 1012 img · RGB 4 min (~4,2 img/s vs ~1,4 en serie). Run total 10 min 45 s. Veredicto: mixto (disco+CPU), CPU media 61 %.
- [x] ProgressModal: ETA por fase, sin dato duplicado ("170 de 1012 img · 170 RGB"), "…"/"escaneando…" en fase sin progreso.
- [x] cierre.verificar() ignora filas `fallido` → arreglado (`cierre.py:233-243` + test `test_las_filas_fallidas_se_reportan`)
- [x] Rotación: `_ContadorRotacion` acumulado del run, compartido RGB↔térmicas desde `phases.py`; emitido en `_emitir_stats_apply`
- [x] UI congelada al terminar: quitado `will-change`/`contain:paint` de `.pm-card` (App.css:413)
- [x] Release v3.4.83 publicada (OK de Rodrigo, 2026-09-08): .exe + AppImage + imagen Cloud Run.

## Sesión 2026-09-08 (tarde) — aceleración del cuello de térmicas

- [x] Traza `[paralelismo]` por ventana y fase (`paralelismo._trazar_ventana`, etiquetas RGB/Termicas)
- [x] Rampa geométrica en `decidir_trabajadores` (`_subir`): duplica durante la rampa inicial, +1 tras la primera bajada. Antes: +1 cada 5 s → 25 ventanas (125 s) para ir de 7 a 32
- [x] `_bajar`: desplome >25% corta a la mitad (contrapartida obligatoria del salto geométrico)
- [x] `utils.arranque_io()` = 2×núcleos, inyectado en las térmicas vía `ControladorAdaptativo(arranque=...)`. Antes arrancaban con la fórmula RAM-bound de RGB (~7 con techo 32)
- [x] Suite verde: 1380 passed
- [x] Medido en Windows con las trazas (KL19, v3.4.82). Falta re-medir v3.4.83 → tarea 3871

### Hallazgos de paridad motor viejo↔nuevo (auditoría, SIN tocar código)
- [x] RESUELTO (2026-09-08): el `*_T.JPG` vuelve a girarse con el mismo ángulo que su TIFF (`apply._copiar_jpg_destino`), sobre la copia de destino y colgando de `cfg.gen_thumbnails` (`--sin-rotacion` lo desactiva). Tests: `tests/test_giro_jpg_termico.py` + `tests/test_apply_termicas.py`. Texto original: 🔴 el JPG térmico hermano ya NO se gira (`apply.py:548-566` lo copia crudo) mientras el TIFF sí → TIFF girado + `*_T.JPG` apaisado. El motor viejo giraba ambos (`pipeline.py:3241-3279`). Deliberado (no destruir el R-JPEG radiométrico) pero SIN test y divergente del comportamiento histórico
- [~] 🟢 Sin test dedicado del EXIF del `_CROP` rotado (comportamiento verificado por lectura). Original: el `_CROP` rotado ahora conserva EXIF (el viejo lo perdía, `pipeline.py:846`). Mejora, sin test que la cubra
- [x] (nota de paridad, no era un fix) Recorte y compresión: mismo código reutilizado de `pipeline.py`, criterio idéntico. El nuevo hace 1 decode en vez de 2-3 (mejor calidad a igual `quality=`)
- [x] RESUELTO (2026-09-08): premisa FALSA — `raspi/modo-servidor` está 0 commits por delante de `main` (`5c45223` ya incluido). Unificada además la detección de SO (`pipeline`/`rjpeg_a_tiff` → `external_tools._current_os()`), con matriz de plataformas en `tests/test_backend_termico_plataformas.py`. Ver `LEDGER-multiplataforma.md`
