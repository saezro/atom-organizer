import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const readConfig = vi.fn()
const writeConfig = vi.fn()
const pickFile = vi.fn()
const redListar = vi.fn()
const redConectar = vi.fn()

vi.mock('./bridge.js', () => ({
  // KioskAjustes monta KioskRed dentro de su sección "Red", que desde el
  // AP+QR (KioskRed.jsx) necesita `esRemoto` del bridge.
  esRemoto: () => false,
  api: {
    readConfig: (...args) => readConfig(...args),
    writeConfig: (...args) => writeConfig(...args),
    pickFile: (...args) => pickFile(...args),
    redListar: (...args) => redListar(...args),
    redConectar: (...args) => redConectar(...args),
  },
}))

import KioskAjustes from './KioskAjustes.jsx'

function baseConfig(overrides = {}) {
  return {
    ruta_thermoviewer: 'C:\\Program Files\\ThermoViewer\\ThermoViewer.exe',
    percentage_by_models: { M4T: 15 },
    ...overrides,
  }
}

async function irAGeneral(user) {
  await user.click(screen.getByTestId('kiosk-ajustes-seccion-general'))
}

describe('KioskAjustes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readConfig.mockResolvedValue(baseConfig())
    writeConfig.mockResolvedValue({ ok: true })
    redListar.mockResolvedValue({ ok: true, actual: null, redes: [] })
  })

  it('muestra el índice de secciones (Red y General), sin campos de config', () => {
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    expect(screen.getByTestId('kiosk-ajustes-seccion-red')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-ajustes-seccion-general')).toBeInTheDocument()
    expect(screen.queryByText('ThermoViewer')).not.toBeInTheDocument()
  })

  it('pulsar "← Atrás" en el índice llama a onVolver (vuelve al launcher)', async () => {
    const onVolver = vi.fn()
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={onVolver} />)
    await user.click(screen.getByRole('button', { name: /atrás/i }))
    expect(onVolver).toHaveBeenCalled()
  })

  it('entra en la sección Red y vuelve al índice de Ajustes (no al launcher)', async () => {
    const onVolver = vi.fn()
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={onVolver} />)

    await user.click(screen.getByTestId('kiosk-ajustes-seccion-red'))
    expect(await screen.findByTestId('kiosk-red-vacio')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /atrás/i }))
    expect(screen.getByTestId('kiosk-ajustes-seccion-red')).toBeInTheDocument()
    expect(onVolver).not.toHaveBeenCalled()
  })

  it('carga y pinta la ruta de ThermoViewer existente en General', async () => {
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    expect(await screen.findByText('C:\\Program Files\\ThermoViewer\\ThermoViewer.exe')).toBeInTheDocument()
    expect(screen.getByText('M4T')).toBeInTheDocument()
    expect(screen.getByText('15%')).toBeInTheDocument()
  })

  it('añade un modelo y al guardar llama a writeConfig con el objeto correcto', async () => {
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    await screen.findByText('M4T')

    await user.type(screen.getByPlaceholderText(/modelo/i), 'm3t')
    await user.type(screen.getByPlaceholderText('%'), '25')
    await user.click(screen.getByTestId('kiosk-ajustes-anadir'))

    expect(await screen.findByText('M3T')).toBeInTheDocument()

    await user.click(screen.getByTestId('kiosk-ajustes-guardar'))

    await waitFor(() => expect(writeConfig).toHaveBeenCalledTimes(1))
    expect(writeConfig).toHaveBeenCalledWith({
      ruta_thermoviewer: 'C:\\Program Files\\ThermoViewer\\ThermoViewer.exe',
      percentage_by_models: { M4T: 15, M3T: 25 },
    })
    expect(await screen.findByTestId('kiosk-ajustes-ok')).toBeInTheDocument()
  })

  it('quita una fila', async () => {
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    await screen.findByText('M4T')

    await user.click(screen.getByTestId('kiosk-ajustes-quitar-M4T'))

    expect(screen.queryByText('M4T')).not.toBeInTheDocument()
  })

  it('pinta el error si writeConfig falla', async () => {
    writeConfig.mockRejectedValue(new Error('boom'))
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    await screen.findByText('M4T')

    await user.click(screen.getByTestId('kiosk-ajustes-guardar'))

    expect(await screen.findByTestId('kiosk-ajustes-error')).toHaveTextContent('Error: boom')
  })

  it('"← Atrás" dentro de General vuelve al índice, no al launcher', async () => {
    const onVolver = vi.fn()
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={onVolver} />)
    await irAGeneral(user)
    await screen.findByText('M4T')

    await user.click(screen.getByRole('button', { name: /atrás/i }))

    expect(screen.getByTestId('kiosk-ajustes-seccion-general')).toBeInTheDocument()
    expect(onVolver).not.toHaveBeenCalled()
  })
})
