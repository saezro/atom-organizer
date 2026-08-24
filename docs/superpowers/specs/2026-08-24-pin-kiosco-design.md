# PIN de kiosco + pantalla de perfil (Raspberry Pi)

Fecha: 2026-08-24 · Rama: `raspi/modo-servidor` · Estado: aprobado por Rodrigo

## Problema

El kiosco del ATOM Organizer en la Pi es hardware compartido en una sala: cualquiera que
pase puede operar la pantalla y subir vuelos con la credencial emparejada. No hay ninguna
barrera local. Además, la pantalla de "cuenta" solo muestra el email y un botón de cerrar
sesión: no hay forma de ver de quién es la sesión ni de gestionar nada.

## Decisiones cerradas

| Decisión | Valor | Motivo |
|---|---|---|
| Ámbito del PIN | Del **dispositivo** (la Pi) | Funciona sin red; independiente de quién esté emparejado |
| Longitud | **4 dígitos** | Estándar de kiosco táctil; el límite de intentos aporta la seguridad |
| Cuándo se pide | Al arrancar **y** tras **10 min** de inactividad | Protege la sala sin molestar durante el trabajo |
| Congelar inactividad | Sí, mientras haya subida en curso | No bloquear en medio de un lote |
| Primer PIN | **Obligatorio tras emparejar** | La Pi nunca queda sin PIN |
| Olvido del PIN | **Re-emparejar lo resetea** | El QR ya prueba acceso a la cuenta; sin endpoints ni secretos nuevos |
| Dónde vive el secreto | `session.db` (SQLite, tabla `meta`), `0600` | Patrón y permisos ya existentes en `atom_core/session_store.py` |
| Quién verifica | **El backend Python** | El hash nunca viaja al frontend |

Descartado: PIN maestro de Atom (secreto compartido que envejece mal) y PIN por defecto de
fábrica (nunca se cambian).

## Arquitectura

### Backend — `atom_core/pin_kiosco.py` (módulo nuevo)

Unidad aislada, sin dependencias de `app_webview`. Responsabilidad única: derivar, guardar y
verificar el PIN del dispositivo.

- Hash con `hashlib.scrypt` (stdlib, sin dependencias nuevas) + salt aleatorio de 16 bytes.
  Se persiste `scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>` como un valor de la tabla `meta`
  de `session.db`, bajo la clave `pin_kiosco`.
- API pública:
  - `hay_pin(store) -> bool`
  - `fijar(store, pin)` — valida formato (4 dígitos exactos), escribe el hash
  - `verificar(store, pin) -> bool` — comparación en tiempo constante
  - `cambiar(store, actual, nuevo)` — falla si `actual` no verifica
  - `borrar(store)` — usado al desemparejar
- Control de intentos: contador **en memoria** en el proceso servidor (no persistido: un
  reinicio del servidor no es un vector de ataque práctico en esta Pi). 5 fallos → espera,
  que escala 30 s, 60 s, 120 s… El backend devuelve `{ok: false, espera_segundos: N}`.

### Backend — `app_webview.py` (métodos nuevos en `Api`)

`pin_estado()`, `pin_fijar(nuevo)`, `pin_verificar(pin)`, `pin_cambiar(actual, nuevo)`.
Delegan en `pin_kiosco` sobre el `SessionStore` ya existente. `pin_estado()` devuelve
`{hay_pin, bloqueado_hasta}` — nunca el hash ni el PIN.

`cloud_logout()` y el pairing completado en `cloud_pair_poll()` llaman a `pin_kiosco.borrar`:
desemparejar resetea el PIN, que es la vía de recuperación acordada.

### Backend — exponer el nombre del usuario

Hoy `cloud_status` solo expone `email` y `picture`. El `name` viene en el id_token de Google
pero no se persiste. Cambio pequeño: guardarlo en `session.db` junto al resto de la sesión y
añadirlo al dict de `cloud_status`. Sin él, la pantalla de perfil no puede mostrar el nombre.

