# Raspi — identidad de dispositivo, aviso de sesión cerrada y gating parcial

Fecha: 2026-08-24 · Repos: `atom-organizer-work` (kiosco Pi) + `Atom-suite` (backend)

## Problema

En la Pi la UI mostraba el correo del usuario (estado cacheado en local) pero al pulsar SUBIR
salía «inicia sesión en aerotools», y al encender no avisaba de nada.

Causa raíz: `Api.cloud_status()` (`app_webview.py:592-616`) **solo lee estado local**, nunca
pregunta al backend. Si el dispositivo fue revocado, la UI sigue pintando sesión válida hasta
que una acción falla. La verificación real ya existe (`cloud_verify()` → `google_auth.verificar()`
→ `access_token(force_refresh=True)`) pero solo se invoca a mano.

No es un problema de caducidad de sesión web: en la Pi **no hay sesión humana**. El único login
es el pairing por QR, que crea un `device_token` en `public.organizer_dispositivos` **sin
`expires_at`**. Lo único que lo mata es que Google devuelva `invalid_grant` en el refresh, y en
ese caso `POST /api/organizer/token` (`server.js:2199-2213`) ya hace `UPDATE ... SET revocado = true`
y responde `401 {error:'dispositivo no vinculado'}`.

## Estados

Un único estado de credencial, servido por la Pi a su UI:

| Estado | Origen | UI |
|---|---|---|
| `ok` | ping al backend responde 200 | operación normal |
| `sin-credencial` | 401 del backend (nunca emparejado, token inválido o `revocado=true`) | aviso grande «SESIÓN CERRADA» + QR |
| `sin-conexion` | timeout / DNS / 5xx | aviso grande «SIN CONEXIÓN» |

El backend devuelve el mismo 401 para «revocado» y «token inválido». No se distinguen: la salida
del usuario es la misma (re-emparejar con el QR).

## Cuándo se verifica

Sin polling — la Pi normalmente está apagada.

- Al arrancar la Pi (antes del menú).
- Antes de cada acción: subir en crudo, organizar, elegir planta.
- Latido lento cada 6 h, por si se queda días encendida.
- Ante cualquier 401 de una llamada real → el estado pasa a `sin-credencial` en el acto.

El resultado se cachea; la verificación nunca bloquea el pintado de la UI.

## Backend (Atom-suite) — cambio mínimo

Un endpoint nuevo, de solo lectura:

```
GET /api/organizer/device/status   →  requireOrganizerIdentity
200 { ok:true, email, esAtom }   |   401 { error:'device-no-emparejado' }
```

Reutiliza el middleware existente (`server.js:1913-1948`), que ya filtra `revocado = false`.
**No se toca ningún guard existente**, ni `guardConsultaRuns`, ni `/api/organizer/lanzar`, ni
`requireIngestOrganizer`. Riesgo de dejar al equipo fuera de la Suite: nulo.

La revocación por Google se propaga sola: `/api/organizer/token` escribe `revocado=true` y el
siguiente ping ya devuelve 401.

## Pi — cola local

Sin credencial, subir y organizar **no fallan**: se aceptan y quedan en cola en disco bajo
`~/.config/atom-organizer/cola/`. Un worker la drena cuando el estado vuelve a `ok`.

- Cada job: carpeta con su manifiesto (planta/inspección destino, lista de ficheros, estado,
  intentos) y los ficheros ya copiados o enlazados.
- Idempotente: reintentar un job ya subido no duplica.
- Reintentos con backoff; los fallos permanentes quedan visibles, no se borran en silencio.

## Kiosco — UI

Pantalla 480×320 landscape, CSS propio del kiosco (`App.css`/`index.css`, variables `:root`),
**sin Tailwind ni react-icons**: iconos SVG inline. rem/vh/vw, cero px nuevos.

- `AvisoSesion.jsx` (nuevo): overlay a pantalla completa, dos variantes (`sin-credencial` con
  llamada al QR, `sin-conexion`). Aparece también al encender la Pi. Es **descartable**.
- Al descartarlo: subir en crudo y organizar siguen accesibles (van a cola) con un indicador de
  «N pendientes».
- `InspeccionSelector` (embebido en `KioskScreen.jsx:641` cuando `accion==='subir' && !inspeccion`)
  queda atenuado con el motivo: listar inspecciones exige backend.

## Fuera de alcance

- Convertir «Organizar» en remoto (`/api/organizer/lanzar` sigue sin llamarse desde el kiosco).
- Cachear la lista de inspecciones para elegir planta sin red.
- Cualquier concepto nuevo de «sesión humana» en la Pi.
- Autostart por systemd del kiosco (sigue pendiente de la fase anterior).

## Verificación

El túnel SSH inverso a la Pi lo abre el portátil de Rodrigo; sin él no hay acceso desde la VM.
Las pruebas en hardware las hace Rodrigo. Desde la VM se verifica: el endpoint nuevo con curl
contra dev, y la lógica de estado/cola de la Pi con tests unitarios en Python.

Despliegue de la UI a la Pi: no hay mecanismo automatizado. `cd webui && npm run build`, llevar
`dist/` a la Pi y reiniciar el servidor.
