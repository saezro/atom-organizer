# LEDGER — Organizado plan→apply

## Clarified
- **Enfoque A aprobado** (índice → manifiesto persistido → apply de un solo decode → cierre desde manifiesto). B (streaming por vuelo) descartado.
- **Alcance: TODO el organizado**, un único pase origen→destino final. Sin copia de split, sin move de struct, sin intermedios.
- **Sustitución limpia**, sin flag de convivencia con el motor viejo.
- **El origen se conserva intacto.**
- **Disco: cualquier caso** (mismo/distinto, SSD/HDD) → el paralelismo se auto-ajusta midiendo, sin constantes que asuman SSD.
- **Los hilos/procesos deben ser dinámicos y automáticos**: adaptarse al hardware y a los datos del momento, en caliente, sin que Rodrigo toque nada.
- **Prioridad nº1 de Rodrigo: la corrección de la salida.** TIFF siempre bien girados y con TODOS sus metadatos; RGB siempre comprimida y recortada; todo girado como debe. La velocidad es secundaria a esto.
- Secciones de diseño aprobadas en bloque ("dale a todo").
- Datos de KL19 (nº imágenes, desglose por fase) y log de Inno del autoupdater: pendientes, no bloquean el diseño (solo la estimación de ganancia).
- **Calidad al girar una RGB (2026-09-08)**: se replica el criterio del motor viejo (`_ROTATION_JPEG_QUALITY = 40`), NO se mejora. Decisión expresa de Rodrigo.

## Requisitos

### Corrección (bloqueantes)
- [x] Rotación: consenso de yaw por carpeta `PBx_Vy` resuelto en el índice; el apply gira UNA sola vez con el ángulo definitivo.
- [x] TIFF girados con el mismo criterio que su JPG (hoy la fase 7 depende del CSV de la 6; en el nuevo motor ambos leen el manifiesto).
- [x] TIFF con todos sus metadatos: EXIF/XMP copiados del origen (GPS, timestamp, yaw, modelo) vía `exiftool -stay_open`.
- [x] RGB: compresión + recorte + rotación aplicados en un ÚNICO decode, escritos directos a la carpeta final (original y `_CROP`).
- [x] Nombre de salida idéntico al motor viejo: `AAAAMMDD_HHMMSS_<original>` vía `Pipeline.nombre_destino`. (Corregido 2026-09-08: NO es numeración secuencial; eso es solo la clave del CSV de criterio de giro.)
- [~] aplazado a Tarea 8 (validación contra planta real): se conservan las 12 dependencias globales inventariadas (yaw, % recorte por modelo, ventana horaria, colisiones PB+vuelo, cuadres de conteo).

### Motor
- [x] Manifiesto persistido en SQLite (WAL), una fila por imagen, con estado por fila.
- [x] Índice: una sola pasada EXIF/XMP con pool de I/O + cruce con estadillo; resuelve destino, yaw, % recorte, nombre nuevo, si comprime.
- [x] Apply: pool CPU (procesos) para RGB, pool I/O (hilos) para térmicas (`dji_irp`/exiftool son procesos externos).
- [x] **Pools dinámicos y adaptativos en caliente**: el tamaño de cada pool se ajusta solo durante todo el run según hardware (núcleos, RAM libre) y datos del momento (throughput MB/s medido, profundidad de cola, CPU ociosa), no solo al arrancar. Cero constantes fijas, cero configuración del usuario.
- [x] Errores aislados por imagen: la fila queda `failed` con motivo, el run no aborta, resumen final + reintento selectivo.
- [x] Imágenes fuera del estadillo → `unassigned` → SIN_ORDENAR en el MISMO apply (fuera el barrido posterior).
- [x] Reanudación: re-run salta las filas `done` verificadas contra disco.
- [x] CSVs (`meta`, `location`, `_Videofiles`) emitidos desde el manifiesto.
- [x] Verificaciones cruzadas reescritas como manifiesto-vs-disco.

