// Entrar con un perfil guardado NO puede recargar la webview.
//
// Es el único camino de login que lo hacía (`window.location.reload()` en
// PantallaEntrada, por no tener `onPerfilActivado`), y por tanto el único que
// pierde el proceso JS entero y vuelve a montarlo todo. El resto de logins se
// quedan en sitio con `useSesion().refrescar()`.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

let logueado = false

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.76' })),
  cloudStatus: vi.fn(async () => (logueado
    ? { configured: true, logged_in: true, email: 'dani@aerotools.es', nombre: 'Dani', picture: '' }
    : { configured: true, logged_in: false })),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
  readConfig: vi.fn(async () => ({ ruta_thermoviewer: '', percentage_by_models: {} })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
  renderConfirmar: vi.fn(async () => ({ ok: true })),
  listarPerfiles: vi.fn(async () => ([
    { email: 'dani@aerotools.es', nombre: 'Dani', picture: '', modo: 'google' },
  ])),
  activarPerfil: vi.fn(async () => { logueado = true; return { ok: true } }),
  borrarPerfil: vi.fn(async () => ({ ok: true })),
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

describe('Entrada con perfil guardado', () => {
  beforeEach(() => {
    localStorage.clear()
    logueado = false
    api.activarPerfil.mockClear()
  })

  it('activa el perfil y entra sin recargar la webview', async () => {
    // jsdom no implementa `reload`: se sustituye por un espía para poder
    // afirmar que NO se llama (si se llamara, el test lo cazaría).
    const reload = vi.fn()
    const locationOriginal = window.location
    delete window.location
    window.location = { ...locationOriginal, reload }

    try {
      render(<App />)
      const tile = await screen.findByRole('button', { name: /Dani/i })
      fireEvent.click(tile)

      await waitFor(() => expect(api.activarPerfil).toHaveBeenCalledWith('dani@aerotools.es'))
      // La sesión se recoge en sitio: la pantalla de entrada desaparece...
      await waitFor(() => expect(screen.queryByTestId('pantalla-entrada')).toBeNull())
      // ...y sin haber remontado nada.
      expect(reload).not.toHaveBeenCalled()
    } finally {
      window.location = locationOriginal
    }
  })
})
