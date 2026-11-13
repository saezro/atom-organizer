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
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const progreso = vi.hoisted(() => ({ cb: null }))

const api = {
  revisionIndiceCalendario: vi.fn(),
  revisionIndiceEstado: vi.fn(async () => ({ ok: true, activa: false, estado: null })),
  revisionIndiceLatido: vi.fn(async () => ({ ok: true })),
  revisionIndiceAsignar: vi.fn(),
  revisionIndiceGuardarEspera: vi.fn(async (n) => ({ ok: true, espera_min: n })),
  renderEstado: vi.fn(async () => ({ modo: 'auto', activa: true })),
  estadoUpdate: vi.fn(async () => ({ ok: true, update_available: false, current: '3.4.24', latest: '3.4.24' })),
  revisionIndiceContinuar: vi.fn(async () => ({ ok: true })),
  sinColocarListar: vi.fn(async () => ({ ok: true, grupos: [{ grupo_id: 'g1' }], filas: [] })),
  runCancelar: vi.fn(async () => ({})),
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(async () => ({
    configured: true,
    logged_in: true,
    email: 'operador@ejemplo.com',
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
  // La pestaña «Subir resultado» (PanelResultado) está siempre montada en Trabajo.
  onResultado: () => () => {},
  whenBridgeReady: () => Promise.resolve(),
  onProgress: (cb) => { progreso.cb = cb; return () => {} },
  onCloud: () => () => {},
  onAnalisis: () => () => {},
  onControlCarpeta: () => () => {},
  onControlUi: () => () => {},
  onUpdate: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => false,
}))

const App = (await import('./App')).default
const { act } = await import('@testing-library/react')

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

    // La app arranca en «Trabajo»; se pulsa la pestaña por si acaso antes de que
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
    await user.click(await screen.findByText('Subir sin organizar'))

    // Lo que el bug rompía: la carpeta se seleccionaba pero el plan no se pedía
    // nunca, así que «2.518 ya subidos» no llegaba a calcularse.
    await waitFor(() =>
      expect(api.cloudPrepareStart).toHaveBeenCalledWith('/home/saez/Descargas/ANTOLIN', 'ANTOLIN')
    )
  })
})

// Revisión del índice: el evento `revision` abre el modal con el Modelo del calendario y
// sustituye a «fotos sin colocar» (SinColocarModal) para ese run.
const calendario = (extra = {}) => ({
  ok: true, ejecucion: 1, zona_horaria: 'exif-local',
  espera: { activa: true, estado: 'esperando', deadline_epoch: (Date.now() + 9 * 60 * 1000) / 1000, inactividad_s: 0 },
  vuelos: [{ fila: 1, pb: 3, vuelo: 2, sufijo: '', fecha: '2026-06-01', inicio: '2026-06-01T10:00:00',
    fin: '2026-06-01T10:30:00', generales: false, angulo: 0, n_total: 4, n_giro_desconocido: 0,
    horas: [{ hora: '2026-06-01T10', n_rgb: 2, n_t: 2, n_otro: 0 }] }],
  libres: [{ clave: 'k1', nombre: 'a.jpg', tipo: 'RGB', ts: '2026-06-01T12:00:00', grupo_id: 'g1', motivo: 'fuera' }],
  grupos: [], recomendaciones: [], ...extra,
})

