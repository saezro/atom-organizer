# Ledger — árbol de salida + RGB wide (post v3.4.83)

## Clarified
- Rodrigo (2026-09-09): el árbol de salida debe ser el de SIEMPRE (legacy), con carpetas
  TERMICA / RGB / ESTADILLOS arriba. El motor plan-apply lo invirtió a PB<n>_V<n>/TIPO.
- "arregla lo de las rgb" = las 1012 `*_W.JPG` que no se procesan (tarea 3874).
- PROD: no commit/push/tag/release sin OK explícito.

## Requisitos
- [x] Restaurar árbol legacy en el motor nuevo: `output/TERMICA/PB<n>/PB<n>_V<n>/` y
      `output/RGB/PB<n>/PB<n>_V<n>/` (hoy `output/PB<n>_V<n>/TERMICA`)
- [x] Volver a crear `ESTADILLOS/` en la salida (el motor nuevo dejó de hacerlo)
- [x] Revisar SIN_ORDENAR: se queda en `output/SIN_ORDENAR/<tipo>` (raíz), es lo legacy
- [x] Auditar consumidores: `sharding.py:247-331` y `phases.py:208-318` YA esperaban el legacy (el bug latente de reparto en nube queda resuelto). `indice.py:270` apuntaba mal el CSV de criterio -> corregido a `CSVs/_criterio/`
- [x] Tests: 1410 passed, 0 failed (1407 baseline + 3 nuevos de ESTADILLOS)
- [x] `*_W.JPG`: causa raíz = `_pct_recorte` devolvía el porcentaje crudo (70) donde se esperaba fracción (0.70). Bug nuevo del 2026-09-08 (motor plan-apply). Fix + guarda + test. Verificado con el fichero real de Rodrigo: crop 8000x6000 -> 5600x4200
- [~] aplazado: Validación de Rodrigo en el PC de oficina antes de dar nada por cerrado
