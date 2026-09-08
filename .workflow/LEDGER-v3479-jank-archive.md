# Ledger — v3.4.79: arranque lento / UI a tirones

## Clarified
- P: ¿La ventana tarda en salir o sale rápido y va a tirones? → R (Rodrigo): **sale rápido, va a tirones**.
- Añadido por él: el selector de carpeta también tarda un poco en abrirse.
- P: ¿arreglo las dos cosas en una release? → R: «arregla todo si».
- OK permanente para commit + push + tag + release sin volver a preguntar.

## Requisitos
- [x] Descartar la precarga síncrona de pandas como causa del jank (la ventana sale rápido).
- [x] Diagnosticar el jank: `render.json` clavado en software porque un crash previo a pintar cuenta como fallo de GPU.
- [x] Diagnosticar el selector de carpeta: compila el C# con csc.exe en cada apertura.
- [x] Fix 1: cachear en disco el DLL del diálogo moderno, con fallback al camino antiguo.
- [x] Fix 1b: precalentar ese DLL en un hilo al arrancar.
- [x] Fix 2: diferir el marcador `pendiente` hasta justo antes de `webview.start()`.
- [x] Fix 3: `olvidar_degradacion()` — al cambiar de versión se borra la degradación acumulada (auto-cura a Rodrigo y a Daniel).
- [x] Fix 4: precarga de pandas a hilo daemon (quita ~1,5 s del arranque).
- [x] Tests de los 4 fixes (11 nuevos, fixture `_pandas_intacto` respetada).
- [x] Suite Python en verde: 1273 passed.
- [x] Suite JS en verde (311 passed, 45 ficheros).
- [x] Bump `version.py` a 3.4.79.
- [x] Commit + push a main + tag v3.4.79 + release verificada (commit 6e31f6b, CI 34204404649 success, .exe y .AppImage publicados).
- [x] Decirle a Rodrigo el atajo para curar YA su instalación sin esperar a la release.
- [x] Documentar con `documentar-sesion` (Diario 2026-09-08 + nota canónica ATOM Organizer).

## Fuera de scope
- [~] aplazado: por qué la instalación vía autoupdater dejó la app inservible (follow-up abierto, no reproducido).

## Auditoría posterior (code-auditor sobre 75080ba)
- [x] ALTA — DLL del selector sin escritura atómica ni validación: un DLL truncado dejaba el
      selector muerto para siempre. Fix: compilar a temporal + `os.replace`, validar tamaño,
      e invalidar la caché y reintentar por el camino antiguo si `Add-Type -Path` aborta.
- [x] MEDIA — rutas con comilla simple (`O'Brien`) rompían el script PowerShell. Fix: `_lit_ps()`.
- [x] MEDIA — precarga de pandas en hilo podía saltarse la neutralización de pytz. Fix:
      `precarga.neutralizar_pytz()` síncrono en el hilo principal antes de lanzar el hilo.
- [x] Here-string PowerShell verificado idéntico al original (terminador `"@` a columna 0).
- [x] 1281 pytest + 311 vitest en verde tras los arreglos.
