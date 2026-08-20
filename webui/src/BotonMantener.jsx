import { useCallback, useEffect, useRef, useState } from 'react'

// Duracion del mantenido pulsado. Vive en CSS (--mantener) y JS la lee de ahi
// para que el timeout de JS y la animacion de relleno de CSS nunca se
// desincronicen — mismo patron que msDeOnda() en pulsacion.jsx con --onda.
export function msDeMantener() {
  const v = getComputedStyle(document.documentElement).getPropertyValue('--mantener').trim()
  if (v.endsWith('ms')) return parseFloat(v) || 1000
  if (v.endsWith('s')) return (parseFloat(v) || 1) * 1000
  return 1000
}

// Boton de confirmacion para acciones DESTRUCTIVAS/irreversibles del kiosco
// (quitar, borrar, resetear...). El panel resistivo de la Pi da toques
// fantasma, y BotonToque dispara al instante: un roce accidental ha bastado
// para borrar un modelo del "% de recorte por dron" sin querer. Este boton
// exige MANTENER PULSADO ~1s (--mantener), con un relleno progresivo
// (.mantenible/.manteniendo, App.css) que avisa de cuanto falta. Soltar,
// salir del boton o cancelar antes de completarse NO dispara `onActivar` y
// el relleno revierte; solo se dispara una vez, al completar el segundo.
//
// Solo para pantallas del KIOSCO: a diferencia de BotonToque no hay caso
// "escritorio" con click normal salvo el fallback de `!tactil` (mismo motivo
// que BotonToque: mantiene la API calcable y testeable en componentes que
// se comparten entre kiosco y escritorio, como EstadilloField).
export default function BotonMantener({
  className = '',
  tactil,
  onActivar,
  children,
  disabled,
  etiquetaConfirmar = 'CONFIRMAR',
  ...rest
}) {
  const [manteniendo, setManteniendo] = useState(false)
  const temporizador = useRef(null)

  const cancelar = useCallback(() => {
    if (temporizador.current) clearTimeout(temporizador.current)
    temporizador.current = null
    setManteniendo(false)
  }, [])

  // El boton puede desmontarse (navegar de pantalla) a medio mantener.
  useEffect(() => cancelar, [cancelar])

  if (!tactil) {
    return (
      <button type="button" className={className} disabled={disabled} onClick={onActivar} {...rest}>
        {children}
      </button>
    )
  }

  return (
    <button
      type="button"
      className={manteniendo ? `${className} mantenible manteniendo` : `${className} mantenible`}
      disabled={disabled}
      onPointerDown={() => {
        if (disabled || temporizador.current) return
        setManteniendo(true)
        temporizador.current = setTimeout(() => {
          temporizador.current = null
          setManteniendo(false)
          onActivar()
        }, msDeMantener())
      }}
      onPointerUp={cancelar}
      onPointerCancel={cancelar}
      onPointerLeave={cancelar}
      // El click sintetizado al soltar llega DESPUES de onPointerUp/cancelar;
      // ya hemos decidido nosotros si se activa o no, asi que se neutraliza.
      onClick={(e) => e.preventDefault()}
      {...rest}
    >
      {manteniendo ? etiquetaConfirmar : children}
    </button>
  )
}
