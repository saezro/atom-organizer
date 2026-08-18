/**
 * Estadillo (ubicación canónica del bucket), dentro de SUBIR AL BUCKET.
 *
 * Flujo NUEVO (commit 8514804): el estadillo es obligatorio y ya no tiene
 * botones propios «Comprobar» / «Subir estadillo». `estadilloValidar` se
 * llama SOLO al elegir los ficheros (efecto reactivo a `estadRutas`), y su
 * resultado (`estadCheck`) decide si el ÚNICO botón «Subir al bucket» puede
 * pulsarse. Al pulsarlo, `subir()` encadena primero `estadilloSubir` (vía
 * `subirEstadilloEsperando`) y solo si eso resuelve sube las imágenes; un
 * `estadCheck.ok === false` bloquea el botón antes de llegar a llamar a
 * `estadilloSubir`.
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
  // Detecta si la inspección elegida ya tiene estadillo subido (auto-marca
  // «omitir estadillo»). Se deja en `existe: false` por defecto para que
  // estos tests, centrados en la validación del estadillo NUEVO, no se
  // encuentren el checkbox de exención ya marcado por sorpresa.
  estadilloExistente: vi.fn(async () => ({ existe: false })),
}

vi.mock('../bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
}))

const App = (await import('../App')).default

// Navega hasta la pantalla SUBIR AL BUCKET, elige la inspección (para que
// `prefijo` quede NO vacío, igual que en App.test.jsx) y elige un fichero de
// estadillo. Elegir el fichero YA dispara la validación automática (no hay
// botón «Comprobar» que pulsar).
//
// Elegir la inspección es imprescindible para que el botón «Subir al bucket»
// pueda depender de verdad de `estadCheck?.ok` en vez de quedar deshabilitado
// por `!prefijo` (App.jsx): sin este paso cualquier test sobre ese botón
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

// Además del estadillo, «Subir al bucket» exige un plan de carpeta válido
// (`plan?.ok`, resultado de `cloudPrepare`). Se elige la carpeta para poder
// aislar el efecto de `estadCheck` sobre el botón: sin esto, el botón
// quedaría deshabilitado igualmente por falta de carpeta y el test no
// probaría nada sobre el estadillo en concreto.
async function elegirCarpeta(user) {
  const elegirBotones = await screen.findAllByRole('button', { name: /elegir/i })
  // El de «Carpeta a subir» es el segundo (el del Estadillo sigue siendo el
  // primero mientras haya 0 o 1 fichero elegido).
  await user.click(elegirBotones[1])
}

describe('Estadillo (ubicación canónica del bucket)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.pickFile.mockResolvedValue('/home/saez/Descargas/estadillo.xlsx')
    api.pickFolder.mockResolvedValue('/home/saez/Descargas/ANTOLIN')
    api.cloudPrepare.mockResolvedValue({
      ok: true,
      prefix: 'ANTOLIN',
      files: 0,
      bytes: 0,
      existing: 0,
    })
    api.estadilloExistente.mockResolvedValue({ existe: false })
  })

  // Antes: pulsaba «Comprobar» y esperaba ver el resumen. Ahora la
  // validación es automática al elegir el fichero: no hay nada que pulsar,
  // basta con esperar a que aparezca el resumen.
  it('muestra los vuelos detectados al elegir el estadillo', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 34,
      filas_con_problemas: 0,
    })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)

    expect(await screen.findByText(/34/)).toBeInTheDocument()
  })

  // Antes: pulsaba «Comprobar» y comprobaba que «Subir estadillo» quedaba
  // deshabilitado. Ahora no hay botón propio del estadillo: el check fallido
  // debe bloquear el ÚNICO botón «Subir al bucket» (con la carpeta ya lista,
  // para aislar que el bloqueo es por el estadillo y no por falta de plan).
  it('muestra el error y no permite subir si el estadillo no valida', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: false,
      error: 'Falta la columna PB',
      vuelos_detectados: 0,
      filas_con_problemas: 0,
    })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)
    await elegirCarpeta(user)

    expect(await screen.findByRole('alert')).toHaveTextContent('Falta la columna PB')
    expect(await screen.findByRole('button', { name: 'Subir al bucket' })).toBeDisabled()
    expect(api.estadilloSubir).not.toHaveBeenCalled()
  })

  // Antes: pulsaba «Comprobar» y comprobaba que «Subir estadillo» quedaba
  // habilitado. Ahora es el botón único «Subir al bucket» el que depende de
  // `estadCheck?.ok`, además de tener el plan de carpeta listo.
  it('habilita subir al bucket con inspección elegida, estadillo válido y carpeta preparada', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 12,
      filas_con_problemas: 0,
    })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)
    await elegirCarpeta(user)

    await screen.findByText(/12/)
    expect(await screen.findByRole('button', { name: 'Subir al bucket' })).not.toBeDisabled()
  })

  // Antes: pulsaba «Subir estadillo» y comprobaba que se deshabilitaba de
  // inmediato. Ahora ese mismo guardado de doble-click vive dentro de
  // `subir()`, que llama a `subirEstadilloEsperando` ANTES de tocar ninguna
  // imagen: se pulsa el único botón «Subir al bucket» y se comprueba que se
  // deshabilita sin esperar a que la subida del estadillo resuelva.
  it('deshabilita subir al bucket en cuanto se pulsa, sin esperar al evento start', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 12,
      filas_con_problemas: 0,
    })
    // `estadilloSubir` no resuelve todavía: simula la ventana entre el click
    // y el evento `start` (IPC + chequeo de sesión + validación + arranque
    // de hilo). Si el botón no se deshabilita ANTES de esta await, un
    // segundo click en esa ventana arrancaría un segundo hilo.
    let resolver
    api.estadilloSubir.mockReturnValue(
      new Promise((r) => {
        resolver = r
      }),
    )

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)
    await elegirCarpeta(user)
    await screen.findByText(/12/)

    const botonSubir = await screen.findByRole('button', { name: 'Subir al bucket' })
    expect(botonSubir).not.toBeDisabled()

    await user.click(botonSubir)

    expect(botonSubir).toBeDisabled()

    resolver({ ok: true })
  })

  // Antes: pulsaba «Subir estadillo» directamente y esperaba a que
  // `started: false` mostrara el motivo y reactivara ESE botón. Ahora
  // `started: false` en `estadilloSubir` hace que `subirEstadilloEsperando`
  // rechace dentro de `subir()`, así que es «Subir al bucket» el que se
  // reactiva; el motivo se sigue viendo en el mismo hint (`estadResult.error`,
  // fijado por `subirEstadillo` antes de rechazar).
  it('rehabilita subir al bucket si la subida del estadillo no arranca', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 5,
      filas_con_problemas: 0,
    })
    api.estadilloSubir.mockResolvedValue({ started: false, reason: 'Primero inicia sesión' })

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)
    await elegirCarpeta(user)
    await screen.findByText(/5/)

    await user.click(await screen.findByRole('button', { name: 'Subir al bucket' }))

    expect(await screen.findByText('Primero inicia sesión')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Subir al bucket' })).not.toBeDisabled()
  })
})
