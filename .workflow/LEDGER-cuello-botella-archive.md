# Ledger — Ver el cuello de botella del organizado (v3.4.80)

## Clarified
- **¿Dónde se ven las métricas?** → En cada fase (`MB/s · CPU %`) + una línea de veredicto al terminar.
- **¿Solo medir o también optimizar?** → Medir **y** optimizar, incluida la fusión de decodes.
- **Disco de destino**: HDD mecánico (`E:`). Sospecha de partida: I/O-bound, pero hay que medirlo.
- **psutil**: ya está en `requirements.txt` (7.2.2) y en los dos `.spec`. No hay dependencia nueva.

## Contrato del evento (fijado para poder paralelizar back y front)
En el evento `phase`, dentro de `prev`, se añade:
```
"recursos": {
  "mb_leidos": float, "mb_escritos": float,
  "mb_por_segundo": float,          // lectura + escritura, media de la fase
  "cpu_pct": float, "nucleos": int, // cpu_pct normalizado 0-100 sobre TODOS los núcleos
  "veredicto": "disco" | "cpu" | "mixto" | null
}
```
Al cerrar el proceso, el mismo objeto agregado de todo el run bajo `recursos_totales`.
`null` en `veredicto` (o `recursos` ausente) = no se pudo medir → el front no pinta nada.

## Criterio del veredicto
- CPU media < 40 % → `disco` (hay throughput pero los núcleos están ociosos esperando al disco).
- CPU media > 70 % → `cpu`.
- Entre medias → `mixto`.
Honesto sobre lo que mide: no hay benchmark del disco, así que el veredicto sale de la CPU ociosa, no de comparar contra un techo teórico.

## Tareas
- [x] A. `atom_core/medicion_recursos.py`: muestreador en hilo (psutil, ~1 s), API por fase, veredicto.
- [x] A2. Enganche en `atom_core/organize.py` (`_close_phase`, ~línea 649) + agregado final.
- [x] A3. Tests del muestreador y del veredicto (incluye: psutil ausente/sin permisos → `null`, sin romper el run).
- [x] B. Frontend `webui/`: métricas bajo cada fase + línea de veredicto. Sin `px` (rem/%), español con tildes.
- [x] C1. `check_and_fix_xmp_data` (`pipeline.py:907`): quitar la verificación pyexiv2 incondicional por imagen.
- [x] C2. `compress_image` (`pipeline.py:661`): reutilizar un único `bloque_xmp` entre `get_gimbal_yaw_pitch` y `get_xmp_data`.
- [~] C3. Fusionar compresión+rotación en un solo decode/encode → **DESCARTADA, no viable hoy** (ver abajo). Pendiente de OK de Rodrigo para el refactor grande.
- [x] D. Suite verde (pytest + vitest desde `webui/`) y publicar v3.4.80.

## Hallazgos (de la auditoría del pipeline)
- Fases que NO decodifican píxel: estructura de carpetas (solo mueve) y meta/geo (solo EXIF). Son las dos rápidas (~52 s cada una). Encaja con I/O.
- Una imagen RGB se decodifica/recodifica ~4 veces en un run: separación, rotación, `get_model` del recorte y el crop.
- La separación abre un `ProcessPoolExecutor` nuevo por carpeta (`pipeline.py:2560`), con `spawn` eso son cientos de ms por carpeta. Candidato extra, no incluido aún.

## Notas de ejecución (2026-09-08)
- A2 completado: faltaban `medidor.detener()` en el `finally` y `recursos_totales` en el evento `done`; añadidos.
- C1 implementado como cribado barato `_xmp_presente_en_cabecera` (busca las 10 claves en la cabecera ya leída) antes de caer a pyexiv2. Conservador: cualquier duda → pyexiv2.
- C2 implementado: un único `leer_bloque_xmp` compartido entre `get_gimbal_yaw_pitch` y `get_xmp_data`.

## C3 — por qué NO se fusiona (análisis del pipeline, 2026-09-08)
El bloqueo NO es de parámetros de encode: los dos `img.save` son idénticos (`quality`, `exif`, sin
`optimize`/`subsampling`/`icc_profile`) y `process_one_image` (`pipeline.py:200-270`) ya resuelve la
mecánica de la fusión. El bloqueo es de **dependencia de datos**:

- El ángulo lo decide `gen_thumbnails_and_rotate` (`pipeline.py:1967-2062`) por **consenso de toda la
  carpeta de vuelo `PBx_Vy`** (`pipeline.py:2034-2093`), no imagen a imagen.
- Esa agrupación `PBx_Vy` la crea la fase STRUCT (`atom_core/phases.py:548`), que corre DESPUÉS de la
  compresión (`phases.py:413-458`). Entre medias hay 3 fases que mueven/crean ficheros (struct, recorte,
  meta/CSV). En el momento de comprimir el ángulo **todavía no existe**.
- El desglose 270°/90°/sin girar llega al front parseando el TEXTO del log con regex
  (`atom_core/progress_stats.py:50-51,129`); mover la rotación rompería ese formato.
- Los conjuntos ni siquiera coinciden: compresión depende de `cfg.compress_rgb` y rotación de
  `cfg.gen_thumbnails`+`gen_thumbnails_rgb`, flags independientes; las térmicas se rotan por otro camino
  (`rotate_thermal_jpgs_in_place`).

Para hacerla viable haría falta: anticipar el agrupamiento por vuelo (o al menos el yaw agregado) a antes
de la separación, sustituir el StatsTracker de regex por contadores estructurados y alinear los dos flags.
Es un refactor grande sobre PROD; no se hace a ciegas sin medir antes dónde está el cuello de verdad.
