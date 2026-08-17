/**
 * Estadillo (ubicación canónica del bucket), dentro de SUBIR AL BUCKET.
 *
 * `estadilloValidar` es el preview obligatorio: se llama al pulsar
 * «Comprobar» y su resultado decide si «Subir estadillo» se puede pulsar.
 * Estos tests afirman sobre lo que ve el operador (resumen de vuelos
 * detectados / error) y sobre que un check `ok: false` bloquea la subida sin
 * llegar siquiera a llamar a `estadilloSubir`.
 *
 * Mismo patrón que `App.test.jsx`: mock de `./bridge`, render de `<App>` y
 * navegación a la pestaña SUBIR AL BUCKET.
 */
import { render, screen } from '@testing-library/react'
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
  pickFile: vi.fn(async () => '/home/saez/Descargas/estadillo.xlsx'),
  cloudPrepare: vi.fn(async () => ({
    ok: true,
    prefix: 'ANTOLIN',
    files: 0,
    bytes: 0,
    existing: 0,
  })),
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
}

vi.mock('../bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onUpdate: () => () => {},
}))

const App = (await import('../App')).default

// Navega hasta la pantalla SUBIR AL BUCKET, elige la inspección (para que
// `prefijo` quede NO vacío, igual que en App.test.jsx) y elige un fichero de
// estadillo, dejando el botón «Comprobar» listo para pulsar.
//
// Elegir la inspección es imprescindible para que «Subir estadillo» pueda
// depender de verdad de `estadCheck?.ok` en vez de quedar deshabilitado por
// `!prefijo` (App.jsx:1015): sin este paso cualquier test sobre ese botón
// estaría verde en falso.
async function irAEstadilloConFichero(user) {
  render(<App />)

  await user.click(await screen.findByRole('button', { name: /SUBIR AL BUCKET/i }))

  await user.click(await screen.findByRole('button', { name: 'ANTOLIN' }))

  // El botón «Elegir…» del campo Estadillo es el primero de la pantalla (el
  // campo se renderiza antes que «Carpeta a subir», que también tiene uno).
  const elegirBotones = await screen.findAllByRole('button', { name: /elegir/i })
  await user.click(elegirBotones[0])
}

describe('Estadillo (ubicación canónica del bucket)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.pickFile.mockResolvedValue('/home/saez/Descargas/estadillo.xlsx')
  })

  it('muestra los vuelos detectados tras validar', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 34,
      filas_con_problemas: 0,
    })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)

    await user.click(await screen.findByRole('button', { name: 'Comprobar' }))

    expect(await screen.findByText(/34/)).toBeInTheDocument()
  })

  it('muestra el error y no permite subir si no carga', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: false,
      error: 'Falta la columna PB',
      vuelos_detectados: 0,
      filas_con_problemas: 0,
    })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)

    await user.click(await screen.findByRole('button', { name: 'Comprobar' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Falta la columna PB')
    expect(screen.getByRole('button', { name: /Subir estadillo/i })).toBeDisabled()
    expect(api.estadilloSubir).not.toHaveBeenCalled()
  })

  it('habilita subir estadillo con inspección elegida y check ok', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 12,
      filas_con_problemas: 0,
    })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)

    await user.click(await screen.findByRole('button', { name: 'Comprobar' }))

    await screen.findByText(/12/)
    expect(screen.getByRole('button', { name: /Subir estadillo/i })).not.toBeDisabled()
  })
})
