import { useEffect, useState } from 'react'
import { api } from './bridge.js'

const INTERVALO_MS = 10000

// Chip de estado de red para la esquina del home. Solo informa (no es
// pulsable, para eso ya está la app "Red"), así que no usa BotonToque.
function IconoCable() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <path d="M7 21V13a2 2 0 012-2h6a2 2 0 012 2v8" />
      <path d="M9 21v-3M15 21v-3" />
      <path d="M9 11V7M15 11V7M9 7h6" />
    </svg>
  )
}

// 3 arcos concentricos (patron habitual de icono wifi) + punto. Los arcos por
// encima del nivel de senal se pintan atenuados, igual que hace `IconoSenal`
// de KioskRed.jsx con sus barras.
function IconoWifi({ nivel }) {
  const arcos = [
    { d: 'M3 8.5a15 15 0 0118 0', n: 3 },
    { d: 'M6.2 12.2a10.5 10.5 0 0111.6 0', n: 2 },
    { d: 'M9.6 15.8a5.8 5.8 0 014.8 0', n: 1 },
  ]
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      {arcos.map((a) => (
        <path key={a.n} d={a.d} opacity={a.n <= nivel ? 1 : 0.3} />
      ))}
      <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

function IconoWifiTachado() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <path d="M3 8.5a15 15 0 0118 0" opacity="0.3" />
      <path d="M6.2 12.2a10.5 10.5 0 0111.6 0" opacity="0.3" />
      <path d="M9.6 15.8a5.8 5.8 0 014.8 0" opacity="0.3" />
      <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
      <path d="M3 3l18 18" />
    </svg>
  )
}

export default function EstadoRed({ compacto = false }) {
  const [estado, setEstado] = useState(null)

  useEffect(() => {
    let vivo = true
    const cargar = () => {
      // Envuelto en try: si el bridge no expone `redConexion` (mocks de otras
      // pantallas que no conocen esta llamada), no debe tumbar el resto del
      // kiosco, igual que un fallo async se traga en el `.catch`.
      try {
        Promise.resolve(api.redConexion())
          .then((r) => { if (vivo && r?.ok) setEstado(r) })
          .catch(() => {
            // Sin conexion no hay nada que mostrar mejor que un icono de aviso
            // suelto: se mantiene el ultimo estado conocido hasta el proximo tick.
          })
      } catch {
        // idem: fallo sincrono al llamar, se ignora hasta el proximo tick.
      }
    }
    cargar()
    const id = setInterval(cargar, INTERVALO_MS)
    return () => { vivo = false; clearInterval(id) }
  }, [])

  // Sin dato todavia (primer render) o backend en error: nada a pantalla.
  if (!estado) return null

  if (estado.tipo === 'cable') {
    return (
      <div className="kiosk-estado-red" data-testid="kiosk-estado-red">
        <IconoCable />
        {!compacto && <span>Cable</span>}
      </div>
    )
  }

  if (estado.tipo === 'wifi') {
    const nivel = estado.senal >= 67 ? 3 : estado.senal >= 34 ? 2 : 1
    return (
      <div className="kiosk-estado-red" data-testid="kiosk-estado-red">
        <IconoWifi nivel={nivel} />
        {!compacto && <span>{estado.ssid} · {estado.senal}%</span>}
      </div>
    )
  }

  return (
    <div className="kiosk-estado-red kiosk-estado-red-sin" data-testid="kiosk-estado-red">
      <IconoWifiTachado />
      {!compacto && <span>Sin red</span>}
    </div>
  )
}
