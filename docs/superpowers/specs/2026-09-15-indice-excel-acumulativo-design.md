# Índice Excel acumulativo por planta
2026-09-15

## Problema
- Organizar por cachitos (varias SD/sesiones) hacia el MISMO destino de planta hoy no se puede: el guard de destino vacío aborta (`atom_core/organize.py:776-792`).
- No hay un índice legible de qué se ha organizado: solo `manifiesto.db` (interno) y CSV `meta`/`location` sin cabecera.
- El cierre relee TODAS las imágenes de salida para sacar GPS/gimbal (`atom_core/cierre.py:147-186` → `exif.MetaLocation.check_input_folder_and_iterate`, `exif.py:1119`): ≈340 s en MELINESTI.

## Decisiones (Cas)
- Cachitos = mismo destino de planta; el índice acumula.
- Excel = una tabla, 1 fila por imagen: metadatos normalizados + vuelo + instrucción de config + estado.
- Columnas comunes para todas las cámaras (los tags varían por modelo).
- Imagen ya en índice y `hecho` → se salta y se avisa. Nuevas y `fallido` → se procesan.
- Enfoque #1: el manifiesto SQLite es la fuente única; Excel y CSV se derivan de él.

## Arquitectura

### Fuente: `<destino>/.organizado/manifiesto.db`
- Sigue siendo el manifiesto actual (`atom_core/manifiesto.py:75-103`), ampliado. Nada nuevo fuera de `.organizado/`.
- **Clave de dedupe** nueva: `clave = nombre_original + "|" + timestamp_exif`. Si `timestamp_exif` es NULL: `nombre_original + "|" + bytes_origen`. UNIQUE sobre `clave`.
  - Motivo: hoy UNIQUE es `ruta_origen` absoluta (`manifiesto.py:78`); otra SD o punto de montaje duplicaría filas.
  - `ruta_origen` deja de ser UNIQUE; se actualiza a la última ruta vista.
- **Columnas nuevas en `imagenes`**: `nombre_original`, `clave`, `lat`, `lon`, `altitud_abs`, `altura_relativa`, `gimbal_yaw`, `gimbal_pitch`, `gimbal_roll`, `flight_yaw`, `ancho_px`, `alto_px`, `ejecucion_id`.
- **Tabla nueva `ejecuciones`**: `id`, `inicio`, `fin`, `origen`, `version_app`, `n_nuevas`, `n_saltadas`, `n_reintentadas`.
- **Migración**: SQLite no quita un UNIQUE con ALTER → recrear tabla (`CREATE imagenes_v2` + `INSERT SELECT` + rename) dentro de una transacción, calculando `clave`/`nombre_original` de las filas viejas. Metadatos nuevos quedan NULL en filas migradas. Extiende `_migrar_columnas` (`manifiesto.py:181-190`).

### Índice (fase existente, `atom_core/indice.py`)
- `_leer_metadatos` (`indice.py:255-334`) ya lee la cabecera una vez; se amplía para sacar del MISMO buffer: GPS (lat/lon/altitud), `GimbalPitchDegree`, `GimbalRollDegree`, `FlightYawDegree`, `RelativeAltitude`, ancho/alto. Sin aperturas extra.
  - Hoy `gps` sale siempre `None` (`AttributeError`, comentario `indice.py:269-275`). Se sustituye por lectura real; el valor debe coincidir con `MetaLocation.leerLatitudLongitudAltitud_exif_DJI` (`exif.py:1164`).
  - Réplica pura en memoria + caída a la función original por ruta si falla, mismo patrón que timestamp/modelo.
- Inserción con `INSERT ... ON CONFLICT(clave)`:
  - fila existente `hecho` → no se toca; cuenta como saltada.
  - fila existente `fallido` o `pendiente` → vuelve a `pendiente`, actualiza `ruta_origen` y `ejecucion_id`; cuenta como reintentada.
  - nueva → inserta.
- **Ángulo de giro por vuelo**: si `(pb, vuelo)` ya tiene filas en el manifiesto, se reutiliza su `angulo_giro`, no se recalcula. Motivo: JPG y TIFF del mismo vuelo con giros distintos entre cachitos.
- Aviso al final del índice: `"N imágenes ya organizadas, saltadas (vuelos: ...)"` por el canal de progreso actual.

