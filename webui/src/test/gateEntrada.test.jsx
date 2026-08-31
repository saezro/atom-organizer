import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.70' })),
  // Sin cuenta vinculada: es lo que hace que salte la pantalla de entrada.
  cloudStatus: vi.fn(async () => ({ configured: true, logged_in: false })),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
  readConfig: vi.fn(async () => ({ ruta_thermoviewer: '', percentage_by_models: {} })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
  renderConfirmar: vi.fn(async () => ({ ok: true })),
}

vi.mock('../bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onAnalisis: () => () => {},
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => false,
}))

const App = (await import('../App')).default

describe('Puerta de entrada', () => {
  beforeEach(() => {
    localStorage.clear()
    api.renderConfirmar.mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('sin sesión pinta la pantalla de entrada y no la de trabajo', async () => {
    render(<App />)
    expect(await screen.findByTestId('pantalla-entrada')).toBeTruthy()
    expect(screen.queryByRole('tab', { name: 'Trabajo' })).toBeNull()
  })

  it('«Entrar sin cuenta» abre Inicio con el avatar de cuenta', async () => {
    render(<App />)
    fireEvent.click(await screen.findByText('Entrar sin cuenta'))
    expect(await screen.findByText('Subir en crudo')).toBeTruthy()
    expect(screen.getByTestId('cuenta-avatar')).toBeTruthy()
  })

  // Regresión de v3.4.69: si `render_confirmar` no llega, el arranque
  // siguiente asume pantalla negra y degrada a rasterizado software (la app
  // va lenta). La pantalla de entrada NO puede saltarse esa confirmación.
  it('confirma el render también desde la pantalla de entrada', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    render(<App />)
    await vi.advanceTimersByTimeAsync(3500)
    expect(api.renderConfirmar).toHaveBeenCalled()
  })
})
