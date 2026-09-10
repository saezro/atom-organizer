# LEDGER — Aviso "sesión remota ocupada" dentro del Organizer

## Clarified
- Alcance: **feature real del repo** (no apaño del banco de pruebas). — Rodrigo, AskUserQuestion.
- Formato: "lo que consideres para que no se puedan solapar" → **overlay bloqueante**, no banner decorativo.
- No push a main ni release sin OK explícito.

## Diseño acordado
Lock JSON con heartbeat en la carpeta de config del usuario; la UI lo consulta y pinta overlay
fullscreen sin botón de cerrar que además impide lanzar organizado.

## Items
- [x] T1 `atom_core/sesion_remota.py`: lock `sesion_remota.json` en `_user_config_path()`, campos
      `{motivo, host, pid, desde}`, heartbeat en hilo daemon, `activa()` con caducidad ~90 s,
      context manager `marcar(motivo)`. Tolerante a JSON corrupto/ausente.
- [x] T2 Tests unitarios de T1 (`tests/test_sesion_remota.py`): activa/no-activa, caducidad,
      corrupto, reentrada, limpieza al salir del context manager.
- [x] T3 `Api.sesion_remota()` en `app_webview.py` + ruta equivalente en modo servidor
      (`atom_core/webserver.py`) → `{activa, motivo, desde}`.
- [x] T4 UI: `webui/src/SesionRemota.jsx` (overlay bloqueante, calcado de `AvisoSesion.jsx`, SIN
      `onCerrar`) + poll ~5 s desde `App.jsx` + bloqueo de lanzar organizado mientras esté activa.
- [x] T5 `scripts/bench_rgb.py` envuelve el run con `marcar("Benchmark RGB en curso")`.
- [x] T6 Verificación: suite pytest verde + `cd webui && npm run build` OK.
- [x] T7 Validación visual en el PC de oficina (copiar `webui/dist`, captura con `ShotPC`).
- [x] T8 Documentar en el Atlas (skill `documentar-sesion`).

## Fuera de scope
- Arreglo del bug ERR_FILE_NOT_FOUND en WebView2 (`?v=` sobre `file://`) — ledger aparte.

## Hallazgo extra (arreglado en esta sesión)
- [x] BUG PROD Windows: `resolve_target()` devolvía `file:///...index.html?v=<ver>`. WebView2/
      EdgeChromium NO resuelve query sobre `file://` → página de error de Edge (la UI no cargaba).
      Confirmado A/B en el PC de oficina: misma URL sin query carga, con query falla.
      Fix: fragmento `#v=<ver>` en vez de `?v=`. Test `tests/test_resolve_target.py` actualizado.

## Decisión de Rodrigo (2026-09-10)
- [x] Se queda la **tarjeta actual** (UI de fondo atenuada pero visible), no velo opaco total.
      Sin cambios de código: `webui/src/SesionRemota.jsx` queda como está.
