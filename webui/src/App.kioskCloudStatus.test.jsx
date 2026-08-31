/**
 * R2 (Task 11): bug preexistente — en modo servidor, el operario hace logout
 * en el panel de subida (`PanelSubida`, dentro del tab «Trabajo») y vuelve al
 * kiosco sin que `App` se remonte; `kioskCloudStatus` se quedaba con
 * `estado: 'ok'` de antes, y «Subir en crudo» seguía pulsable sin sesión.
 *
 * La cadena de props ya estaba tendida por Task 9 (`PanelSubida.onCloudStatusChange`)
 * y Task 10 (`TrabajoScreen` la reenvía tal cual). El eslabón que faltaba era
 * `App.jsx`: pasarle ese handler a `<TrabajoScreen>` para que actualice el
 * mismo `kioskCloudStatus` que lee el kiosco.
 *
 * El propio kiosco no tiene forma de volver a la UI completa (es un callejón
 * sin salida táctil, ver comentario en `App.jsx`), así que aquí se fuerza el
 * camino inverso al real: arranca en la UI completa (`isServerMode` mockeado
 * a `false` en el montaje) y, tras hacer logout desde `PanelSubida`, el mock
 * de `isServerMode` se pone a `true` para poder pulsar «Volver al kiosco» y
 * comprobar que el kiosco arranca YA con el estado post-logout, sin
 * remontar `App`.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  cloudLogout: vi.fn(async () => ({ ok: true })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
  pickFolder: vi.fn(async () => '/home/saez/Descargas/ANTOLIN'),
  readConfig: vi.fn(async () => ({ ruta_thermoviewer: '', percentage_by_models: {} })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
  pinEstado: vi.fn(async () => ({ ok: true, hay_pin: false })),
}

// `isServerMode` se pilota desde el propio test (ver docstring): empieza en
// `false` para poder llegar a «Trabajo» → «Subir al bucket» (PanelSubida) sin
// pasar por el kiosco, y se pone a `true` justo antes de pulsar «Volver al
// kiosco», el único camino que expone `App.jsx` hacia el kiosco.
const isServerMode = vi.fn(() => false)

vi.mock('./bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onAnalisis: () => () => {},
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => isServerMode(),
}))

const App = (await import('./App')).default

describe('App: sincroniza kioskCloudStatus tras logout en PanelSubida (R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isServerMode.mockReturnValue(false)
    // Por estado, no por orden de llamada: `cloudStatus` puede pedirse más de
    // una vez antes del logout (p.ej. si el efecto de `PanelSubida` se
    // reinvoca), así que secuenciar con `mockResolvedValueOnce` sería frágil.
    let sesionActiva = true
    api.cloudLogout.mockImplementation(async () => {
      sesionActiva = false
      return { ok: true }
    })
    api.cloudStatus.mockImplementation(async () =>
      sesionActiva
        ? { configured: true, logged_in: true, email: 'operador@aerotools.es' }
        : { configured: true, logged_in: false, estado: 'sin-credencial' }
    )
  })

  it('pasa un onCloudStatusChange real (no undefined) a TrabajoScreen y actualiza el estado del kiosco', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<App />)

    // La app arranca en «Inicio»: hay que entrar a «Trabajo» antes de que
    // aparezcan «Carpeta del vuelo» y los destinos.
    await user.click(await screen.findByRole('tab', { name: 'Trabajo' }))

    // Carpeta del vuelo → destino «Subir al bucket» → PanelSubida montado.
    const elegirBotones = await screen.findAllByRole('button', { name: /elegir/i })
    await user.click(elegirBotones[0])
    await user.click(await screen.findByText('Subir al bucket'))

    // Sesión iniciada: PanelSubida ya llamó a onCloudStatusChange una vez al
    // montarse (con logged_in: true) — confirma que el handler SÍ es una
    // función real (si `App` pasara `undefined`, `onCloudStatusChange?.(s)`
    // no rompería, así que la única forma de confirmar que hace algo es
    // observar su efecto, más abajo).
    await waitFor(() => expect(api.cloudStatus).toHaveBeenCalled())
    // La puerta de entrada (`useSesion`) también llama a `cloudStatus` al
    // montar `App`, además de `PanelSubida`: el número absoluto de llamadas
    // hasta aquí no es relevante para lo que prueba este test, solo que el
    // logout dispare EXACTAMENTE una llamada más (el `refresh()` de
    // `PanelSubida`).
    const llamadasAntesDeLogout = api.cloudStatus.mock.calls.length

    // Logout: dispara `refresh()` → `api.cloudStatus()` (ahora sin sesión) →
    // `onCloudStatusChange(s)`. Si `App.jsx` no lo cableara a
    // `setKioskCloudStatus`, el kiosco (más abajo) seguiría leyendo el
    // `kioskCloudStatus` inicial (`null`, sin estado).
    await user.click(await screen.findByText(/Cerrar sesión/i))
    await waitFor(() => expect(api.cloudLogout).toHaveBeenCalled())
    await waitFor(() =>
      expect(api.cloudStatus).toHaveBeenCalledTimes(llamadasAntesDeLogout + 1)
    )

    // Único camino de vuelta al kiosco: sin remontar `App`, sin volver a
    // pedir `cloudStatus` a mano. `isServerMode()` se lee en cada render de
    // `App` (no está memoizada): tras cambiar el mock hace falta forzar un
    // render para que el botón «Volver al kiosco» aparezca — SIN remontar
    // `App` (eso perdería `kioskCloudStatus`, justo lo que este test evita).
    isServerMode.mockReturnValue(true)
    rerender(<App />)
    await user.click(await screen.findByText(/Volver al kiosco/i))

    // El kiosco arranca YA con el estado post-logout: si `kioskCloudStatus`
    // se hubiera quedado stale en `{logged_in: true}` (sin `estado`),
    // `AvisoSesion` no pintaría nada (`TEXTOS[undefined]` es `undefined`).
    expect(await screen.findByText('SESIÓN CERRADA')).toBeTruthy()
  })
})