### Frontend — `webui/src/KioskLock.jsx` (componente nuevo)

Pantalla de bloqueo a pantalla completa: 4 puntos de progreso, pad de 0-9 y borrar,
construido sobre `BotonToque` (`webui/src/pulsacion.jsx`). No se reutiliza
`TecladoPantalla.jsx` — ese es un teclado general con letras/números; para un PIN queremos
dígitos grandes y nada más. Sí se reutiliza el patrón táctil.

Dos modos en el mismo componente: `verificar` (desbloquear) y `fijar` (crear PIN nuevo, con
confirmación por repetición). Mensaje de espera cuando el backend devuelve `espera_segundos`.

### Frontend — guard en `App.jsx`

Estado nuevo `kioskDesbloqueado`. En el render del modo kiosco (hoy `App.jsx:627`), si
`pin_estado().hay_pin` y no está desbloqueado → `KioskLock` en modo `verificar`. Si hay
credencial emparejada y **no** hay PIN → `KioskLock` en modo `fijar` (obligatorio).

Temporizador de inactividad de 10 minutos que resetea `kioskDesbloqueado`. Se congela
mientras haya una subida en curso (el estado de subida ya existe en el frontend).

### Frontend — pantalla de perfil

Sustituye la rama `accion === 'cuenta'` de `KioskScreen.jsx:469-497`, que hoy es solo email +
cerrar sesión. Pasa a mostrar:

- Foto grande (`status.picture`, con la inicial del email como fallback ya existente)
- Nombre y email
- Último acceso (`status.validada_en`) y última subida (de `upload_log`)
- Botón "Cambiar PIN" → `KioskLock` en modo cambio (pide actual, luego nuevo)
- Botón "Cerrar sesión" (se mantiene, con `BotonMantener` por ser destructivo)

## Flujo de datos

1. Arranque del kiosco → `pin_estado()` → sin PIN y sin credencial: se opera normal (solo
   funciones locales). Con credencial y sin PIN: pantalla obligatoria de fijar PIN.
2. Con PIN: `KioskLock` en `verificar` → `pin_verificar(pin)` → si ok, `kioskDesbloqueado`.
3. 10 min sin toques (y sin subida activa) → vuelve a bloquear.
4. Cerrar sesión o re-emparejar → `pin_kiosco.borrar` → el ciclo empieza de nuevo en (1).

## Errores

- PIN mal formado → error de validación en el backend, el frontend no deja enviarlo.
- Demasiados intentos → el pad se deshabilita y muestra la cuenta atrás; no se filtra si el
  PIN era correcto.
- `session.db` ilegible o corrupto → se trata como "no hay PIN" y se registra en el log; no
  se debe dejar la Pi inoperable por un fichero de estado.
- Sin red: todo el flujo del PIN es local y debe funcionar igual.

## Testing

Backend, `pytest` siguiendo el patrón de `tests/test_app_webview_credencial.py` (sin
fixtures de framework, mocks por duck-typing sobre una `Api()` real):

- fijar → verificar ok / verificar mal
- cambiar con `actual` correcto e incorrecto
- desemparejar y cerrar sesión borran el PIN
- bloqueo tras 5 fallos y expiración de la espera
- formato inválido rechazado

Frontend, `vitest` + `@testing-library/react` siguiendo `webui/src/AvisoSesion.test.jsx`
(mock de `./bridge` y `./bridge.js`):

- el pad compone 4 dígitos y llama a `pinVerificar`
- guard: con PIN y bloqueado no se pinta `KioskScreen`
- con credencial y sin PIN se pinta el modo `fijar`
- la pantalla de perfil muestra foto, nombre y email

## Fuera de alcance

- PIN por usuario o validado contra la Suite (se descartó: requiere red).
- Reset del PIN desde Atom-suite (endpoint nuevo; el re-emparejado ya cubre el caso).
- Cualquier cambio en el modo escritorio: el PIN es solo del kiosco.
