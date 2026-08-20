import BotonToque from './pulsacion.jsx'

// Botón «atrás» del kiosco: solo la flecha, en naranja de marca. Sin texto
// porque en 480x320 cada píxel de la cabecera cuenta y la flecha ya es
// inequívoca; el nombre accesible va en `aria-label` (`etiqueta`).
// Único sitio donde se define el atrás: antes cada pantalla repetía el
// literal «← Atrás» (y una decía «← Listo»).
export default function BotonAtras({ tactil, disabled, onActivar, etiqueta = 'Atrás', ...resto }) {
  return (
    <BotonToque
      className="kiosk-atras"
      tactil={tactil}
      disabled={disabled}
      onActivar={onActivar}
      aria-label={etiqueta}
      {...resto}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
           strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M15 6l-6 6 6 6" />
      </svg>
    </BotonToque>
  )
}
