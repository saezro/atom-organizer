import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'

import BotonMantener from './BotonMantener.jsx'

describe('BotonMantener — confirmacion por mantener pulsado', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('soltar antes de completar el segundo NO dispara onActivar', () => {
    const onActivar = vi.fn()
    render(
      <BotonMantener tactil onActivar={onActivar} data-testid="btn">
        Quitar
      </BotonMantener>
    )
    const boton = screen.getByTestId('btn')

    fireEvent.pointerDown(boton)
    act(() => vi.advanceTimersByTime(600)) // menos del segundo completo
    fireEvent.pointerUp(boton)
    act(() => vi.advanceTimersByTime(1000)) // si quedase un timer vivo, saltaria aqui

    expect(onActivar).not.toHaveBeenCalled()
  })

  it('mantener el segundo completo SI dispara onActivar', () => {
    const onActivar = vi.fn()
    render(
      <BotonMantener tactil onActivar={onActivar} data-testid="btn">
        Quitar
      </BotonMantener>
    )
    const boton = screen.getByTestId('btn')

    fireEvent.pointerDown(boton)
    act(() => vi.advanceTimersByTime(1000))

    expect(onActivar).toHaveBeenCalledTimes(1)
  })

  it('solo dispara una vez, aunque el pointerdown/up se repita tras completarse', () => {
    const onActivar = vi.fn()
    render(
      <BotonMantener tactil onActivar={onActivar} data-testid="btn">
        Quitar
      </BotonMantener>
    )
    const boton = screen.getByTestId('btn')

    fireEvent.pointerDown(boton)
    act(() => vi.advanceTimersByTime(1000))
    fireEvent.pointerUp(boton)

    // Un segundo mantenido completo, seguido de otro toque corto que no llega
    // a completar: sigue siendo una sola activacion en total.
    fireEvent.pointerDown(boton)
    act(() => vi.advanceTimersByTime(300))
    fireEvent.pointerUp(boton)

    expect(onActivar).toHaveBeenCalledTimes(1)
  })
})
