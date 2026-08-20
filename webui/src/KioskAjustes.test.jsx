import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
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

// Con `tactil` los botones (BotonToque/BotonMantener) se activan al SOLTAR el
// puntero, no con el `click` sintetizado (que solo hace preventDefault): hace
// falta simular el gesto completo, igual que BotonMantener.test.jsx.
function tocar(el) {
  fireEvent.pointerDown(el)
  fireEvent.pointerUp(el)
}

// Deja correr las promesas pendientes (readConfig del bridge mockeado) bajo
// fake timers: mismo patrón que PairScreen.test.jsx (`await` a secas no basta,
// hace falta ceder el microtask queue de verdad).
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
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

  it('carga y pinta la ruta de ThermoViewer en el índice de General, y los modelos en su lista', async () => {
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)

    expect(await screen.findByText('C:\\Program Files\\ThermoViewer\\ThermoViewer.exe')).toBeInTheDocument()
    expect(screen.getByText('1 modelo')).toBeInTheDocument()

    await user.click(screen.getByTestId('kiosk-ajustes-abrir-modelos'))
    expect(await screen.findByText('M4T')).toBeInTheDocument()
    expect(screen.getByText('15%')).toBeInTheDocument()
  })

  it('añade un modelo con el teclado en pantalla y el selector de %, y guarda con el resto', async () => {
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    await user.click(screen.getByTestId('kiosk-ajustes-abrir-modelos'))
    await screen.findByText('M4T')

    // Paso 1: nombre con el teclado en pantalla (con cambio a modo números
    // para el "3", como en un teclado real de la Pi).
    await user.click(screen.getByTestId('kiosk-ajustes-nuevo'))
    await user.click(screen.getByTestId('kiosk-tecla-m'))
    await user.click(screen.getByTestId('kiosk-tecla-modo'))
    await user.click(screen.getByTestId('kiosk-tecla-3'))
    await user.click(screen.getByTestId('kiosk-tecla-modo'))
    await user.click(screen.getByTestId('kiosk-tecla-t'))
    expect(screen.getByTestId('kiosk-ajustes-nombre')).toHaveValue('M3T')

    await user.click(screen.getByTestId('kiosk-ajustes-siguiente'))

    // Paso 2: % a pasos de 5, desde el valor por defecto (20%).
    expect(screen.getByTestId('kiosk-ajustes-pct-valor')).toHaveTextContent('20%')
    await user.click(screen.getByTestId('kiosk-ajustes-pct-mas'))
    await user.click(screen.getByTestId('kiosk-ajustes-pct-mas'))
    expect(screen.getByTestId('kiosk-ajustes-pct-valor')).toHaveTextContent('30%')

    await user.click(screen.getByTestId('kiosk-ajustes-anadir'))
    expect(await screen.findByText('M3T')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()

    await user.click(screen.getByTestId('kiosk-ajustes-guardar'))

    await waitFor(() => expect(writeConfig).toHaveBeenCalledTimes(1))
    expect(writeConfig).toHaveBeenCalledWith({
      ruta_thermoviewer: 'C:\\Program Files\\ThermoViewer\\ThermoViewer.exe',
      percentage_by_models: { M4T: 15, M3T: 30 },
    })
    expect(await screen.findByTestId('kiosk-ajustes-ok')).toBeInTheDocument()
  })

  it('quita un modelo existente MANTENIENDO PULSADO (BotonMantener, no con un click)', async () => {
    vi.useFakeTimers()
    try {
      render(<KioskAjustes tactil onVolver={vi.fn()} />)

      tocar(screen.getByTestId('kiosk-ajustes-seccion-general'))
      await flush()
      tocar(screen.getByTestId('kiosk-ajustes-abrir-modelos'))
      await flush()
      expect(screen.getByText('M4T')).toBeInTheDocument()

      const boton = screen.getByTestId('kiosk-ajustes-quitar-M4T')

      // Soltar antes de completar el segundo NO lo quita.
      fireEvent.pointerDown(boton)
      act(() => vi.advanceTimersByTime(600))
      fireEvent.pointerUp(boton)
      act(() => vi.advanceTimersByTime(1000))
      expect(screen.getByText('M4T')).toBeInTheDocument()

      // Mantener el segundo completo sí lo quita.
      fireEvent.pointerDown(boton)
      act(() => vi.advanceTimersByTime(1000))

      expect(screen.queryByText('M4T')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('la lista de modelos muestra el texto de vacío cuando no hay ninguno configurado', async () => {
    readConfig.mockResolvedValue(baseConfig({ percentage_by_models: {} }))
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    await screen.findByText('0 modelos')

    await user.click(screen.getByTestId('kiosk-ajustes-abrir-modelos'))
    expect(await screen.findByText('No hay modelos configurados todavía.')).toBeInTheDocument()
  })

  it('pinta el error si writeConfig falla', async () => {
    writeConfig.mockRejectedValue(new Error('boom'))
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={vi.fn()} />)
    await irAGeneral(user)
    await screen.findByText('ThermoViewer')

    await user.click(screen.getByTestId('kiosk-ajustes-guardar'))

    expect(await screen.findByTestId('kiosk-ajustes-error')).toHaveTextContent('Error: boom')
  })

  it('"← Atrás" dentro de General vuelve al índice, no al launcher', async () => {
    const onVolver = vi.fn()
    const user = userEvent.setup()
    render(<KioskAjustes tactil={false} onVolver={onVolver} />)
    await irAGeneral(user)
    await screen.findByText('ThermoViewer')

    await user.click(screen.getByRole('button', { name: /atrás/i }))

    expect(screen.getByTestId('kiosk-ajustes-seccion-general')).toBeInTheDocument()
    expect(onVolver).not.toHaveBeenCalled()
  })
})
