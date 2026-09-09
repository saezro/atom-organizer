# LEDGER — Diagnóstico de cuello de botella (HDD) — 2026-09-09

## Clarified
- E:\DATOS_PARA_ORGANIZAR es un HDD mecánico (Rodrigo).
- El veredicto actual solo mira CPU media (<40 disco / >70 cpu); 47% → "mixto". Por eso nunca dijo "disco".
- Rodrigo quiere: (a) que diga tal cual que el disco es el cuello, (b) EN VIVO durante el proceso,
  (c) sonda AL EMPEZAR con tipo de disco origen/destino (HDD/SSD), núcleos, RAM y CPU ociosa
  (para saber si la máquina está libre o compartida con otros procesos).
- ES PRODUCCIÓN: implementar sí; commit/push/tag/release solo con OK explícito.

## Items
- [x] T1 `atom_core/diagnostico_maquina.py` nuevo: tipo_disco() (Win PowerShell / Linux rotational) + sonda_inicial() + tests
- [x] T2 `medicion_recursos.py`: veredicto consciente del tipo de disco + resumen_parcial() (ventana en vivo) + tests
- [x] T3 `organize.py`: emitir sonda al arrancar + veredicto en vivo dentro del evento `stats`
- [x] T4 webui: pintar sonda inicial y banner en vivo "El disco es el cuello de botella"
- [x] T5 Suite completa verde (pytest 1415+ y webui 311+)
- [x] T6 OK de Rodrigo (2026-09-09): cap de workers en HDD + release v3.4.86
- [x] T7 `paralelismo.py`: `TOPE_WORKERS_HDD = 3` + kwargs `tope_hdd`/`proveedor_tipo_disco`, aplicado
      en la property `trabajadores` y en `revisar()`; registro global del tipo de disco en
      `diagnostico_maquina.py`, poblado por la sonda desde `organize.py`. Solo la fase RGB lo pasa
      (`phases.py`); Térmicas (I/O a procesos externos) sin cap.
- [x] T8 Verificado: pytest 1443/1443 y webui 315/315. Auditoría sin hallazgos ALTO (solo un log
      "una sola vez" que bajo concurrencia podría trazarse dos veces: benigno, aceptado).
      El fallo de `logoutBucket.test.jsx` visto en una pasada era FLAKY (contención de CPU con
      pytest en paralelo), no regresión: confirmado con 3 pasadas limpias.
- [x] T9 version.py 3.4.86 + commit + tag v3.4.86 + push (dispara el workflow `release`).
