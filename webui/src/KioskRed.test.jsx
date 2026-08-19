import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const redListar = vi.fn()
const redConectar = vi.fn()

vi.mock('./bridge.js', () => ({
  api: {
    redListar: (...args) => redListar(...args),
    redConectar: (...args) => redConectar(...args),
  },
}))

import KioskRed from './KioskRed.jsx'

function baseRedes(overrides = {}) {
  return {
    ok: true,
    actual: 'CASA-WIFI',
    redes: [
      { ssid: 'CASA-WIFI', senal: 90, segura: true, activa: true },
      { ssid: 'VECINO-ABIERTA', senal: 40, segura: false, activa: false },
    ],
    ...overrides,
  }
}

describe('KioskRed', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    redListar.mockResolvedValue(baseRedes())
    redConectar.mockResolvedValue({ ok: true })
  })

  it('carga y pinta la lista de redes', async () => {
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    expect(await screen.findByText('CASA-WIFI')).toBeInTheDocument()
    expect(screen.getByText('VECINO-ABIERTA')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-red-actual')).toHaveTextContent('Conectado: CASA-WIFI')
  })

  it('pinta el candado solo en la red segura', async () => {
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')
    expect(screen.getByTestId('kiosk-red-candado-CASA-WIFI')).toBeInTheDocument()
    expect(screen.queryByTestId('kiosk-red-candado-VECINO-ABIERTA')).not.toBeInTheDocument()
  })

  it('al tocar una red segura abre el teclado y permite escribir la contraseña', async () => {
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-CASA-WIFI'))

    expect(await screen.findByTestId('kiosk-red-password')).toBeInTheDocument()
    await user.click(screen.getByTestId('kiosk-tecla-h'))
    await user.click(screen.getByTestId('kiosk-tecla-o'))
    await user.click(screen.getByTestId('kiosk-tecla-l'))
    await user.click(screen.getByTestId('kiosk-tecla-a'))
    expect(screen.getByTestId('kiosk-red-password')).toHaveValue('hola')

    await user.click(screen.getByTestId('kiosk-red-conectar'))

    await waitFor(() => expect(redConectar).toHaveBeenCalledTimes(1))
    expect(redConectar).toHaveBeenCalledWith('CASA-WIFI', 'hola')
  })

  it('conecta directo a una red abierta sin pedir contraseña', async () => {
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('VECINO-ABIERTA')

    await user.click(screen.getByTestId('kiosk-red-VECINO-ABIERTA'))

    await waitFor(() => expect(redConectar).toHaveBeenCalledTimes(1))
    expect(redConectar).toHaveBeenCalledWith('VECINO-ABIERTA', undefined)
  })

  it('pinta el error si redConectar falla', async () => {
    redConectar.mockResolvedValue({ ok: false, error: 'contraseña incorrecta' })
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-CASA-WIFI'))
    await screen.findByTestId('kiosk-red-password')
    await user.click(screen.getByTestId('kiosk-tecla-a'))
    await user.click(screen.getByTestId('kiosk-red-conectar'))

    expect(await screen.findByTestId('kiosk-red-password-error')).toHaveTextContent('contraseña incorrecta')
  })

  it('pinta el error si no se pudo listar redes', async () => {
    redListar.mockResolvedValue({ ok: false, error: 'WiFi no disponible' })
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    expect(await screen.findByTestId('kiosk-red-error')).toHaveTextContent('WiFi no disponible')
  })
})
