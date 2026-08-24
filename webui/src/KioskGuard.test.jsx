import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'

const pinEstado = vi.fn()
const cloudStatus = vi.fn()

const puente = {
  isServerMode: () => false,
  api: {
    pinEstado,
    pinVerificar: vi.fn().mockResolvedValue({ ok: true }),
    pinFijar: vi.fn().mockResolvedValue({ ok: true }),
    pinCambiar: vi.fn().mockResolvedValue({ ok: true }),
    cloudStatus,
    cloudLogout: vi.fn().mockResolvedValue({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
  },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskGuard } = await import('./KioskGuard.jsx')

describe('KioskGuard', () => {
  beforeEach(() => {
    pinEstado.mockReset().mockResolvedValue({ ok: true, hay_pin: false, bloqueado: false, espera_segundos: 0 })
  })

  it('con PIN fijado pinta el bloqueo y no los hijos', async () => {
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByTestId('kiosk-pin')).toBeTruthy())
    expect(screen.queryByText('contenido')).toBeNull()
  })

  it('sin PIN y con sesion obliga a fijarlo', async () => {
    render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByText(/pin nuevo/i)).toBeTruthy())
    expect(screen.queryByText('contenido')).toBeNull()
  })

  it('sin PIN y sin sesion deja usar el kiosco', async () => {
    render(<KioskGuard status={{ logged_in: false }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByText('contenido')).toBeTruthy())
  })

  it('tras diez minutos de inactividad vuelve a bloquear', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado={false} desbloqueadoInicial><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      expect(screen.getByText('contenido')).toBeTruthy()
      await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000 + 1000) })
      expect(screen.queryByText('contenido')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('no bloquea mientras hay una subida en curso', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado desbloqueadoInicial><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      await act(async () => { vi.advanceTimersByTime(20 * 60 * 1000) })
      expect(screen.getByText('contenido')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })
})
