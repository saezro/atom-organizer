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
- [ ] Rotación: consenso de yaw por carpeta `PBx_Vy` resuelto en el índice; el apply gira UNA sola vez con el ángulo definitivo.
- [ ] TIFF girados con el mismo criterio que su JPG (hoy la fase 7 depende del CSV de la 6; en el nuevo motor ambos leen el manifiesto).
- [ ] TIFF con todos sus metadatos: EXIF/XMP copiados del origen (GPS, timestamp, yaw, modelo) vía `exiftool -stay_open`.
- [ ] RGB: compresión + recorte + rotación aplicados en un ÚNICO decode, escritos directos a la carpeta final (original y `_CROP`).
- [ ] Nombre de salida idéntico al motor viejo: `AAAAMMDD_HHMMSS_<original>` vía `Pipeline.nombre_destino`. (Corregido 2026-09-08: NO es numeración secuencial; eso es solo la clave del CSV de criterio de giro.)
- [ ] Se conservan las 12 dependencias globales inventariadas (yaw, % recorte por modelo, ventana horaria, colisiones PB+vuelo, cuadres de conteo).

### Motor
- [ ] Manifiesto persistido en SQLite (WAL), una fila por imagen, con estado por fila.
- [ ] Índice: una sola pasada EXIF/XMP con pool de I/O + cruce con estadillo; resuelve destino, yaw, % recorte, nombre nuevo, si comprime.
- [ ] Apply: pool CPU (procesos) para RGB, pool I/O (hilos) para térmicas (`dji_irp`/exiftool son procesos externos).
- [ ] **Pools dinámicos y adaptativos en caliente**: el tamaño de cada pool se ajusta solo durante todo el run según hardware (núcleos, RAM libre) y datos del momento (throughput MB/s medido, profundidad de cola, CPU ociosa), no solo al arrancar. Cero constantes fijas, cero configuración del usuario.
- [ ] Errores aislados por imagen: la fila queda `failed` con motivo, el run no aborta, resumen final + reintento selectivo.
- [ ] Imágenes fuera del estadillo → `unassigned` → SIN_ORDENAR en el MISMO apply (fuera el barrido posterior).
- [ ] Reanudación: re-run salta las filas `done` verificadas contra disco.
- [ ] CSVs (`meta`, `location`, `_Videofiles`) emitidos desde el manifiesto.
- [ ] Verificaciones cruzadas reescritas como manifiesto-vs-disco.

### Validación
- [x] `tools/comparar_organizados.py` + tests (commit `7ccc6de`): árbol, TIFF byte a byte, JPG por píxeles con tolerancia de recompresión + EXIF normalizado, CSV fila a fila.
- [ ] Comparación motor viejo vs nuevo sobre una planta real: árbol idéntico (rutas + nombres) y contenido equivalente (TIFF radiométrico byte a byte; RGB por hash de píxeles + EXIF normalizado).
- [ ] `pytest` verde y `cd webui && npx vitest run` verde.
- [ ] Sin regresión en el modal de progreso (métricas MB/s + CPU por fase de v3.4.80).

### Entregables
- [x] Spec en `docs/superpowers/specs/2026-09-08-organizado-plan-apply-design.md`, commiteada y revisada por Rodrigo.
- [ ] Plan con `superpowers:writing-plans`.
- [ ] Ejecución subagent-driven.
- [ ] NO deploy a main sin OK expreso de Rodrigo.
