import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import BotonToque from './pulsacion.jsx'

// jsdom no implementa `setPointerCapture`/`hasPointerCapture` (Chromium sí,
// tanto en el panel resistivo de la Pi como en WebView2/Qt de Windows): sin
// este stub, TODO gesto se comportaria como si la captura fuera imposible,
// que es justo el caso excepcional que cubre el ultimo test de este fichero.
const CAPTURA_ORIGINAL = window.HTMLElement.prototype.setPointerCapture
function conCapturaSoportada() {
  window.HTMLElement.prototype.setPointerCapture = function setPointerCapture() {}
}
function sinCapturaSoportada() {
  delete window.HTMLElement.prototype.setPointerCapture
}

// Estos tests cubren el bug del PIN del kiosco (panel tactil resistivo
// 480x320): sin `setPointerCapture`, un temblor del dedo que saca el toque
// del borde de la tecla antes de soltar perdia el toque o activaba la tecla
// vecina. Ver `pulsacion.jsx` (BotonToque, modo `tactil`).
function renderBoton(props = {}) {
  const onActivar = vi.fn()
  render(
    <BotonToque tactil onActivar={onActivar} {...props}>
      Tecla
    </BotonToque>,
  )
  return { boton: screen.getByRole('button'), onActivar }
}

describe('BotonToque (modo tactil) — captura del puntero', () => {
  beforeEach(() => { conCapturaSoportada() })
  afterEach(() => {
    if (CAPTURA_ORIGINAL) window.HTMLElement.prototype.setPointerCapture = CAPTURA_ORIGINAL
    else delete window.HTMLElement.prototype.setPointerCapture
  })

  it('pointerdown + leave + up en la misma tecla, con movimiento por debajo del umbral, activa', () => {
    const { boton, onActivar } = renderBoton()
    fireEvent.pointerDown(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    // El temblor del resistivo saca el dedo del borde de la tecla antes de
    // soltar: con la captura del puntero esto ya no debe cancelar el toque.
    fireEvent.pointerLeave(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    fireEvent.pointerUp(boton, { pointerId: 1, clientX: 12, clientY: 11 })
    expect(onActivar).toHaveBeenCalledTimes(1)
  })

  it('un pointerup sin pointerdown previo en esta tecla NO activa', () => {
    const { boton, onActivar } = renderBoton()
    // Simula lo que antes activaba la tecla vecina: un pointerup que le
    // llega a un boton que nunca tuvo su propio pointerdown.
    fireEvent.pointerUp(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    expect(onActivar).not.toHaveBeenCalled()
  })

  it('un movimiento mayor que el umbral (arrastre real) NO activa', () => {
    const { boton, onActivar } = renderBoton()
    fireEvent.pointerDown(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    fireEvent.pointerUp(boton, { pointerId: 1, clientX: 10, clientY: 200 })
    expect(onActivar).not.toHaveBeenCalled()
  })

  it('pointercancel NO activa', () => {
    const { boton, onActivar } = renderBoton()
    fireEvent.pointerDown(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    fireEvent.pointerCancel(boton, { pointerId: 1 })
    fireEvent.pointerUp(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    expect(onActivar).not.toHaveBeenCalled()
  })

  // Regresion: en un dispositivo SIN soporte de `setPointerCapture` (o que
  // la pierde sin avisar), un `pointerleave` dejaba el boton "armado" para
  // siempre -`presionado`/`punteroId` nunca se reseteaban-, así que ni el
  // `pointerup` que cae fuera lo cerraba ni el siguiente toque LIMPIO en
  // este mismo boton volvía a activarlo.
  it('sin captura del puntero, un pointerleave desarma el toque: el siguiente toque limpio si activa', () => {
    sinCapturaSoportada()
    const { boton, onActivar } = renderBoton()
    fireEvent.pointerDown(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    fireEvent.pointerLeave(boton, { pointerId: 1, clientX: 10, clientY: 10 })
    // El `pointerup` real cae fuera del boton (no llega aquí, como pasaría
    // sin captura): el gesto queda desarmado por el propio `pointerleave`.
    expect(onActivar).not.toHaveBeenCalled()

    // El boton debe seguir respondiendo a un toque nuevo y limpio.
    fireEvent.pointerDown(boton, { pointerId: 2, clientX: 10, clientY: 10 })
    fireEvent.pointerUp(boton, { pointerId: 2, clientX: 10, clientY: 10 })
    expect(onActivar).toHaveBeenCalledTimes(1)
  })
})
