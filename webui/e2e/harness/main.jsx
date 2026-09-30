// Arnés e2e para el regression test de layout del kiosco (480x320).
//
// Monta `KioskScreen` DIRECTAMENTE (sin pasar por `App.jsx`, que arrastra
// arranque de sesión, SSE y demás llamadas que no pintan nada en un test de
// layout). El bridge real (`src/bridge.js`) se deja intacto: en vez de
// mockear el módulo (imposible en un build de verdad servido por Vite), se
// planta `window.pywebview.api` con los 4 métodos de estadillo que
// `EsperaEstadillo`/`KioskScreen` llaman, tal com lo haría pywebview.
//
// El escenario se elige por query string (`?escenario=...`), leído ANTES de
// montar React, así que el efecto de "retomar espera" de `KioskScreen`
// (`api.estadilloEsperaEstado()` al montar) ya ve el estado correcto desde
// la primera llamada.
import { createRoot } from 'react-dom/client'
import '../../src/index.css'
import '../../src/App.css'
import KioskScreen from '../../src/KioskScreen.jsx'

const params = new URLSearchParams(window.location.search)
const escenario = params.get('escenario') || 'menu'

// Marca real del servidor de la Pi (`atom_core/webserver.py` la inyecta en
// el `index.html` que sirve) — `isServerMode()` (`src/bridge.js`) decide
// `tactil` a partir de ella. El resto de escenarios de este arnés no la
// necesitan (sus asserts de layout no dependen del modo táctil), pero el de
// la tarjeta «Estadillo recibido» sí: su paginación solo se activa con
// `tactil` (kiosco de verdad), que es justo el caso que hay que reproducir.
if (escenario === 'tarjeta-recibida') window.__ATOM_SERVIDOR__ = true

// Estado que devuelve `estadillo_espera_estado`. Mutable: `elegirCarpeta`
// (botón "Elegir carpeta…" del propio panel de espera) lo actualiza para que
// el siguiente poll ya no pida carpeta.
const estadoEsperaPorDefecto = { esperando: false, caducado: true }

const escenarios = {
  // Espera en curso, carpeta ya elegida (el caso que competía con el botón
  // "Organizar" antes del fix).
  'espera-con-carpeta': {
    esperando: true,
    caducado: false,
    fase: 'esperando',
    carpeta_seleccionada: true,
    red: { hostname: 'organizer.local', puerto: 80, ips: [{ interfaz: 'wlan0', ip: '192.168.1.50' }] },
    fotos: { total: 42, calculando: false, primera: '2026-09-20T08:00:00Z', ultima: '2026-09-20T09:30:00Z' },
    eventos: [{ cuando: '2026-09-20T08:00:00Z', ip: '192.168.1.30', tipo: 'conexion' }],
  },
  // Espera en curso SIN carpeta: pinta el bloque "Elige carpeta" dentro del
  // panel (`.espera-carpeta`).
  'espera-sin-carpeta': {
    esperando: true,
    caducado: false,
    fase: 'esperando',
    carpeta_seleccionada: false,
    red: { hostname: 'organizer.local', puerto: 80, ips: [] },
    fotos: null,
    eventos: [],
  },
  // Estadillo YA recibido: ejemplo REAL (`/tmp/claude-1000/estadillo-real-20260920.csv`,
  // CSV recibido de verdad en la Pi el 2026-09-20, piloto Rebeca/M300, 2
  // vuelos). El resumen (planta/fecha/pilotos/drones/n_vuelos) sale de
  // `atom_core.estadillo.read_estadillo_info` sobre ese fichero tal cual;
  // `fotos`/`carpeta`/`validacion` se han completado a mano para el ejemplo
  // (no llegaron con el CSV real) — ver `estadillo-ejemplo.json`.
  'espera-recibido': {
    esperando: true,
    caducado: false,
    fase: 'recibido_ok',
    recibido: true,
    rutas: ['/home/pi/.config/atom-organizer/estadillos_recibidos/20260920_estadillo_Rebeca.csv'],
    errores: [],
    resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
    fotos: { total: 58, primera: '2026-09-20T08:01:12+02:00', ultima: '2026-09-20T08:57:40+02:00', calculando: false },
    carpeta_seleccionada: true,
    validacion: {
      ok: true,
      vuelos: [
        { id: 'PB1_V1', fotos: 30, esperado: 30, estado: 'ok' },
        { id: 'PB1_V2', fotos: 28, esperado: 28, estado: 'ok' },
      ],
      fotos_fuera: 0,
      avisos: [],
    },
  },
  // Mismo estadillo real, pero con la validación en rojo/ámbar (mockeada: el
  // backend de `validacion` todavía no existe — pedido explícito de la
  // ampliación, "mockea validacion en tests/e2e con casos ok y con
  // problemas").
  'espera-recibido-avisos': {
    esperando: true,
    caducado: false,
    fase: 'recibido_ok',
    recibido: true,
    rutas: ['/home/pi/.config/atom-organizer/estadillos_recibidos/20260920_estadillo_Rebeca.csv'],
    errores: [],
    resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
    fotos: { total: 12, primera: '2026-09-20T08:01:12+02:00', ultima: '2026-09-20T08:20:00+02:00', calculando: false },
    carpeta_seleccionada: true,
    validacion: {
      ok: false,
      vuelos: [
        { id: 'PB1_V1', fotos: 12, esperado: 30, estado: 'pocas_fotos' },
        { id: 'PB1_V2', fotos: 0, esperado: 28, estado: 'sin_fotos' },
      ],
      fotos_fuera: 3,
      avisos: ['PB1_V2 no tiene fotos en la carpeta'],
    },
  },
  // Rechazado con motivo explícito del backend (pedido de Rodrigo: "si no ha
  // elegido carpeta dice rechazado sin decir por qué").
  'espera-rechazado-motivo': {
    esperando: true,
    caducado: false,
    fase: 'rechazado',
    errores: [],
    motivo: 'No hay carpeta seleccionada en el Organizer',
  },
}

