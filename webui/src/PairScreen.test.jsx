// Pantalla de emparejamiento por QR (kiosco de la Raspberry Pi, sin
// navegador propio para el login OAuth): arranca `cloud_pair_start`, pinta el
// QR y sondea `cloud_pair_poll` cada 2 s hasta 'listo'/'expirado'/error.
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const cloudPairStart = vi.fn()
const cloudPairPoll = vi.fn()

vi.mock('./bridge.js', () => ({
  api: { cloudPairStart: (...a) => cloudPairStart(...a), cloudPairPoll: (...a) => cloudPairPoll(...a) },
  isServerMode: () => true,
}))

const PairScreen = (await import('./PairScreen.jsx')).default

// Deja correr las promesas pendientes (respuesta del bridge mockeado) y, con
// ellas, los cambios de estado que React tiene que aplicar. Con fake timers
// `await` a secas no basta: hace falta ceder el microtask queue de verdad.
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

describe('PairScreen', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    cloudPairStart.mockReset()
    cloudPairPoll.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('arranca el vinculo al montar y pinta el QR de la url recibida', async () => {
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p1', url: 'https://atom/pair/p1', expires_in: 300 })
    cloudPairPoll.mockResolvedValue({ estado: 'pendiente' })

    render(<PairScreen />)
    await flush()

    expect(cloudPairStart).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('img', { name: /codigo qr/i })).toBeInTheDocument()
    expect(screen.getByText(/escanea con el móvil/i)).toBeInTheDocument()
  })

  it('sondea cada 2s mientras el estado es pendiente, y para al llegar a listo', async () => {
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p1', url: 'https://atom/pair/p1', expires_in: 300 })
    cloudPairPoll
      .mockResolvedValueOnce({ estado: 'pendiente' })
      .mockResolvedValueOnce({ estado: 'listo', email: 'operador@aerotools.es' })

    const onPaired = vi.fn()
    render(<PairScreen onPaired={onPaired} />)
    await flush()

    // primer poll a los 2s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(cloudPairPoll).toHaveBeenCalledWith('p1')
    expect(cloudPairPoll).toHaveBeenCalledTimes(1)

    // segundo poll: llega 'listo'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(cloudPairPoll).toHaveBeenCalledTimes(2)
    expect(onPaired).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/vinculado como operador@aerotools\.es/i)).toBeInTheDocument()

    // ya no se sigue sondeando tras 'listo'
    cloudPairPoll.mockClear()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(cloudPairPoll).not.toHaveBeenCalled()
  })

  it('estado expirado ofrece "Reintentar", que rearranca desde cloud_pair_start', async () => {
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p1', url: 'https://atom/pair/p1', expires_in: 300 })
    cloudPairPoll.mockResolvedValue({ estado: 'expirado' })

    render(<PairScreen />)
    await flush()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(screen.getByText(/ha caducado/i)).toBeInTheDocument()

    cloudPairStart.mockClear()
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p2', url: 'https://atom/pair/p2', expires_in: 300 })
    // `tactil=true` (isServerMode mockeado): el boton se activa por
    // pointerdown+pointerup, como en el panel resistivo real, no por click.
    const boton = screen.getByText('Reintentar')
    await act(async () => {
      fireEvent.pointerDown(boton, { clientX: 0, clientY: 0 })
      fireEvent.pointerUp(boton)
    })
    await flush()

    expect(cloudPairStart).toHaveBeenCalledTimes(1)
  })

  it('un error de cloud_pair_start se enseña con "Reintentar"', async () => {
    cloudPairStart.mockResolvedValue({ ok: false, error: 'sin red' })

    render(<PairScreen />)
    await flush()

    expect(screen.getByText('sin red')).toBeInTheDocument()
    expect(screen.getByText('Reintentar')).toBeInTheDocument()
  })

  it('limpia el intervalo de sondeo al desmontar (sin fugas)', async () => {
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p1', url: 'https://atom/pair/p1', expires_in: 300 })
    cloudPairPoll.mockResolvedValue({ estado: 'pendiente' })
    const clearSpy = vi.spyOn(global, 'clearInterval')

    const { unmount } = render(<PairScreen />)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000) // deja el intervalo de poll ya vivo
    })

    unmount()
    expect(clearSpy).toHaveBeenCalled()

    // tras desmontar, ni un solo poll mas aunque pase tiempo
    cloudPairPoll.mockClear()
    await vi.advanceTimersByTimeAsync(10000)
    expect(cloudPairPoll).not.toHaveBeenCalled()
  })

  it('tolera hasta 9 fallos de red seguidos en el poll sin pasar a error', async () => {
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p1', url: 'https://atom/pair/p1', expires_in: 300 })
    cloudPairPoll.mockRejectedValue(new Error('network error'))

    render(<PairScreen />)
    await flush()

    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
    }
    expect(cloudPairPoll).toHaveBeenCalledTimes(3)
    expect(screen.getByText(/escanea con el móvil/i)).toBeInTheDocument()
    expect(screen.queryByText('Reintentar')).not.toBeInTheDocument()
  })

  it('se rinde y pasa a error tras 10 fallos de red seguidos en el poll', async () => {
    cloudPairStart.mockResolvedValue({ ok: true, pair_id: 'p1', url: 'https://atom/pair/p1', expires_in: 300 })
    cloudPairPoll.mockRejectedValue(new Error('network error'))

    render(<PairScreen />)
    await flush()

    for (let i = 0; i < 10; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
    }
    expect(cloudPairPoll).toHaveBeenCalledTimes(10)
    expect(screen.getByText('Reintentar')).toBeInTheDocument()
    expect(screen.queryByText(/escanea con el móvil/i)).not.toBeInTheDocument()
  })
})
