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
 * navegación al tab «Trabajo» (Task 11) → destino «Subir al bucket» (monta
 * `PanelSubida`).
 */
import { render, screen, waitFor, act } from '@testing-library/react'
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
  // Detecta si la inspección elegida ya tiene estadillo subido (auto-marca
  // «omitir estadillo»). Se deja en `existe: false` por defecto para que
  // estos tests, centrados en la validación del estadillo NUEVO, no se
  // encuentren el checkbox de exención ya marcado por sorpresa.
  estadilloExistente: vi.fn(async () => ({ existe: false })),
}

// `onAnalisis` (Task 4/9) es el canal por el que `PanelSubida` recibe el plan
// de subida en hilo (`cloudPrepareStart` + evento `atom:analisis`, `scope:
// 'plan'`), sustituyendo al `cloudPrepare` síncrono de antes. Se implementa
// de verdad (no un stub `() => () => {}`) para poder emitir el «done» del
// plan desde los tests que dependen de `plan?.ok`.
vi.mock('../bridge', () => ({
  api,
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => false,
}))

const App = (await import('../App')).default

const emitirPlanOk = () =>
  act(() => {
    window.dispatchEvent(
      new CustomEvent('atom:analisis', {
        detail: { kind: 'done', scope: 'plan', data: { ok: true, prefix: 'ANTOLIN', files: 3, bytes: 10 } },
      })
    )
  })

// Navega hasta el tab «Trabajo» (activo por defecto tras Task 11), elige la
// carpeta del vuelo y la inspección (para que `prefijo` quede NO vacío, igual
// que en App.test.jsx), elige el destino «Subir al bucket» (monta
// PanelSubida) y elige un fichero de estadillo. Elegir el fichero YA dispara
// la validación automática (no hay botón «Comprobar» que pulsar).
//
// Elegir la inspección es imprescindible para que el botón «Subir al bucket»
// pueda depender de verdad de `estadCheck?.ok` en vez de quedar deshabilitado
// por `!prefijo`: sin este paso cualquier test sobre ese botón estaría verde
// en falso.
async function irAEstadilloConFichero(user) {
  render(<App />)

  // «Carpeta del vuelo»: primer paso de Trabajo, primer botón «Elegir…».
  const elegirCarpetaBotones = await screen.findAllByRole('button', { name: /elegir/i })
  await user.click(elegirCarpetaBotones[0])

  await user.click(await screen.findByRole('button', { name: 'ANTOLIN' }))

  // Destino «Subir al bucket»: monta PanelSubida (y, con él, el único botón
  // «Subir al bucket» de envío) sin el cual el estadillo no llega a validarse
  // contra un plan real.
  await user.click(await screen.findByText('Subir al bucket'))

  // El botón «Elegir…» del campo Estadillo es el segundo de la pantalla (el
  // primero, el de «Carpeta del vuelo», ya se ha usado arriba).
  const elegirBotones = await screen.findAllByRole('button', { name: /elegir/i })
  await user.click(elegirBotones[1])
}

// Además del estadillo, «Subir al bucket» exige un plan de carpeta válido
// (`plan?.ok`, resultado en hilo de `cloudPrepareStart`). Con la carpeta ya
// elegida en `irAEstadilloConFichero`, `PanelSubida` lo pide solo al montarse
// (mismo useEffect que dispara `cloudPrepareStart` en cuanto hay carpeta y
// prefijo); aquí solo hace falta emitir el evento `done` para que el plan
// llegue a `ok`, y así poder aislar el efecto de `estadCheck` sobre el botón.
async function completarPlanCarpeta() {
  await waitFor(() => expect(api.cloudPrepareStart).toHaveBeenCalled())
  emitirPlanOk()
}

describe('Estadillo (ubicación canónica del bucket)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.pickFile.mockResolvedValue('/home/saez/Descargas/estadillo.xlsx')
    api.pickFolder.mockResolvedValue('/home/saez/Descargas/ANTOLIN')
    api.cloudPrepareStart.mockResolvedValue({ started: true })
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
    await completarPlanCarpeta()

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
    await completarPlanCarpeta()

    await screen.findByText(/12/)
    expect(await screen.findByRole('button', { name: 'Subir al bucket' })).not.toBeDisabled()
  })

  // Antes (BucketScreen monolítico): pulsaba «Subir estadillo» y comprobaba
  // que el botón se deshabilitaba de inmediato, sin esperar al evento `start`.
  // Tras la partición en pasos, `estadSubiendo` vive dentro de `PasoEstadillo`
  // y viaja a `PanelSubida` como prop `estadilloSubiendo` (vía
  // `TrabajoScreen`), que la suma a su `ocupado`. El guardado anti doble-click
  // se conserva, ahora sobre el botón único «Subir al bucket».
  it('deshabilita subir al bucket mientras sube el estadillo', async () => {
    api.estadilloValidar.mockResolvedValue({
      ok: true,
      error: null,
      vuelos_detectados: 12,
      filas_con_problemas: 0,
    })
    let resolver
    api.estadilloSubir.mockReturnValue(
      new Promise((r) => {
        resolver = r
      }),
    )

    const user = userEvent.setup()
    await irAEstadilloConFichero(user)
    await completarPlanCarpeta()
    await screen.findByText(/12/)

    const botonSubir = await screen.findByRole('button', { name: 'Subir al bucket' })
    expect(botonSubir).not.toBeDisabled()

    await user.click(botonSubir)

    await waitFor(() => expect(api.estadilloSubir).toHaveBeenCalled())
    expect(api.cloudUpload).not.toHaveBeenCalled()
    // Guardado anti doble-click: mientras el estadillo está subiendo el botón
    // queda deshabilitado, sin esperar al evento `start` de las imágenes.
    await waitFor(() => expect(botonSubir).toBeDisabled())

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
    await completarPlanCarpeta()
    await screen.findByText(/5/)

    await user.click(await screen.findByRole('button', { name: 'Subir al bucket' }))

    expect(await screen.findByText('Primero inicia sesión')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Subir al bucket' })).not.toBeDisabled()
  })
})
