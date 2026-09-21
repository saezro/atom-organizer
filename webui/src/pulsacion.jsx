import { useCallback, useEffect, useRef, useState } from 'react'

// Por debajo de este recorrido el dedo se considera quieto (es un toque); por
// encima esta scrolleando. Generoso a proposito: el panel resistivo de la Pi
// tiembla bastante en un toque legitimo, y un umbral corto se traduce en
// toques muertos, que es peor que un scroll de mas.
export const UMBRAL_REM = 1

export function pxDeRem(rem) {
  const base = parseFloat(getComputedStyle(document.documentElement).fontSize)
  return rem * (base || 16)
}

// Duracion del destello de confirmacion. Vive en CSS (--onda) y JS la lee de
// ahi para que la clase no se quite antes de que termine la animacion.
export function msDeOnda() {
  const v = getComputedStyle(document.documentElement).getPropertyValue('--onda').trim()
  if (v.endsWith('ms')) return parseFloat(v) || 200
  if (v.endsWith('s')) return (parseFloat(v) || 0.2) * 1000
  return 200
}

// Boton generico para el panel tactil de la Pi. Chromium ve ese panel como un
// RATON, asi que el click se sintetiza al soltar y no se puede distinguir un
// toque de un scroll solo con el click. Lo que los distingue es el RECORRIDO,
// no el tiempo: se activa al soltar, al instante, salvo que el dedo se haya
// movido mas de UMBRAL_REM. Antes esto era una pulsacion larga de 700 ms y
// resultaba lentisima para lo unico que hace falta, que es abrir una carpeta.
//
// `cancelarAlMover` solo aplica cuando `tactil` es cierto: en listas con gesto
// de scroll (FolderPicker) hay que descartar el arrastre; en botones sueltos
// del kiosco no hay ese gesto, y descartar por el temblor del dedo dejaria
// botones que no responden. En escritorio (tactil=false) se usa el click de
// siempre y nada de esto entra.
//
// `onPulsarLargo` es opcional: si se pasa, mantener pulsado `duracionLargoMs`
// lo dispara EN VEZ de `onActivar` (p.ej. borrar-todo en la tecla Borrar del
// PIN). Sin el prop, el boton se comporta exactamente igual que antes.
export default function BotonToque({
  className = '',
  tactil,
  cancelarAlMover = false,
  onActivar,
  onPulsarLargo,
  duracionLargoMs = 600,
  children,
  ...rest
}) {
  const [tocando, setTocando] = useState(false)
  const arrastrado = useRef(false)
  const destello = useRef(null)
  const largo = useRef(null)
  const largoDisparado = useRef(false)

  const apagar = useCallback(() => {
    if (destello.current) clearTimeout(destello.current)
    destello.current = null
    setTocando(false)
  }, [])

  const cancelarLargo = useCallback(() => {
    if (largo.current) clearTimeout(largo.current)
    largo.current = null
  }, [])

  const armarLargo = useCallback(() => {
    if (!onPulsarLargo) return
    cancelarLargo()
    largo.current = setTimeout(() => {
      largo.current = null
      largoDisparado.current = true
      apagar()
      onPulsarLargo()
    }, duracionLargoMs)
  }, [onPulsarLargo, duracionLargoMs, cancelarLargo, apagar])

  // El boton puede desmontarse (navegar de carpeta) con el destello o la
  // pulsacion larga vivos.
  useEffect(() => () => { apagar(); cancelarLargo() }, [apagar, cancelarLargo])

  if (!tactil) {
    return (
      <button
        type="button"
        className={className}
        onMouseDown={armarLargo}
        onMouseUp={cancelarLargo}
        onMouseLeave={cancelarLargo}
        onClick={(e) => {
          if (largoDisparado.current) { largoDisparado.current = false; e.preventDefault(); return }
          onActivar(e)
        }}
        {...rest}
      >
        {children}
      </button>
    )
  }

  return (
    <button
      type="button"
      className={tocando ? `${className} pulsable pulsando` : `${className} pulsable`}
      onPointerDown={(e) => {
        arrastrado.current = false
        armarLargo()
        e.currentTarget.dataset.y0 = String(e.clientY)
        // La onda nace donde cae el dedo, no en el centro: se lee como "he
        // tocado AQUI". Va por custom properties para que la animacion siga
        // viviendo entera en CSS. El diametro se calcula a la esquina mas
        // lejana para que cubra el boton entero.
        const r = e.currentTarget.getBoundingClientRect()
        const x = e.clientX - r.left
        const y = e.clientY - r.top
        const radio = Math.hypot(Math.max(x, r.width - x), Math.max(y, r.height - y))
        e.currentTarget.style.setProperty('--onda-x', `${x}px`)
        e.currentTarget.style.setProperty('--onda-y', `${y}px`)
        e.currentTarget.style.setProperty('--onda-d', `${radio * 2}px`)
        setTocando(true)
        // El destello se apaga solo, no al soltar: un toque rapido dura menos
        // que la animacion y si no se veria cortado a medias.
        if (destello.current) clearTimeout(destello.current)
        destello.current = setTimeout(() => {
          destello.current = null
          setTocando(false)
        }, msDeOnda())
      }}
      onPointerMove={(e) => {
        if (!cancelarAlMover || arrastrado.current) return
        const y0 = Number(e.currentTarget.dataset.y0 ?? e.clientY)
        if (Math.abs(e.clientY - y0) > pxDeRem(UMBRAL_REM)) {
          arrastrado.current = true  // era un scroll: al soltar no se activa
          cancelarLargo()
          apagar()
        }
      }}
      onPointerUp={() => {
        cancelarLargo()
        if (largoDisparado.current) {
          largoDisparado.current = false
          return
        }
        if (arrastrado.current) {
          arrastrado.current = false
          return
        }
        onActivar()
      }}
      onPointerCancel={() => { arrastrado.current = false; cancelarLargo(); apagar() }}
      onPointerLeave={() => { arrastrado.current = true; cancelarLargo(); apagar() }}
      // El click sintetizado al soltar llega DESPUES de onPointerUp; ya hemos
      // actuado nosotros, asi que se neutraliza para no activar dos veces.
      onClick={(e) => e.preventDefault()}
      {...rest}
    >
      {children}
    </button>
  )
}
