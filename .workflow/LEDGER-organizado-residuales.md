# LEDGER — Residuales del motor de organizado

Salen de `LEDGER-rgb-lento-archive.md`: hallazgos 🟠/🟢 de la auditoría del motor plan→apply que
NO bloqueaban el rendimiento ni la release v3.4.83, y quedaron sin aplicar.

## Clarified
- Ninguno de los tres es un 🔴: no cuelgan un run ni corrompen datos hoy. Se cierran cuando toque.

## Requisitos
- [ ] 🟠 `paralelismo.py:135-139` + `apply.py:351` — `maximo = arranque*2` puede pasarse del presupuesto de RAM (la regla 1 solo corrige a posteriori) y en Windows cada worker de proceso relanza el bootloader de PyInstaller. Validar con medición real.
- [ ] 🟠 `indice.py:332` — sin detección de colisión de `ruta_salida_original`; el manifiesto solo tiene UNIQUE en `ruta_origen`, así que dos imágenes distintas pueden pisarse en destino.
- [ ] 🟢 `manifiesto.py:177-181` — conexiones de hilos de pool sin `close()` explícito (se confía en el GC).
- [ ] Test dedicado del EXIF conservado en el `_CROP` rotado (verificado por lectura, sin cobertura)

## Fuera de alcance
- Rendimiento de la fase RGB: resuelto y medido (KL19, ~4,2 img/s). Ver `LEDGER-rgb-lento-archive.md`.
