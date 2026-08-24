import { useEffect, useState } from 'react'
import { api } from './bridge.js'
import EstadoRed from './EstadoRed.jsx'

const INTERVALO_MS = 10000

// Icono de disco/USB externo, mismo estilo trazado a mano que `IconoWifi` /
// `IconoCable` de EstadoRed.jsx: rectangulo del disco + conector USB.
function IconoDisco() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <rect x="3" y="8" width="18" height="12" rx="2" />
      <path d="M8 8V5a1 1 0 011-1h2v2M14 8V5a1 1 0 00-1-1h-1" />
      <path d="M7 14h.01M11 14h6" />
    </svg>
  )
}

// Indicador de disco externo conectado/desconectado: barra superior del
// kiosco, junto a `EstadoRed`. Solo informa si hay disco o no, sin nombre ni
// espacio libre (eso ya lo da la app "Ajustes" si hace falta detalle).
function IndicadorDisco() {
  const [estado, setEstado] = useState(null)

  useEffect(() => {
    let vivo = true
    const cargar = () => {
      // Mismo patron defensivo que EstadoRed: si el bridge no expone
      // `discoEstado` (mocks de otras pantallas), no debe tumbar el kiosco.
      try {
        Promise.resolve(api.discoEstado())
          .then((r) => { if (vivo && r?.ok) setEstado(r) })
          .catch(() => {
            // Se mantiene el ultimo estado conocido hasta el proximo tick.
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

  return (
    <div
      className={`kiosk-estado-disco${estado.conectado ? '' : ' kiosk-estado-disco-sin'}`}
      data-testid="kiosk-estado-disco"
    >
      <IconoDisco />
    </div>
  )
}

// Barra de indicadores del kiosco: red + disco externo. `compacto` la reduce
// a solo iconos (sin SSID), para pantallas con poco sitio como la de subida.
export default function BarraEstado({ compacto = false }) {
  return (
    <div className={`kiosk-barra-estado${compacto ? ' kiosk-barra-estado-compacta' : ''}`}>
      <EstadoRed compacto={compacto} />
      <IndicadorDisco />
    </div>
  )
}