### Guard de destino (`organize.py:776-792`)
- Destino con `.organizado/manifiesto.db` válido → se permite (modo acumular). `_restos` se ignora.
- Destino con contenido y SIN manifiesto → sigue abortando (igual que hoy).

### Apply
- Sin cambios. Solo procesa `pendiente` (`manifiesto.py:210`).

### Cierre (`atom_core/cierre.py`)
- `_emitir_meta_location` deja de llamar a `exif.MetaLocation`: genera los CSV desde el manifiesto (`filas_por_vuelo()`).
  - Mismos nombres (`<carpeta>_meta.csv` térmica, `<carpeta>_location.csv` RGB y RGB_Extra; `exif.py:1035`), mismas carpetas (vuelo + `CSVs/`), sin cabecera, mismas columnas y orden (`exif.py:975-977`).
  - `CalculatedDistance`/`LatitudFoto`/`LongitudFoto`: `image_theoretical_position` (`exif.py:1276`) con los valores del manifiesto y `cfg.flight_height`.
  - Filas: solo `estado='hecho'`. Se regeneran enteros por vuelo en cada run (acumula lo de cachitos anteriores).
  - Filas migradas con metadatos NULL → caída a la lectura actual SOLO para esas imágenes.
- Genera `<destino>/INDICE_<PLANTA>.xlsx` entero en cada cierre.
  - `PLANTA` = `basename(output_folder)`.
  - `openpyxl` en modo `write_only` (ya en requirements; añadir `collect_all('openpyxl')` a ambos `.spec`).
  - Una hoja `Imagenes`: cabecera congelada + autofiltro.
  - Si el fichero está bloqueado (Excel abierto en Windows, `PermissionError`): escribe `INDICE_<PLANTA>_<AAAAMMDD_HHMMSS>.xlsx` y avisa. No reintenta, no falla el cierre.
  - Escritura atómica: `.tmp` + `os.replace`.

### Columnas del Excel (orden)
1. Vuelo: `PB`, `Vuelo`, `Tipo`, `SinOrdenar`
2. Imagen: `NombreOriginal`, `NombreNuevo`, `TimestampEXIF`, `Modelo`, `AnchoPx`, `AltoPx`, `BytesOrigen`
3. Posición: `Lat`, `Lon`, `AltitudAbs`, `AlturaRelativa`, `GimbalYaw`, `GimbalPitch`, `GimbalRoll`, `FlightYaw`, `AlturaVuelo`, `CalculatedDistance`, `LatitudFoto`, `LongitudFoto`
4. Config: `AnguloGiro`, `PctRecorte`, `Comprime`, `RutaOriginal`, `RutaCrop`, `RutaTIFF`
5. Estado: `Estado`, `MotivoFallo`, `Ejecucion`, `FechaEjecucion`, `RutaOrigen`

- Rutas relativas al destino. Campo no disponible para ese modelo → celda vacía.

## Errores
- Metadato ilegible en índice → NULL en esa columna; no bloquea la imagen (igual que hoy).
- Manifiesto corrupto o migración fallida → aborta antes de tocar el destino con mensaje claro; no se borra nada.
- Excel falla por otra causa → aviso, cierre continúa (CSV y verificaciones son lo crítico).

## Tests (`pytest tests/`)
- **Equivalencia CSV**: vuelo real pequeño (RGB + térmica M4T); CSV desde manifiesto == CSV de `MetaLocation` actual, byte a byte o con tolerancia float documentada.
- Migración: manifiesto viejo (`test_manifiesto.py` ya tiene fixture de columna vieja) → clave calculada, filas intactas.
- Dedupe: misma imagen desde dos rutas → 1 fila; `hecho` se salta; `fallido` se reintenta.
- Ángulo: segundo cachito del mismo vuelo reutiliza `angulo_giro`.
- Guard: destino con manifiesto pasa; destino con restos sin manifiesto aborta.
- Excel: columnas/orden, nº filas == filas manifiesto; fichero bloqueado → nombre alternativo.
- Bench MELINESTI portátil: cierre antes/después (objetivo ≈ −300 s), índice sin regresión >10 %.

## Fuera de alcance
- Editar el Excel y releerlo como entrada.
- Varias hojas/resúmenes por vuelo.
- Rutas `gs://` en el cálculo de metadatos nuevos: siguen el camino original por ruta.
- Borrar del manifiesto imágenes eliminadas a mano del destino.