describe('Revisión del índice en App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('stats con sin_asignar NO abre SinColocarModal tras el evento revision', async () => {
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    expect(await screen.findByRole('dialog', { name: 'Revisión del índice' })).toBeTruthy()
    await act(async () => { progreso.cb({ kind: 'stats', data: { vuelos: 3, sin_asignar: 5 } }) })
    expect(api.sinColocarListar).not.toHaveBeenCalled()
  })

  it('sin revision, stats con sin_asignar comprueba fotos sin colocar como hoy', async () => {
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'stats', data: { vuelos: 3, sin_asignar: 5 } }) })
    await waitFor(() => expect(api.sinColocarListar).toHaveBeenCalled())
  })

  it('el aviso de giro desconocido del calendario se ve', async () => {
    const c = calendario()
    c.vuelos[0].n_giro_desconocido = 3
    api.revisionIndiceCalendario.mockResolvedValue(c)
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    expect((await screen.findByTestId('ri-aviso-dato')).textContent).toMatch(/3 fotos.*giro/)
  })

  it('un fallo del calendario se enseña, no se traga', async () => {
    api.revisionIndiceCalendario.mockResolvedValue({ ok: false, error: 'boom' })
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    expect((await screen.findByTestId('ri-fallo')).textContent).toMatch(/boom/)
  })

  it('el modal se cierra al seguir el run (phase)', async () => {
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    await screen.findByRole('dialog', { name: 'Revisión del índice' })
    await act(async () => { progreso.cb({ kind: 'phase', data: { index: 2, total: 3, name: 'x' } }) })
    expect(screen.queryByRole('dialog', { name: 'Revisión del índice' })).toBeNull()
  })

  it('carrera stats -> revision: la respuesta tardía de sinColocarListar NO abre SinColocarModal', async () => {
    let resolver
    api.sinColocarListar.mockImplementationOnce(() => new Promise((r) => { resolver = r }))
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'stats', data: { vuelos: 3, sin_asignar: 5 } }) })
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    await screen.findByRole('dialog', { name: 'Revisión del índice' })
    await act(async () => { resolver({ ok: true, grupos: [{ grupo_id: 'g1' }], filas: [] }) })
    expect(screen.queryByRole('dialog', { name: 'Fotos sin colocar' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Revisión del índice' })).toBeTruthy()
  })

  it('al montar con una pausa ya abierta recupera el modal con la cuenta atrás real', async () => {
    api.revisionIndiceEstado.mockResolvedValueOnce({ ok: true, activa: true, estado: 'esperando' })
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    render(<App />)
    expect(await screen.findByRole('dialog', { name: 'Revisión del índice' })).toBeTruthy()
    const txt = (await screen.findByTestId('ri-cuenta-atras')).textContent
    const [, m] = /en (\d+):/.exec(txt)
    expect(Number(m)).toBeLessThanOrEqual(9)
  })

  it('un asignar fallido recarga el calendario (pudo escribir a medias) y enseña el error', async () => {
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    api.revisionIndiceAsignar.mockResolvedValue({ ok: false, error: 'asignación a medias: 1 de 2 grupos' })
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    await screen.findByRole('dialog', { name: 'Revisión del índice' })
    const antes = api.revisionIndiceCalendario.mock.calls.length
    await act(async () => { screen.getByTestId('ri-tramo-t0').click() })
    await act(async () => { fireEvent.change(screen.getByLabelText('Vuelo destino'), { target: { value: '1' } }) })
    await act(async () => { screen.getByText('Meter tramo en vuelo').click() })
    await waitFor(() => expect(api.revisionIndiceAsignar).toHaveBeenCalled())
    await waitFor(() => expect(api.revisionIndiceCalendario.mock.calls.length).toBe(antes + 1))
    expect((await screen.findAllByText(/asignación a medias/)).length).toBeGreaterThan(0)
  })

  it('al reconectar el SSE con la pausa abierta recupera el modal', async () => {
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    render(<App />)
    await waitFor(() => expect(api.revisionIndiceEstado).toHaveBeenCalled())
    expect(screen.queryByRole('dialog', { name: 'Revisión del índice' })).toBeNull()
    api.revisionIndiceEstado.mockResolvedValueOnce({ ok: true, activa: true, estado: 'esperando' })
    await act(async () => { window.dispatchEvent(new CustomEvent('atom:sse_reconectado')) })
    expect(await screen.findByRole('dialog', { name: 'Revisión del índice' })).toBeTruthy()
  })

  it('al reconectar el SSE sin pausa cierra un modal obsoleto (el evento phase se perdió)', async () => {
    api.revisionIndiceCalendario.mockResolvedValue(calendario())
    render(<App />)
    await waitFor(() => expect(progreso.cb).toBeTruthy())
    await act(async () => { progreso.cb({ kind: 'revision', data: { estado: 'esperando', espera_s: 1800 } }) })
    await screen.findByRole('dialog', { name: 'Revisión del índice' })
    await act(async () => { window.dispatchEvent(new CustomEvent('atom:sse_reconectado')) })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Revisión del índice' })).toBeNull())
  })
})

describe('Ajustes · espera de la revisión del índice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })
  async function irAAjustes() {
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('tab', { name: 'Ajustes' }))
    await screen.findByText('Actualizaciones')
    return user
  }

  it('flag apagado: no se ve el campo ni se guarda nada', async () => {
    api.revisionIndiceEstado.mockResolvedValue({ ok: true, disponible: false, activa: false, estado: null })
    await irAAjustes()
    expect(screen.queryByLabelText('Esperar mi respuesta tras el índice (min)')).toBeNull()
    expect(api.revisionIndiceGuardarEspera).not.toHaveBeenCalled()
  })

  it('flag encendido: campo visible; blur sin cambios no guarda; con cambio guarda en front y backend', async () => {
    api.revisionIndiceEstado.mockResolvedValue({ ok: true, disponible: true, activa: false, estado: null })
    const user = await irAAjustes()
    const campo = await screen.findByLabelText('Esperar mi respuesta tras el índice (min)')
    await user.click(campo)
    await user.tab()
    expect(localStorage.getItem('atom.revision_indice.espera_min')).toBeNull()
    expect(api.revisionIndiceGuardarEspera).not.toHaveBeenCalled()
    await user.clear(campo)
    await user.type(campo, '12')
    await user.tab()
    await waitFor(() => expect(api.revisionIndiceGuardarEspera).toHaveBeenCalledWith(12))
    expect(localStorage.getItem('atom.revision_indice.espera_min')).toBe('12')
  })

  it('si el backend no guarda, se dice (los runs remotos no lo usarán)', async () => {
    api.revisionIndiceEstado.mockResolvedValue({ ok: true, disponible: true, activa: false, estado: null })
    api.revisionIndiceGuardarEspera.mockResolvedValueOnce({ ok: false, error: 'disco lleno' })
    const user = await irAAjustes()
    const campo = await screen.findByLabelText('Esperar mi respuesta tras el índice (min)')
    await user.clear(campo)
    await user.type(campo, '9')
    await user.tab()
    expect((await screen.findByText(/no en el equipo.*disco lleno/)).textContent).toMatch(/runs remotos/)
  })
})
