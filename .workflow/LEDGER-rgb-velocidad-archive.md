# LEDGER — velocidad fase RGB (2026-09-09)

## Clarified
- ¿Cambiar de librería de imagen? **NO.** Bench N=5 8000x6000: Pillow 12.3 = 1,72 s/ciclo,
  opencv 2,30 s, simplejpeg 2,34 s, PyTurboJPEG no arranca (DLL 3.x externa). Pillow ya usa
  libjpeg-turbo (`features.check_feature('libjpeg_turbo') == True`) → no hay nada mejor.
- ¿Subir Pillow 9.4 → 12.3? **YA ESTÁ**: los tres requirements pinnean `Pillow==12.3.0`.
  El "9.4" del handoff era del entorno del bench, no del repo.
- ¿Girar y luego recortar (idea de Rodrigo)? **SÍ**, es lo único que queda. Conmuta exacto:
  el recorte centrado por fracción sobre la girada da el mismo píxel.

## Items
- [x] Benchmark de decoders (Pillow 9.4 / 12.3 / opencv / simplejpeg / PyTurboJPEG)
- [x] Transponer UNA vez en `_escribir_salidas_de_fila` en vez de dos
- [x] Test que fija el invariante (crop girado == girado y recortado)
- [x] Suite python 1443+ passed
- [x] Suite webui 319 passed
- [x] Documentar en el Atlas (ATOM Organizer + Diario 2026-09-09) incl. descarte de jpegtran

## Fase 2 — balance entrada/salida (petición 2026-09-09)
Rodrigo: «necesito un estado final de lo que ocupa todo el output comparado con el input».
Motivo: `mb_leidos`/`mb_escritos` son `disk_io_counters()` del kernel (I/O de toda la máquina),
NO el tamaño de los datos. Los 28→9 GB no son entregables, son bytes de disco.

- [x] Sumar bytes de ENTRADA en el indexado (antes de mover/borrar nada)
- [x] Sumar bytes de SALIDA entregada al terminar el run
- [x] Emitir ambos en el resumen final y pintarlos en ProgressModal (con el % de ratio)
- [x] Tests python + webui
