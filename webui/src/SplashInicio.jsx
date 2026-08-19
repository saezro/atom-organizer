import { useEffect, useState } from 'react'

// Splash de arranque del kiosco: el logo de Atom aparece sobre negro y se
// desvanece hacia el menú. Es SOLO del front (el arranque del escritorio de la
// Pi, ~9-10 s antes de que salga Chromium, no se tapa desde aquí).
//
// El componente se autogestiona: monta -> `entrando` -> `saliendo` -> avisa por
// `onFin` para que el padre lo desmonte. Así App.jsx no lleva timers.
export const SPLASH_VISIBLE_MS = 1200
export const SPLASH_SALIDA_MS = 500

export default function SplashInicio({ onFin, visibleMs = SPLASH_VISIBLE_MS, salidaMs = SPLASH_SALIDA_MS }) {
  const [saliendo, setSaliendo] = useState(false)

  useEffect(() => {
    const aSalir = setTimeout(() => setSaliendo(true), visibleMs)
    const aFin = setTimeout(() => { if (onFin) onFin() }, visibleMs + salidaMs)
    return () => {
      clearTimeout(aSalir)
      clearTimeout(aFin)
    }
  }, [onFin, visibleMs, salidaMs])

  return (
    <div
      className={'splash-inicio' + (saliendo ? ' saliendo' : '')}
      data-testid="splash-inicio"
      role="presentation"
    >
      <img className="splash-inicio-logo" src="/atom-logo.svg" alt="" aria-hidden="true" />
      <div className="splash-inicio-marca">
        <span className="atom">ATOM</span> <span className="org">ORGANIZER</span>
      </div>
    </div>
  )
}
