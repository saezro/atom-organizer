import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.70' })),
  cloudStatus: vi.fn(async () => ({ configured: true, logged_in: true, email: 'a@b.c', bucket: 'datos-para-organizar' })),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
  readConfig: vi.fn(async () => ({ ruta_thermoviewer: '', percentage_by_models: {} })),
  renderEstado: vi.fn(async () => ({ modo: 'auto', activa: true })),
  estadoUpdate: vi.fn(async () => ({ ok: true, update_available: false, current: '3.4.70', latest: '3.4.70' })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false, current: '3.4.70', latest: '3.4.70' })),
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

// Cubre el bloque «Actualizaciones» de Ajustes (ConfigScreen). Antes, un fallo
// de `checkUpdate`/`estadoUpdate` se tragaba en silencio y el texto se
// quedaba colgado o mentía diciendo que estaba al día.
describe('Ajustes · Actualizaciones', () => {
  async function irAAjustes() {
    render(<App />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Ajustes' }))
    await screen.findByText('Actualizaciones')
  }

  it('el botón «Buscar actualizaciones» existe y llama a checkUpdate al pulsarlo', async () => {
    await irAAjustes()
    const boton = await screen.findByRole('button', { name: 'Buscar actualizaciones ahora' })
    expect(boton).toBeTruthy()
    fireEvent.click(boton)
    await waitFor(() => expect(api.checkUpdate).toHaveBeenCalled())
  })

  it('muestra que está al día cuando no hay actualización pendiente', async () => {
    api.estadoUpdate.mockResolvedValueOnce({ ok: true, update_available: false, current: '3.4.70', latest: '3.4.70' })
    await irAAjustes()
    expect(await screen.findByText('Estás en la última versión (3.4.70).')).toBeTruthy()
  })

  it('muestra la versión nueva disponible', async () => {
    api.estadoUpdate.mockResolvedValueOnce({ ok: true, update_available: true, current: '3.4.70', latest: '3.4.71' })
    await irAAjustes()
    expect(await screen.findByText('Hay una versión nueva: 3.4.71 (tienes la 3.4.70).')).toBeTruthy()
  })

  it('muestra el error en vez de tragárselo en silencio', async () => {
    api.estadoUpdate.mockResolvedValueOnce({ ok: false, error: 'sin conexión' })
    await irAAjustes()
    expect(await screen.findByText('No se pudo comprobar: sin conexión')).toBeTruthy()
  })
})
