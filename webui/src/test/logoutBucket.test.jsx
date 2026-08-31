import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(async () => ({
    configured: true,
    logged_in: true,
    email: 'a@b.c',
    bucket: 'datos-para-organizar',
  })),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({
    ok: true,
    origen: 'api',
    inspecciones: [{ etiqueta: 'ANTOLIN', prefijo: 'ANTOLIN' }],
  })),
  pickFolder: vi.fn(async () => '/home/saez/Descargas/ANTOLIN'),
  pickFile: vi.fn(async () => '/home/saez/Descargas/estadillo.xlsx'),
  cloudPrepareStart: vi.fn(async () => ({ started: true })),
  analisisReset: vi.fn(async () => ({ ok: true })),
  analisisCancel: vi.fn(async () => ({ ok: true })),
  cloudUpload: vi.fn(async () => ({ ok: true })),
  cloudCancel: vi.fn(async () => ({ ok: true })),
  cloudLogin: vi.fn(async () => ({ started: true })),
  cloudLogout: vi.fn(async () => ({ ok: true })),
  readConfig: vi.fn(async () => ({ ruta_thermoviewer: '', percentage_by_models: {} })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
  estadilloValidar: vi.fn(async () => ({
    ok: true,
    error: null,
    vuelos_detectados: 0,
    filas_con_problemas: 0,
  })),
  estadilloSubir: vi.fn(async () => ({ ok: true })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
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

// Navegación: Task 11 fundió «SUBIR AL BUCKET» en el destino homónimo dentro
// del tab «Trabajo» (activo por defecto). El comportamiento que prueba este
// test (cerrar sesión no revienta con un ReferenceError) no cambia, solo el
// camino: hace falta elegir la carpeta del vuelo antes de que aparezcan los
// destinos, y elegir «Subir al bucket» para montar `PanelSubida`.
describe('BucketScreen · cerrar sesión', () => {
  it('cierra sesión sin lanzar ReferenceError', async () => {
    const errores = []
    const onError = (e) => errores.push(e.error ?? e.reason)
    window.addEventListener('error', onError)
    window.addEventListener('unhandledrejection', onError)

    render(<App />)
    // La app arranca en «Inicio»: hay que entrar a «Trabajo» antes de que
    // aparezca «Carpeta del vuelo».
    fireEvent.click(await screen.findByRole('tab', { name: 'Trabajo' }))
    fireEvent.click((await screen.findAllByRole('button', { name: /elegir/i }))[0])
    fireEvent.click(await screen.findByText('Subir al bucket'))
    fireEvent.click(await screen.findByText(/Cerrar sesión/i))

    await waitFor(() => expect(errores).toHaveLength(0))
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onError)
  })
})
