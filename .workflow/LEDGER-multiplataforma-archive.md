# LEDGER — Unificación multiplataforma + giro del JPG térmico

## Clarified
- **¿Unificar también `rjpeg_a_tiff.py` (3er mecanismo de resolución del SDK)?** → Sí ("sí a todo").
- **TAREA 2, ¿el `*_T.JPG` debe girarse?** → Sí: "sin perder funcionalidad" = paridad con el motor viejo.
- **Restricción**: es producción, sin commits/push/release sin OK explícito de Rodrigo.

## Requisitos
- [x] Verificar la divergencia `main` ↔ `raspi/modo-servidor` → **no existe**: 0 commits por delante, `5c45223` (ARM/box64) ya en `main`. Nada que mergear.
- [x] Punto único de decisión de SO: `pipeline._is_windows()` delega en `external_tools._current_os()` (era un `sys.platform` duplicado).
- [x] Matriz de plataformas en test: `tests/test_backend_termico_plataformas.py` (Windows / Linux x86-64 / ARM / Cloud Run) + test que rompe si se reintroduce un test de SO local.
- [x] `rjpeg_a_tiff.py` deja de resolver el SDK por su cuenta y delega en `external_tools`, conservando los overrides `--sdk` / `--exiftool`.
- [x] El `*_T.JPG` vuelve a publicarse girado, con el mismo ángulo que su TIFF (`_copiar_jpg_destino`), sobre la COPIA de destino: el original nunca se toca.
- [x] Guarda `--sin-rotacion` replicada del motor viejo: con `gen_thumbnails=False` el JPG NO se gira (girar destruye el payload radiométrico del R-JPEG — incidente CLARE `wpv52`, 2026-08-21).
- [x] Sin doble giro: `phases.split_images` (que llamaba a `rotate_thermal_jpgs_in_place`) es código muerto desde que `_TASKS["split_images"] → organizar_plan_apply`; `do_convert_to_tif` sigue girando in-place en su propia tarea, que es correcto.
- [x] Suite completa verde.
- [~] Commit / push / release: **aplazado**, requiere OK explícito de Rodrigo.
- [~] Medición en Windows con las trazas de `[paralelismo]`: bloqueada, requiere release + PC de oficina.

## Fuera de alcance
- Cerrar `LEDGER-rgb-lento.md` (→ archive): pendiente del OK de Rodrigo.
