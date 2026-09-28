import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import ErrorBoundary from './ErrorBoundary.jsx'

function Bomba() {
  throw new Error('boom')
}

describe('ErrorBoundary', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('pinta la app normalmente sin error', () => {
    render(
      <ErrorBoundary>
        <span>todo bien</span>
      </ErrorBoundary>,
    )
    expect(screen.getByText('todo bien')).toBeTruthy()
  })

  it('un error de render no deja la pantalla en blanco: muestra el aviso y recarga sola', async () => {
    vi.useFakeTimers()
    // React 18 vuelca el error del boundary a consola aunque lo capture: se
    // silencia para no ensuciar la salida del test.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const reload = vi.fn()
    const locOriginal = window.location
    Object.defineProperty(window, 'location', {
      value: { ...locOriginal, reload },
      configurable: true,
    })
    try {
      render(
        <ErrorBoundary>
          <Bomba />
        </ErrorBoundary>,
      )
      expect(screen.getByTestId('error-boundary')).toBeTruthy()
      expect(reload).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(5000)
      expect(reload).toHaveBeenCalledTimes(1)
    } finally {
      Object.defineProperty(window, 'location', { value: locOriginal, configurable: true })
    }
  })
})
