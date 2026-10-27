import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
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
  // La pestaña «Subir resultado» (PanelResultado) está siempre montada en Trabajo.
  onResultado: () => () => {},
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onAnalisis: () => () => {},
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => false,
  onControlCarpeta: () => () => {},
  onControlUi: () => () => {},
}))

const App = (await import('./App')).default

describe('Navegación', () => {
  it('tiene exactamente tres pestañas', async () => {
    render(<App />)
    // Solo la navegación principal; Trabajo trae además sus pestañas internas (Organizar | Subir resultado).
    const nav = await screen.findByRole('tablist', { name: 'Secciones' })
    expect(within(nav).getAllByRole('tab')).toHaveLength(3)
  })

  it('arranca en Trabajo', async () => {
    render(<App />)
    expect(await screen.findByText('¿Qué hacemos con este trabajo?')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Trabajo' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: 'Inicio' })).toBeNull()
  })

  it('Avanzado agrupa las dos pantallas de herramientas', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Avanzado' }))
    expect(await screen.findByText(SECTIONS.aerotools.label)).toBeTruthy()
    expect(screen.getByText(SECTIONS.otros.label)).toBeTruthy()
  })
})
