# Organizado: motor plan→apply (índice, apply, cierre)
2026-09-08

## Contexto y problema
- Orquestador actual: `atom_core/phases.py:354` (`split_images`), invocado desde `atom_core/organize.py:762`. Orden de fases: `atom_core/organize.py:291-299`.
- 7 fases con ficheros intermedios: split RGB/térmica (`phases.py:413-458`), RGB_EXTRA (`phases.py:461-496`), struct (`phases.py:498-568`), recorte RGB (`phases.py:591-625`), meta/geolocalización (`phases.py:627-660`), rotación (`phases.py:662-712`), TIF (`phases.py:714-777`).
- Coste actual por imagen RGB: ~3 decode+encode completos (compresión, recorte, rotación) + 1 copia byte a byte + 1 move + ~4 pasadas de EXIF/XMP.
- Cuello de botella diagnosticado: `_reflink_or_copy` (`pipeline.py:89-117`) cae siempre a `shutil.copy2` en NTFS/Windows (copia byte a byte); el pool se dimensiona como CPU-bound (`utils.py:1305`) en vez de I/O-bound para una fase que es I/O puro.
- Dependencia frágil concreta: la fase 7 (TIF) depende de que la fase 6 (rotación) haya escrito `CSVs/_criterio/<vuelo>_Videofiles.csv` (`pipeline.py:1971`, lectura en `pipeline.py:3308-3326`) — el JPG y el TIFF de la misma imagen pueden acabar con criterios de giro distintos si se leen en momentos distintos.
- Ninguna de las 12 dependencias globales del organizado necesita píxeles: todas se resuelven con metadatos (EXIF/XMP/estadillo). Esto habilita separar "decidir" (índice) de "escribir" (apply).

## Alcance y no-alcance
- **Alcance**: TODO el organizado, un único pase origen→destino final. Sin copia de split, sin move de struct, sin ficheros intermedios. Sustitución limpia del motor viejo, sin flag de convivencia. El origen se conserva intacto.
- **Fuera de alcance**: diagnóstico del autoupdater (asunto aparte); cualquier estimación de ganancia en tiempo (faltan datos de KL19).

## Arquitectura

### Índice (plan)
- Una sola pasada de EXIF/XMP sobre el origen con pool de I/O; cruce con el estadillo.
- Resuelve EN MEMORIA, sin tocar píxeles: vuelo destino, consenso de yaw de la carpeta `PBx_Vy`, % de recorte según modelo, nombre nuevo (`New Name` secuencial por vuelo), si comprime, y ruta final de cada salida (original, `_CROP`, `.tiff`).
- Produce un manifiesto persistido en disco: una fila por imagen.
- Ventajas: idempotencia, reanudación, plan inspeccionable antes de tocar nada.

### Apply
- Pool sobre el manifiesto. Cada worker abre la imagen UNA sola vez y aplica compresión + recorte + rotación sobre el mismo objeto decodificado, escribiendo el original y su `_CROP` directamente en su carpeta final.
- Térmicas: encadenan `dji_irp` en el mismo paso y copian EXIF/XMP con `exiftool -stay_open`.
- El giro del TIFF y el de su JPG usan el MISMO ángulo del manifiesto: desaparece la dependencia fase 7 → CSV de la fase 6.
- Imágenes que no casan con ninguna ventana horaria del estadillo se marcan `unassigned` en el índice y se escriben en SIN_ORDENAR en el mismo apply; desaparece el barrido posterior secuencial.

### Cierre
- Los CSV (`meta`, `location`, `_Videofiles`) se emiten del manifiesto.
- Las verificaciones cruzadas pasan a ser manifiesto-vs-disco:
  - `jpg_count == tiff_count` por vuelo (hoy `pipeline.py:2446-2556`).
  - `crop_count == non_crop_count` (hoy `pipeline.py:3900-3960`).
  - `csv_lines == image_count` (hoy `exif.py:780-864`).
  - `total_images_number == current_image_number` al cierre (hoy repetido por fase).

## Manifiesto (esquema)
- Formato: SQLite en modo WAL, no JSONL. El apply actualiza estado por fila desde varios procesos concurrentes; JSONL no soporta esas actualizaciones concurrentes sin corromperse ni permite consultar "qué queda pendiente" sin releer todo.
- Columnas mínimas:

| Columna | Contenido |
|---|---|
| id | clave de la fila |
| ruta_origen | path en el origen |
| tipo | RGB / térmica |
| timestamp_exif | de la captura |
| modelo | para % de recorte |
| pb | del estadillo |
| vuelo | asignado por ventana horaria |
| nombre_nuevo | `New Name` secuencial por vuelo |
| angulo_giro | ángulo definitivo (consenso yaw de `PBx_Vy`) |
| pct_recorte | según modelo |
| comprime | sí/no |
| ruta_salida_original | destino final del original |
| ruta_salida_crop | destino final del `_CROP` (si aplica) |
| ruta_salida_tiff | destino final del `.tiff` (si aplica, térmica) |
| estado | `pending` / `running` / `done` / `failed` |
| motivo_fallo | texto libre si `failed` |
| verificacion | hash o tamaño para checar reanudación |

## Paralelismo adaptativo
- Prohibido asumir SSD o fijar constantes: el disco puede ser cualquiera (mismo o distinto para origen y destino, SSD o HDD).

| Etapa | Tipo de pool | Motivo |
|---|---|---|
| Índice | ThreadPool de I/O | lectura EXIF/XMP, I/O puro |
| Apply RGB | ProcessPool CPU-bound | decode/crop/rot/encode |
| Apply térmicas | ThreadPool | `dji_irp` y `exiftool` son procesos externos → espera, no CPU del intérprete |