// Tras «OK, seguir» del modal de `EsperaEstadillo`, el resumen no
// desaparece: `KioskScreen` sigue mostrándolo en `EstadilloField`
// (`infoRecibido`), ya dentro de la pantalla normal de "Organizar" (bug
// real reportado por Rodrigo: la tarjeta salía cortada por debajo sin poder
// hacer scroll con el dedo — el panel resistivo de la Pi no genera gesto de
// arrastre). 3 vuelos para reproducir el caso que no cabía.
const estadilloInfo3Vuelos = {
  origen: 'recibido',
  planta: 'PRUEBA_SIM',
  fechas: ['2026-09-23'],
  pilotos: ['Rebeca'],
  drones: ['DJI M300'],
  numVuelos: 3,
  vuelos: [
    { n: '1', inicio: '09:00', fin: '09:40', duracionMin: 40 },
    { n: '2', inicio: '10:00', fin: '10:35', duracionMin: 35 },
    { n: '3', inicio: '11:00', fin: '11:20', duracionMin: 20 },
  ],
  tiempoTotalMin: 95,
  avisos: [],
}

const estadoEsperaInicial = escenarios[escenario] ? { ...escenarios[escenario] } : { ...estadoEsperaPorDefecto }
let estadoEspera = { ...estadoEsperaInicial }

window.pywebview = {
  api: {
    // Como el backend real (`estadillo_espera_iniciar` sustituye el estado
    // entero): sin esto, StrictMode (monta→limpia→monta) deja el estado en
    // "caducado" del primer ciclo y el botón "Volver a esperar" del test
    // nunca vuelve a ver la fase "esperando".
    estadillo_espera_iniciar: async () => {
      estadoEspera = { ...estadoEsperaInicial }
      return { ok: true }
    },
    estadillo_espera_cancelar: async () => {
      estadoEspera = { ...estadoEsperaPorDefecto }
      return { ok: true }
    },
    estadillo_espera_estado: async () => estadoEspera,
    estadillo_espera_carpeta: async (carpeta) => {
      estadoEspera = { ...estadoEspera, carpeta_seleccionada: Boolean(carpeta) }
      return { ok: true }
    },
  },
}

const props = {
  status: { logged_in: true, email: 'rebeca@ejemplo.com', nombre: 'Rebeca', picture: null, estado: 'ok', pendientes: 0 },
  carpeta: escenario === 'espera-sin-carpeta' ? '' : '/home/pi/vuelo/PLANTA',
  onPickCarpeta: async () => '/home/pi/vuelo/PLANTA',
  inspecciones: [],
  inspeccion: null,
  onSelectInspeccion: () => {},
  onActualizarInspecciones: () => {},
  estadillo: escenario === 'tarjeta-recibida' ? ['/home/pi/vuelo/estadillo.csv'] : [],
  estadilloInfo: escenario === 'tarjeta-recibida' ? estadilloInfo3Vuelos : null,
  onEstadillo: () => {},
  onOrganizar: () => {},
  onSubirCrudo: () => {},
  onComprobarSubida: null,
  onRefreshStatus: () => {},
  busy: false,
  progreso: null,
  resultado: null,
  onCerrarResultado: () => {},
  onRunTask: () => {},
  // No hace falta forzar el paso inicial salvo para la tarjeta ya recibida
  // (no hay espera en curso que retomar, así que el efecto de "retomar" no
  // se dispara solo): el propio efecto de "retomar" de `KioskScreen` (lee
  // `estadillo_espera_estado` al montar) ya salta al paso "organizar" con
  // `esperandoEstadillo=true` en los demás escenarios, que simulan una
  // espera en curso — el mismo camino que sigue la Pi de verdad tras
  // recargar Chromium con una espera activa.
  accionInicial: escenario === 'tarjeta-recibida' ? 'organizar' : null,
}

// Sin StrictMode: la Pi corre el build de producción (`npm run build`), que
// no monta-limpia-remonta los efectos. Con StrictMode, la limpieza del
// primer montaje de `EsperaEstadillo` llama a `estadillo_espera_cancelar` y
// dejaba el estado en "caducado" antes de que llegara el montaje real — un
// falso positivo del arnés, no un bug de la app.
createRoot(document.getElementById('root')).render(<KioskScreen {...props} />)
