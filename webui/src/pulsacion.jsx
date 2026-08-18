import { useCallback, useEffect, useRef, useState } from 'react'

// Los dos gestos comparten umbral: por debajo de esto el dedo se considera
// quieto (es un toque), por encima esta scrolleando.
export const UMBRAL_REM = 0.625

export function pxDeRem(rem) {
  const base = parseFloat(getComputedStyle(document.documentElement).fontSize)
  return rem * (base || 16)
}

// La duracion de la pulsacion larga vive en CSS (--pulsacion) para que la
// animacion de relleno y el temporizador no se puedan desincronizar: JS la lee
// de ahi en vez de tener su propia copia.
export function msDePulsacion() {
  const v = getComputedStyle(document.documentElement).getPropertyValue('--pulsacion').trim()
  if (v.endsWith('ms')) return parseFloat(v) || 700
  if (v.endsWith('s')) return (parseFloat(v) || 0.7) * 1000
  return 700
}

// Boton generico con soporte de pulsacion larga. Con puntero de raton (el
// panel resistivo de la Pi) el click se sintetiza al soltar, asi que en modo
// servidor la activacion pasa a ser pulsacion larga: hay que mantener el dedo
// quieto ~1s, con el boton rellenandose como feedback, y la accion salta al
// cumplirse el tiempo (no al soltar, que es justo el momento ambiguo). El
// click queda neutralizado. En escritorio (larga=false) se mantiene el click
// de siempre.
//
// `cancelarAlMover` solo aplica cuando `larga` es cierto: en listas con gesto
// de scroll (FolderPicker) hay que desambiguar moviendo el dedo; en botones
// sueltos del kiosco no hay ese gesto y cancelar por el temblor del dedo en
// el panel resistivo dejaria botones que no responden.
export default function BotonLargo({ className = '', larga, cancelarAlMover = false, onActivar, children, ...rest }) {
  const [pulsando, setPulsando] = useState(false)
  const temporizador = useRef(null)
  const y0 = useRef(0)

  const cancelar = useCallback(() => {
    if (temporizador.current) clearTimeout(temporizador.current)
    temporizador.current = null
    setPulsando(false)
  }, [])

  // Si el componente se recarga, el boton se desmonta con el temporizador vivo.
  useEffect(() => cancelar, [cancelar])

  if (!larga) {
    return (
      <button type="button" className={className} onClick={onActivar} {...rest}>
        {children}
      </button>
    )
  }

  return (
    <button
      type="button"
      className={pulsando ? `${className} pulsable pulsando` : `${className} pulsable`}
      onPointerDown={(e) => {
        y0.current = e.clientY
        setPulsando(true)
        temporizador.current = setTimeout(() => {
          temporizador.current = null
          setPulsando(false)
          onActivar()
        }, msDePulsacion())
      }}
      onPointerMove={(e) => {
        if (cancelarAlMover && temporizador.current && Math.abs(e.clientY - y0.current) > pxDeRem(UMBRAL_REM)) {
          cancelar()
        }
      }}
      onPointerUp={cancelar}
      onPointerCancel={cancelar}
      onPointerLeave={cancelar}
      onClick={(e) => e.preventDefault()}
      {...rest}
    >
      {children}
    </button>
  )
}
