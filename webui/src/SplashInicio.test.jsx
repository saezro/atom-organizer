import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'

import SplashInicio from './SplashInicio.jsx'

describe('SplashInicio', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('al montar se pinta con el logo', () => {
    render(<SplashInicio visibleMs={10} salidaMs={10} />)
    const splash = screen.getByTestId('splash-inicio')
    expect(splash).toBeInTheDocument()
    expect(screen.getByAltText('')).toHaveAttribute('src', '/atom-logo.svg')
  })

  it('pasados visibleMs + salidaMs llama a onFin', () => {
    const onFin = vi.fn()
    render(<SplashInicio onFin={onFin} visibleMs={10} salidaMs={10} />)
    expect(onFin).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(20)
    })
    expect(onFin).toHaveBeenCalledTimes(1)
  })
})
