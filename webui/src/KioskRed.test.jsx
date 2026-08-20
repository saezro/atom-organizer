import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const redListar = vi.fn()
const redConectar = vi.fn()
const redApActivar = vi.fn()
const redApDesactivar = vi.fn()
// `esRemoto` se mockea mutable (no una arrow fija) para que los tests del
// modo movil puedan conmutarla en caliente sin re-mockear el modulo entero.
let remoto = false

vi.mock('./bridge.js', () => ({
  esRemoto: () => remoto,
  api: {
    redListar: (...args) => redListar(...args),
    redConectar: (...args) => redConectar(...args),
    redApActivar: (...args) => redApActivar(...args),
    redApDesactivar: (...args) => redApDesactivar(...args),
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
    remoto = false
    redListar.mockResolvedValue(baseRedes())
    redConectar.mockResolvedValue({ ok: true })
    redApActivar.mockResolvedValue({
      ok: true,
      ssid: 'ATOM-Organizer',
      password: 'abc1234567',
      ip: '10.42.0.1',
      token: 'tok123',
    })
    redApDesactivar.mockResolvedValue({ ok: true })
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

  it('conecta directo a una red segura ya guardada, sin abrir el teclado', async () => {
    redListar.mockResolvedValue(baseRedes({
      redes: [
        { ssid: 'CASA-WIFI', senal: 90, segura: true, activa: true, guardada: true },
        { ssid: 'VECINO-ABIERTA', senal: 40, segura: false, activa: false },
      ],
    }))
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-CASA-WIFI'))

    await waitFor(() => expect(redConectar).toHaveBeenCalledTimes(1))
    expect(redConectar).toHaveBeenCalledWith('CASA-WIFI', undefined)
    expect(screen.queryByTestId('kiosk-red-password')).not.toBeInTheDocument()
  })

  it('una red segura no guardada sigue pidiendo la contraseña', async () => {
    redListar.mockResolvedValue(baseRedes({
      redes: [
        { ssid: 'CASA-WIFI', senal: 90, segura: true, activa: true, guardada: false },
        { ssid: 'VECINO-ABIERTA', senal: 40, segura: false, activa: false },
      ],
    }))
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-CASA-WIFI'))

    expect(await screen.findByTestId('kiosk-red-password')).toBeInTheDocument()
    expect(redConectar).not.toHaveBeenCalled()
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

  it('en modo remoto (movil) el campo de contraseña es escribible y no hay teclado en pantalla', async () => {
    remoto = true
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-CASA-WIFI'))
    const campo = await screen.findByTestId('kiosk-red-password')

    expect(screen.queryByTestId('kiosk-tecla-h')).not.toBeInTheDocument()
    await user.type(campo, 'hola')
    expect(campo).toHaveValue('hola')
  })

  it('el boton para conectar desde el movil no sale si ya se ve desde el movil', async () => {
    remoto = true
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')
    expect(screen.queryByTestId('kiosk-red-ap-abrir')).not.toBeInTheDocument()
  })

  it('levanta el AP y pinta el QR de wifi, avanza al QR de url y termina apagandolo', async () => {
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-ap-abrir'))
    await waitFor(() => expect(redApActivar).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('kiosk-red-ap-qr-wifi')).toBeInTheDocument()

    await user.click(screen.getByTestId('kiosk-red-ap-siguiente'))
    expect(await screen.findByTestId('kiosk-red-ap-qr-url')).toBeInTheDocument()

    await user.click(screen.getByTestId('kiosk-red-ap-cerrar'))
    await waitFor(() => expect(redApDesactivar).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('CASA-WIFI')).toBeInTheDocument()
  })

  it('pinta el error si no se pudo levantar el AP', async () => {
    redApActivar.mockResolvedValue({ ok: false, error: 'nmcli no disponible' })
    const user = userEvent.setup()
    render(<KioskRed tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('CASA-WIFI')

    await user.click(screen.getByTestId('kiosk-red-ap-abrir'))
    expect(await screen.findByTestId('kiosk-red-ap-error')).toHaveTextContent('nmcli no disponible')
  })
})
