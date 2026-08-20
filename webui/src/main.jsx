import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import MovilRed from './MovilRed.jsx'
import { esRemoto } from './bridge.js'

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
    {esRemoto() ? <MovilRed /> : <App />}
  </StrictMode>,
)
