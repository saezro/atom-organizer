import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import MovilRed from './MovilRed.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import { esRemoto, logFront } from './bridge.js'

// Errores del front -> log de Python (diagnostico por SSH).
let reenviando = false
function reenviar(nivel, mensaje, detalle) {
  if (reenviando) return
  reenviando = true
  try {
    logFront(nivel, mensaje, detalle)
  } finally {
    reenviando = false
  }
}
function texto(a) {
  if (a instanceof Error) return a.stack || a.message
  if (typeof a === 'string') return a
  try {
    return JSON.stringify(a)
  } catch {
    return String(a)
  }
}
window.addEventListener('error', (e) => {
  reenviar('error', `window.onerror: ${e.message} @ ${e.filename}:${e.lineno}:${e.colno}`, e.error?.stack)
})
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason
  reenviar('error', `unhandledrejection: ${r?.message || texto(r)}`, r?.stack)
})
for (const [nivel, metodo] of [['error', 'error'], ['warning', 'warn']]) {
  const original = console[metodo].bind(console)
  console[metodo] = (...args) => {
    original(...args)
    reenviar(nivel, `console.${metodo}: ${args.map(texto).join(' ')}`)
  }
}

// Modo de prueba: `?cursor` en la URL repone el puntero del raton dentro del
// kiosco (ver App.css). Solo para probar desde un navegador de escritorio; la
// Pi arranca su URL sin parametros y sigue con el cursor oculto.
if (new URLSearchParams(window.location.search).has('cursor')) {
  document.documentElement.setAttribute('data-cursor', '1')
}

// Desde el movil (hotspot de la Pi) NO se monta la app entera: el kiosco esta
// dimensionado para la pantalla de 480x320 y ademas dispara llamadas
// (cloudStatus, inspecciones...) que `METODOS_REMOTOS` bloquea a proposito.
// El movil solo tiene una tarea: meter la Pi en una wifi.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      {esRemoto() ? <MovilRed /> : <App />}
    </ErrorBoundary>
  </StrictMode>,
)
