/**
 * Carpeta fijada en remoto (`/api/control/carpeta`, portátil): el backend la
 * refleja con el evento `atom:control_carpeta` y el kiosco debe tomarla como
 * si se hubiera elegido en pantalla (Task «control remoto del kiosco»),
 * SIN volver a llamar a `carpeta_trabajo_fijar` (el backend ya la tiene, es
 * quien avisó) y re-detectando el estadillo de la nueva carpeta.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(async () => ({ configured: true, logged_in: false, estado: 'sin-credencial' })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  pinEstado: vi.fn(async () => ({ ok: true, hay_pin: false })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
  estadillosDetectar: vi.fn(async () => ({ rutas: [] })),
  estadilloEsperaEstado: vi.fn(async () => ({ recibido: false })),
  analisisReset: vi.fn(async () => ({ ok: true })),
  detectSuffixesStart: vi.fn(async () => ({ started: true })),
  carpetaTrabajoFijar: vi.fn(async () => ({ ok: true })),
  pickFolder: vi.fn(async () => null),
}

let handlerControlCarpeta = null

vi.mock('./bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onAnalisis: () => () => {},
  onUpdate: () => () => {},
  onControlCarpeta: (handler) => {
    handlerControlCarpeta = handler
    return () => { handlerControlCarpeta = null }
  },
  onControlUi: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => true,
}))

const App = (await import('./App')).default

describe('App: refleja la carpeta fijada en remoto (atom:control_carpeta)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    handlerControlCarpeta = null
  })

  it('actualiza la carpeta del kiosco y re-detecta el estadillo, sin re-fijarla en el backend', async () => {
    const user = userEvent.setup()
    render(<App />)

    // Kiosco arranca en el launcher de apps: «Organizer» → «Organizar», dos
    // pasos, para llegar a la tarjeta de carpeta.
    await user.click(await screen.findByText('Organizer'))
    await user.click(await screen.findByText('Organizar'))

    await waitFor(() => expect(handlerControlCarpeta).toBeTruthy())

    // El portátil fija la carpeta por la vía remota: el evento SSE llega ya
    // resuelto, sin que el kiosco haya llamado a nada.
    act(() => { handlerControlCarpeta({ path: '/media/disco/VUELO_REMOTO' }) })

    // Se pinta como si se hubiera elegido a mano (la tarjeta de «Organizar»
    // muestra el destino derivado, con sufijo `_ORGANIZADO`)...
    expect(await screen.findByText('/media/disco/VUELO_REMOTO_ORGANIZADO')).toBeTruthy()
    // ...y re-detecta el estadillo de esa carpeta.
    await waitFor(() => expect(api.estadillosDetectar).toHaveBeenCalledWith('/media/disco/VUELO_REMOTO'))

    // Sin volver a llamar al backend para fijarla: ya la tiene él.
    expect(api.carpetaTrabajoFijar).not.toHaveBeenCalled()
  })
})