- Controlador adaptativo en caliente: el tamaño de cada pool se reajusta durante TODO el run, no solo al arrancar.
  - Qué se mide: MB/s medidos, profundidad de cola pendiente, CPU ociosa, latencia por imagen, RAM libre.
  - Cada cuánto: en una ventana periódica corta y recurrente durante todo el run (no solo al inicio).
  - Cómo decide: sube trabajadores mientras el throughput mejore; los recorta en cuanto deja de mejorar o la RAM aprieta. En HDD, más hilos provoca thrashing (latencia por imagen sube sin que suba el throughput) y el controlador debe detectarlo y bajar solo.
  - Límites duros de seguridad: techo por RAM libre (igual que hoy hace `utils.py:1305` para el dimensionado inicial, pero reevaluado en caliente, no solo al arrancar).
  - Cero configuración del usuario, cero constantes mágicas.

## Invariantes de corrección (mandan sobre la velocidad)
Prioridad expresa de Rodrigo: la salida correcta primero; el rendimiento, después.

| Invariante | Cómo lo garantiza el motor nuevo |
|---|---|
| Todo TIFF girado como debe | El ángulo sale del manifiesto, el mismo que usa su JPG. No hay dos lecturas del criterio en momentos distintos |
| Todo TIFF con TODOS sus metadatos | `exiftool -stay_open` copia EXIF/XMP del origen; el cierre verifica por fila que el TIFF existe y trae GPS, timestamp, yaw y modelo |
| Toda RGB comprimida y recortada | Compresión, recorte y giro salen del ÚNICO decode del apply; el cierre verifica `crop_count == non_crop_count` contra el manifiesto |
| Nada se pierde por el camino | Toda fila del manifiesto acaba `done` o `failed` con motivo; un run con `failed` no se da por bueno |

- RGB_EXTRA (hoy `phases.py:461-496`) no es una fase aparte: es una salida más de la misma fila del manifiesto, resuelta en el índice.
- Colisiones PB+Vuelo entre estadillos fusionados (`estadillo_mod.detectar_colisiones_pb_vuelo`, hoy `pipeline.py:1265`) se detectan en el índice y abortan ANTES de escribir nada — hoy se detectan con la escritura ya empezada.
- Corrección gimbal por vecindad temporal (`exif.py:873-910`), OFF por defecto (`organize.py:257`): se resuelve en el índice, donde la vecindad temporal completa ya está en memoria.

## Manejo de errores y reanudación
- Aislado por imagen: un fallo marca la fila `failed` con su motivo y el run continúa.
- Un fallo nunca deja una salida a medias: escritura a fichero temporal y rename atómico al nombre final.
- Al final: resumen y reintento selectivo de las `failed` (misma operación que la reanudación).
- Reanudación: un re-run lee el manifiesto y salta las filas `done` cuya salida se verifica existente en disco; rehace `pending`, `running` huérfanas y `failed`.

## Imágenes fuera del estadillo
- Se marcan `unassigned` en el índice (no casan con ninguna ventana horaria del estadillo, `ventana_horaria_vuelo` hoy en `pipeline.py:1541-1564`).
- Se escriben en SIN_ORDENAR en el MISMO apply, no en un barrido posterior secuencial (hoy `phases.py:557-565` y `178`, que exige struct terminada entera, comentario en `184-196`).

## Emisión de CSV y verificaciones
- `meta` / `location` (hoy `exif.py:1037/1053`) y `_Videofiles` (hoy `pipeline.py:1971`) se emiten desde el manifiesto al cierre, no incrementalmente por fase.
- Verificaciones cruzadas del inventario de dependencias (5, 6, 7, 10) se reescriben como consultas manifiesto-vs-disco en vez de contadores acumulados fase a fase.

## Plan de validación contra el motor viejo
- Correr ambos motores sobre la MISMA planta real, con el mismo estadillo, a dos destinos distintos.
- Árbol: rutas y nombres deben ser idénticos (incluida la numeración `New Name` y SIN_ORDENAR).
- Contenido:
  - TIFF radiométricos: byte a byte.
  - RGB: por hash de los píxeles decodificados + diff de EXIF normalizado (los bytes del JPG pueden diferir por recompresión, los píxeles y los metadatos no).
- CSV (`meta`, `location`, `_Videofiles`): fila a fila.
- Cualquier discrepancia bloquea la sustitución.
- Además: `pytest` verde y `cd webui && npx vitest run` verde; sin regresión en el modal de progreso (métricas MB/s + CPU por fase, añadidas en v3.4.80).

## Riesgos
- La equivalencia byte a byte de las RGB no es alcanzable por recompresión → se valida por píxeles, no por bytes del fichero.
- El consenso de yaw debe reproducir exactamente el criterio actual, incluida la memoización por carpeta (`read_auto_rotate_degree`, `pipeline.py:3308-3326`).
- `exiftool -stay_open` es un proceso de larga vida compartido: hay que decidir cómo se reparte entre workers sin serializar todo el apply.
- El pico de RAM al subir trabajadores es el límite duro del controlador adaptativo.
- Es PROD, lo usan Daniel y el equipo, y no hay flag de convivencia → la validación en planta real es innegociable antes de sustituir.

## Datos pendientes
- Nº de imágenes de KL19 con reparto RGB/térmica y desglose por fase de la captura de v3.4.80: pendiente, no bloquea el diseño, solo la estimación de ganancia en tiempo.
