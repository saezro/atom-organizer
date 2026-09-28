/**
 * Marco azul de "control remoto activo" (Task «control remoto del kiosco»):
 * se enciende PLENO con CUALQUIER accion remota (`atom:control_ui` o
 * `atom:control_carpeta`). A los 15s sin eventos nuevos se atenua (clase
 * `.kiosk-control-marco-desvanecido`, opacidad por CSS) hasta un estado
 * TENUE que se QUEDA asi — ya NO se desmonta solo. Un evento nuevo lo vuelve
 * a poner pleno. Un toque o tecla LOCAL es la unica forma de quitarlo, al
 * instante, sin esperar la atenuacion.
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
  listDir: vi.fn(async () => ({ ok: true, path: '/', parent: null, dirs: [], files: [] })),
}

let handlerControlCarpeta = null
let handlerControlUi = null

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
  onControlUi: (handler) => {
    handlerControlUi = handler
    return () => { handlerControlUi = null }
  },
  registerPicker: vi.fn(),
  isServerMode: () => true,
}))

const App = (await import('./App')).default

describe('App: marco de "control remoto activo"', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    handlerControlCarpeta = null
    handlerControlUi = null
  })

  it('se enciende con atom:control_ui, se atenua a los 15s y se queda tenue (no se desmonta)', async () => {
    const user = userEvent.setup()
    render(<App />)

    // Navegacion con timers reales: `findByText`/`waitFor` de testing-library
    // pollean con `setTimeout` real, no se llevan bien con timers falsos.
    await user.click(await screen.findByText('Organizer'))
    await user.click(await screen.findByText('Organizar'))
    await waitFor(() => expect(handlerControlUi).toBeTruthy())
    expect(document.querySelector('.kiosk-control-marco')).toBeNull()

    vi.useFakeTimers()
    try {
      act(() => { handlerControlUi({ accion: 'cancelar' }) })
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeNull()

      act(() => { vi.advanceTimersByTime(14000) })
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeNull()

      act(() => { vi.advanceTimersByTime(1500) }) // 15500: ya atenuandose
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeTruthy()

      // Ya no se desmonta solo: se queda tenue por mucho que pase el tiempo.
      act(() => { vi.advanceTimersByTime(60000) })
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('un nuevo evento tras quedar tenue lo vuelve a poner pleno (atom:control_carpeta cuenta igual)', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByText('Organizer'))
    await user.click(await screen.findByText('Organizar'))
    await waitFor(() => expect(handlerControlCarpeta).toBeTruthy())
    await waitFor(() => expect(handlerControlUi).toBeTruthy())

    vi.useFakeTimers()
    try {
      act(() => { handlerControlCarpeta({ path: '/media/disco/VUELO' }) })
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()

      act(() => { vi.advanceTimersByTime(15500) }) // tenue
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeTruthy()

      act(() => { handlerControlUi({ accion: 'cancelar' }) }) // nuevo evento: pleno otra vez
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeNull()

      act(() => { vi.advanceTimersByTime(14000) })
      expect(document.querySelector('.kiosk-control-marco-desvanecido')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('se enciende con atom:control_ui accion "api_remota" (cualquier /api/* no-loopback, no solo /api/control/*)', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByText('Organizer'))
    await user.click(await screen.findByText('Organizar'))
    await waitFor(() => expect(handlerControlUi).toBeTruthy())
    expect(document.querySelector('.kiosk-control-marco')).toBeNull()

    act(() => { handlerControlUi({ accion: 'api_remota' }) })
    expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
  })

  it('un toque local apaga el marco al instante, sin esperar el desvanecido', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByText('Organizer'))
    await user.click(await screen.findByText('Organizar'))
    await waitFor(() => expect(handlerControlUi).toBeTruthy())

    vi.useFakeTimers()
    try {
      act(() => { handlerControlUi({ accion: 'cancelar' }) })
      expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()

      act(() => { window.dispatchEvent(new PointerEvent('pointerdown')) })
      expect(document.querySelector('.kiosk-control-marco')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  // Ultimo test del fichero a proposito: sobreescribe `pinEstado` con
  // `mockResolvedValue` (sobrevive a `vi.clearAllMocks()` del `beforeEach`,
  // que solo limpia llamadas, no implementaciones) para forzar la pantalla
  // bloqueada por PIN.
  it('el marco tambien se ve durante un login remoto, encima del bloqueo de PIN', async () => {
    // Con PIN fijado, la pantalla bloqueada (`KioskLock`) es lo unico que
    // pinta `KioskGuard`: el marco lo pinta `App` por su cuenta, FUERA de
    // ese arbol, asi que debe verse igual (Rodrigo: "no salio el marco").
    api.pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    render(<App />)

    await waitFor(() => expect(document.querySelector('[data-testid="kiosk-pin"]')).toBeTruthy())
    await waitFor(() => expect(handlerControlUi).toBeTruthy())

    act(() => { handlerControlUi({ accion: 'login' }) })
    expect(document.querySelector('.kiosk-control-marco')).toBeTruthy()
    // Sigue bloqueada (la animacion de `KioskLock` tarda ~1.25s): el marco
    // no espera a que desbloquee para pintarse.
    expect(document.querySelector('[data-testid="kiosk-pin"]')).toBeTruthy()
  })
})
