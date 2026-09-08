# LEDGER — Métricas de progreso del organizado (ETA, img/s, desglose)

## Clarified (2026-09-08)
- **Dónde**: en el MODAL de la app (webui React), no solo en el log. Decisión de Rodrigo.
- **Entrega**: release directa (3.4.82) en cuanto pasen los tests, sin esperar validación.
- Planta de referencia de la queja: ~2000 imágenes, el índice tardó ~50 s.
- El "5 % de RGB" NO sirve: hace falta ETA y velocidad.

## Contrato de eventos (lo fija el hilo base, backend y frontend deben respetarlo)
Canal ya existente (`organize.py`, docstring 11-13): `emit('stats', dict)` y `emit('done', dict)`.
NO se crean tipos de evento nuevos: se extienden los payloads.

- Tras el índice → `emit("stats", {...})` con: `fase:"Índice"`, `total`, `rgb`, `termica`,
  `rgb_extra`, `sin_asignar`, `sin_timestamp`, `vuelos`.
- Durante el apply → `emit("stats", {...})` con, además de `done`/`total`/`rgb`/`termica`:
  `img_por_segundo` (float) y `eta_segundos` (int|null).
- Al terminar → `payload_done["fases"] = [{"nombre": str, "segundos": float}, ...]`, en orden
  de ejecución. El modal destaca la más lenta.

## Requisitos
### Backend
- [ ] Desglose del índice por `tipo` del manifiesto (`COUNT ... GROUP BY tipo` + `unassigned`),
  emitido como `stats` al cerrar la fase Índice. `TIPOS_RGB` incluye `RGB_Extra`: contarlo
  aparte, no fundirlo con RGB.
- [ ] Throughput real en `aplicar_rgb` y `aplicar_termicas`: img/s medidas sobre ventana móvil
  (no media global desde el inicio, que miente cuando el ritmo cambia) + ETA derivada.
- [ ] Duración por fase acumulada en `organize.py` alrededor de `cerrar_fase()`, a
  `payload_done["fases"]`. Hoy `MedidorRecursos` NO expone segundos.
- [ ] El coste de medir no puede penalizar el run (nada de un `emit` por imagen; re-emitir cada
  N imágenes o cada X segundos, como ya hace `StatsTracker.on_image`).

### Frontend (`webui/src/ProgressModal.jsx`)
- [ ] Desglose del índice visible al terminar esa fase.
- [ ] ETA + img/s bajo la barra, reusando `fmtDur` y el patrón de `recursosLine`.
- [ ] Al final, qué fase tardó más, junto al veredicto disco/CPU que ya existe.
- [ ] Nunca px: rem/vh. Sin controles flotantes nuevos.

### Verificación
- [ ] Tests nuevos del backend (dobles a mano + monkeypatch, NADA de unittest.mock).
- [ ] `.venv/bin/python -m pytest -q` verde (hoy 1356) y `cd webui && npx vitest run` verde.
- [ ] Release 3.4.82 (bump `version.py` -> commit -> tag -> push).

## Aparte, NO bloqueante para esto
- [ ] **Bug**: tras actualizar con el autoupdater, el Organizer no arrancaba y hubo que
  reinstalarlo. Este Organizer AÚN NO lo usa el equipo, así que no es incendio, pero hay que
  cerrarlo antes de que lo usen. Falta el log de Inno de Rodrigo:
  `%APPDATA%\ATOM-Organizer\Logs\atom-organizer-install_<fecha>.log`. El updater lanza el
  instalador con `/CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /RESTARTAPPLICATIONS /NORESTART`
  (`updater.py:241-247`).
