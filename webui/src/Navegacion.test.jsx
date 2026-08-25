import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SECTIONS } from './schema'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(async () => ({ configured: true, logged_in: true, email: 'a@b.c' })),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
  readConfig: vi.fn(async () => ({ ruta_thermoviewer: '', percentage_by_models: {} })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
}

vi.mock('./bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onAnalisis: () => () => {},
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => false,
}))

const App = (await import('./App')).default

describe('Navegación', () => {
  it('tiene exactamente tres pestañas', async () => {
    render(<App />)
    const tabs = await screen.findAllByRole('tab')
    expect(tabs).toHaveLength(3)
  })

  it('arranca en Trabajo', async () => {
    render(<App />)
    expect(await screen.findByText(/Carpeta del vuelo/i)).toBeTruthy()
  })

  it('Herramientas agrupa las dos pantallas de herramientas', async () => {
    render(<App />)
    fireEvent.click(await screen.findByText('Herramientas'))
    expect(await screen.findByText(SECTIONS.aerotools.label)).toBeTruthy()
    expect(screen.getByText(SECTIONS.otros.label)).toBeTruthy()
  })
})
