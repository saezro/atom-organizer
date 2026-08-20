import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const redListar = vi.fn()
const redConectar = vi.fn()

vi.mock('./bridge.js', () => ({
  esRemoto: () => true,
  api: {
    redListar: (...args) => redListar(...args),
    redConectar: (...args) => redConectar(...args),
  },
}))

import MovilRed from './MovilRed.jsx'

function baseRedes(overrides = {}) {
  return {
    ok: true,
    actual: 'CASA-WIFI',
    redes: [
      { ssid: 'CASA-WIFI', senal: 90, segura: true, activa: true, guardada: true },
      { ssid: 'VECINO-SEGURA', senal: 60, segura: true, activa: false, guardada: false },
    ],
    ...overrides,
  }
}

describe('MovilRed', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    redListar.mockResolvedValue(baseRedes())
    redConectar.mockResolvedValue({ ok: true })
  })

  it('lista las redes y muestra la red actual', async () => {
    render(<MovilRed />)
    expect(await screen.findByTestId('movil-red-CASA-WIFI')).toBeInTheDocument()
    expect(screen.getByTestId('movil-red-VECINO-SEGURA')).toBeInTheDocument()
    expect(screen.getByTestId('movil-red-actual')).toHaveTextContent('CASA-WIFI')
  })

  it('tocar una red segura no guardada abre la pantalla de contraseña', async () => {
    const user = userEvent.setup()
    render(<MovilRed />)
    await screen.findByTestId('movil-red-VECINO-SEGURA')

    await user.click(screen.getByTestId('movil-red-VECINO-SEGURA'))

    expect(await screen.findByTestId('movil-password')).toBeInTheDocument()
    expect(redConectar).not.toHaveBeenCalled()
  })

  it('tocar una red guardada conecta directo sin password ni pantalla de contraseña', async () => {
    const user = userEvent.setup()
    render(<MovilRed />)
    await screen.findByTestId('movil-red-CASA-WIFI')

    await user.click(screen.getByTestId('movil-red-CASA-WIFI'))

    expect(redConectar).toHaveBeenCalledWith('CASA-WIFI', undefined)
    expect(screen.queryByTestId('movil-password')).not.toBeInTheDocument()
  })

  it('conexion OK muestra la pantalla movil-listo con el SSID', async () => {
    const user = userEvent.setup()
    render(<MovilRed />)
    await screen.findByTestId('movil-red-CASA-WIFI')

    await user.click(screen.getByTestId('movil-red-CASA-WIFI'))

    const listo = await screen.findByTestId('movil-listo')
    expect(listo).toHaveTextContent('CASA-WIFI')
  })

  it('si redConectar rechaza (cae el hotspot) tambien muestra movil-listo', async () => {
    redConectar.mockRejectedValue(new Error('network error'))
    const user = userEvent.setup()
    render(<MovilRed />)
    await screen.findByTestId('movil-red-CASA-WIFI')

    await user.click(screen.getByTestId('movil-red-CASA-WIFI'))

    const listo = await screen.findByTestId('movil-listo')
    expect(listo).toHaveTextContent('CASA-WIFI')
  })
})
