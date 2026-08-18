import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Modo de prueba: `?cursor` en la URL repone el puntero del raton dentro del
// kiosco (ver App.css). Solo para probar desde un navegador de escritorio; la
// Pi arranca su URL sin parametros y sigue con el cursor oculto.
if (new URLSearchParams(window.location.search).has('cursor')) {
  document.documentElement.setAttribute('data-cursor', '1')
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
