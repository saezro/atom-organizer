# LEDGER — log crudo del modal legible (v3.4.90)

## Clarified
- Rodrigo: "es que este log al abrirlo es horrible" → el log crudo sale inundado de `0`.
- Propuse 3 puntos (filtrar ceros + cablear [paralelismo] + tests); respondió "sí, dale".
- Scope cerrado: NO optimizar rendimiento, NO tocar la métrica de balance de bytes, NO ampliar.
- Release: v3.4.90 (patch).

## Items
- [x] 1. `_CollectingProgress.emit` (pipeline.py:325-327) filtra payloads no-texto → adiós `0`
- [x] 2. Cablear `[paralelismo]` de paralelismo.py (sink de módulo) al progress_callback vía organize.py
- [x] 3. Tests python de 1 y 2
- [x] 4. Suite python verde (baseline 1456)
- [~] 5. Suite webui: NO ejecutada (OOM en la VM); justificado, cero cambios en webui/ en v3.4.90
- [x] 6. Bump version.py a 3.4.90
- [x] 7. Commit + push + tag v3.4.90
- [x] 8. Verificar build/release en GitHub — run 34366829355 success (8m3s), release v3.4.90 con Setup .exe + AppImage
- [x] 9. Documentar en Atlas (skill documentar-sesion)