### Validación
- [x] `tools/comparar_organizados.py` + tests (commit `7ccc6de`): árbol, TIFF byte a byte, JPG por píxeles con tolerancia de recompresión + EXIF normalizado, CSV fila a fila.
- [ ] Comparación motor viejo vs nuevo sobre una planta real: árbol idéntico (rutas + nombres) y contenido equivalente (TIFF radiométrico byte a byte; RGB por hash de píxeles + EXIF normalizado).
- [x] `pytest` verde (1356 passed) y `cd webui && npx vitest run` verde (311 tests; 2 dan timeout al correr la suite entera bajo carga y pasan aislados — flaky, no regresión).
- [x] Sin regresión en el modal de progreso: los 4 nombres de fase (`Índice`, `Imágenes RGB`, `Conversión térmica`, `Cierre`) coinciden carácter a carácter entre los `emit` de cada fase, `_SPLIT_PHASES` y `MedidorRecursos.abrir_fase` (auditado). Falta verlo en vivo (Tarea 8).

### Entregables
- [x] Spec en `docs/superpowers/specs/2026-09-08-organizado-plan-apply-design.md`, commiteada y revisada por Rodrigo.
- [x] Plan con `superpowers:writing-plans`.
- [x] Ejecución subagent-driven.
- [ ] NO deploy a main sin OK expreso de Rodrigo.

## Hallazgos de la auditoría del cableado (2026-09-08, Tarea 7)

Descubiertos por `code-auditor` sobre el diff sin commitear y **confirmados leyendo el código
por el hilo base**. La suite estaba verde (1354 passed): ninguno de los dos lo capturaban los
tests.

### Clarified (decisión de Rodrigo, 2026-09-08)
- **Sharding**: poner un guard que RECHACE `shard_count > 1` hasta que la Tarea 8 implemente el
  reparto. Descartado implementarlo ahora (retrasaría la release) y descartado sacarlo roto.
- **Reanudación**: arreglarla ANTES de la release 3.4.81, no dejarla para la Tarea 8.

### Requisitos
- [x] **Guard de sharding**: `organizar_plan_apply` no lee `self.shard_index`/`shard_count`, que
  `organize.py:743-744` sigue inyectando y `organize_cli.py:260-291` expone (`--shard-count`, y
  `sharding.shard_desde_entorno()` en Cloud Run). Con N shards, las N tareas harían el organizado
  ENTERO sobre el mismo `output_folder`: trabajo ×N y carreras de escritura. Debe abortar con
  error accionable, calcando el guard hermano de `organize.py:588-596`.
- [x] **Manifiesto persistente**: hoy vive en `tempfile.mkdtemp()` (ruta aleatoria) y se borra en
  un `finally` incondicional → `reabrir_huerfanas()` es código muerto por construcción. Pasa a
  `<output_folder>/.organizado/manifiesto.db` y solo se borra tras un `verificar()` limpio.
- [x] `.organizado/` NO puede acabar en el árbol entregado ni contar como contenido inesperado en
  las verificaciones de `cierre.py`.
- [x] Tests que cubran los tres puntos: guard con `shard_count > 1`, reanudación real tras run
  interrumpido, y ausencia de `.organizado/` tras run limpio.

### Anotado, NO bloqueante para la release
- [~] aplazado: la GUI Qt (`gui.py:2266-2268`) sigue llamando al motor VIEJO `split_images`
  directo, sin pasar por `organize.run_task`. Solo headless (CLI/webui) usa el motor nuevo. Es
  coherente con validar en headless antes de tocar la GUI, pero hay que decidirlo en la Tarea 8.
- [~] aplazado: el árbol real del organizado bueno de KL19 es `RGB/PB{1,2}/PB{1,2}_V1/` y
  `TERMICA/...` (dos árboles top-level), NO `PBx_Vy/RGB` como asume el plan, y sus `CSVs/` no
  tienen `_criterio/` ni `SIN_ORDENAR`. Revisar antes de dar por bueno el diff de la Tarea 8.
