import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'

vi.mock('./bridge', () => ({
  api: {
    downloadUpdate: vi.fn().mockResolvedValue({ started: true }),
    installUpdate: vi.fn().mockResolvedValue({ ok: true }),
  },
  onUpdate: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:update', w)
    return () => window.removeEventListener('atom:update', w)
  },
}))

import UpdateModal from './UpdateModal'

const emitir = (detail) =>
  act(() => { window.dispatchEvent(new CustomEvent('atom:update', { detail })) })

const disponible = () =>
  emitir({ kind: 'available', data: { latest: '3.4.62', current: '3.4.61', can_install: true, asset_url: 'u', asset_size: 10 } })

beforeEach(() => vi.clearAllMocks())

describe('UpdateModal', () => {
  it('no se ve hasta que hay versión nueva', () => {
    render(<UpdateModal />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  // Antes había DOS clics: uno para descargar y otro, ya con la descarga hecha,
  // para instalar. Daniel se quedaba con la app abierta sin actualizar.
  it('instala sola al terminar la descarga, sin un segundo clic', async () => {
    const { api } = await import('./bridge')
    render(<UpdateModal />)
    disponible()
    emitir({ kind: 'downloaded' })
    await waitFor(() => expect(api.installUpdate).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/Instalar y reiniciar/i)).toBeNull()
  })

  it('no lanza el instalador dos veces aunque se repita el evento', async () => {
    const { api } = await import('./bridge')
    render(<UpdateModal />)
    disponible()
    emitir({ kind: 'downloaded' })
    emitir({ kind: 'downloaded' })
    await waitFor(() => expect(api.installUpdate).toHaveBeenCalledTimes(1))
  })

  it('muestra el error si el instalador no arranca', async () => {
    const { api } = await import('./bridge')
    api.installUpdate.mockResolvedValueOnce({ ok: false, error: 'no se pudo' })
    render(<UpdateModal />)
    disponible()
    emitir({ kind: 'downloaded' })
    expect(await screen.findByText(/no se pudo/i)).toBeTruthy()
  })
})
