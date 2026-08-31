/**
 * Camino de «elegir la carpeta del vuelo» en la pestaña SUBIR AL BUCKET.
 *
 * Existe por un bug concreto: `pickCarpeta` llamaba a `setForce(false)`, un
 * estado que un refactor había quitado, y el ReferenceError cortaba la función
 * ANTES de pedir el plan. La pantalla se quedaba sin reconciliar y nada fallaba
 * a la vista: el único rastro era una línea en la consola del webview.
 *
 * Por eso el test afirma sobre lo que el operador espera que pase (elijo
 * carpeta → la app comprueba qué hay que subir) y no sobre el estado interno:
 * cualquier excepción por el camino, se llame como se llame, lo pone rojo.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(async () => ({
    configured: true,
    logged_in: true,
    email: 'operador@aerotools.es',
    bucket: 'datos-para-organizar',
  })),
  cloudVerify: vi.fn(async () => ({ ok: true })),
  cloudInspecciones: vi.fn(async () => ({
    ok: true,
    origen: 'api',
    inspecciones: [{ etiqueta: 'ANTOLIN', prefijo: 'ANTOLIN' }],
  })),
  pickFolder: vi.fn(async () => '/home/saez/Descargas/ANTOLIN'),
  folderIsEmpty: vi.fn(async () => ({ empty: true })),
  cloudPrepareStart: vi.fn(async () => ({ started: true })),
  analisisReset: vi.fn(async () => ({ ok: true })),
  analisisCancel: vi.fn(async () => ({ ok: true })),
  estadilloExistente: vi.fn(async () => ({ existe: false })),
  cloudUpload: vi.fn(async () => ({ ok: true })),
  cloudCancel: vi.fn(async () => ({ ok: true })),
  cloudLogin: vi.fn(async () => ({ started: true })),
  cloudLogout: vi.fn(async () => ({ ok: true })),
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

// Navegación: Task 11 fundió «Organizar»/«SUBIR AL BUCKET» en el tab único
// «Trabajo» (activo por defecto). El camino para llegar cambió; lo que
// comprueba el test (elegir carpeta pide el plan de subida) sigue siendo lo
// mismo — solo que ahora el plan se pide en hilo (`cloudPrepareStart`, Task
// 4), no de forma síncrona (`cloudPrepare`).
describe('SUBIR AL BUCKET · elegir la carpeta del vuelo', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('pide el plan de subida a la carpeta elegida', async () => {
    const user = userEvent.setup()
    render(<App />)

    // La app arranca en «Inicio»: hay que entrar a «Trabajo» antes de que
    // aparezca «Carpeta del vuelo», su primer paso.
    await user.click(await screen.findByRole('tab', { name: 'Trabajo' }))

    // «Carpeta del vuelo» es el primer paso de Trabajo: el botón «Elegir…»
    // de ahí es el primero de la pantalla.
    const elegirBotones = await screen.findAllByRole('button', { name: /elegir/i })
    await user.click(elegirBotones[0])

    await waitFor(() => expect(api.pickFolder).toHaveBeenCalled())

    // Sin inspección elegida no hay prefijo destino y `preparar` sale de vacío:
    // el plan solo tiene sentido cuando se sabe a dónde van los datos.
    await user.click(await screen.findByRole('button', { name: 'ANTOLIN' }))

    // Destino «Subir al bucket»: monta PanelSubida, que pide el plan en hilo.
    await user.click(await screen.findByText('Subir al bucket'))

    // Lo que el bug rompía: la carpeta se seleccionaba pero el plan no se pedía
    // nunca, así que «2.518 ya subidos» no llegaba a calcularse.
    await waitFor(() =>
      expect(api.cloudPrepareStart).toHaveBeenCalledWith('/home/saez/Descargas/ANTOLIN', 'ANTOLIN')
    )
  })
})
