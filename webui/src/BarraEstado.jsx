import { useEffect, useState } from 'react'
import { api } from './bridge.js'
import EstadoRed from './EstadoRed.jsx'

const INTERVALO_MS = 10000

// Icono de pendrive USB: cuerpo macizo + conector metalico saliente con dos
// ranuras. Relleno en vez de trazo fino y ocupando casi todo el viewBox, para
// que se distinga de un vistazo en la pantalla de 480x320 (a trazo 2px y
// tamano de texto el disco duro anterior era irreconocible).
function IconoDisco() {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">
      {/* cuerpo */}
      <rect x="7" y="4" width="16" height="16" rx="3" fill="currentColor" />
      {/* conector: mismo color, separado del cuerpo por el hueco de x=6..7 */}
      <rect x="1" y="7.5" width="5" height="9" rx="1.5" fill="currentColor" />
      {/* ranuras del conector, caladas con el fondo del kiosco */}
      <rect x="2.2" y="9.4" width="2.6" height="1.6" rx="0.6" fill="var(--bg)" />
      <rect x="2.2" y="13" width="2.6" height="1.6" rx="0.6" fill="var(--bg)" />
      {/* tapa del cuerpo, para leer el pendrive y no un simple rectangulo */}
      <rect x="9.6" y="4" width="1.6" height="16" rx="0.8" fill="var(--bg)" opacity="0.55" />
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
