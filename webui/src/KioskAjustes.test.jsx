import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const readConfig = vi.fn()
const writeConfig = vi.fn()
const pickFile = vi.fn()

vi.mock('./bridge.js', () => ({
  api: {
    readConfig: (...args) => readConfig(...args),
    writeConfig: (...args) => writeConfig(...args),
    pickFile: (...args) => pickFile(...args),
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

describe('KioskAjustes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readConfig.mockResolvedValue(baseConfig())
    writeConfig.mockResolvedValue({ ok: true })
  })

  it('carga y pinta la ruta de ThermoViewer existente', async () => {
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    expect(await screen.findByText('C:\\Program Files\\ThermoViewer\\ThermoViewer.exe')).toBeInTheDocument()
    expect(screen.getByText('M4T')).toBeInTheDocument()
    expect(screen.getByText('15%')).toBeInTheDocument()
  })

  it('añade un modelo y al guardar llama a writeConfig con el objeto correcto', async () => {
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
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
    await screen.findByText('M4T')

    await user.click(screen.getByTestId('kiosk-ajustes-quitar-M4T'))

    expect(screen.queryByText('M4T')).not.toBeInTheDocument()
  })

  it('pinta el error si writeConfig falla', async () => {
    writeConfig.mockRejectedValue(new Error('boom'))
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await screen.findByText('M4T')

    await user.click(screen.getByTestId('kiosk-ajustes-guardar'))

    expect(await screen.findByTestId('kiosk-ajustes-error')).toHaveTextContent('Error: boom')
  })
})
