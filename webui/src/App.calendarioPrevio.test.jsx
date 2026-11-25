/**
 * Calendario previo en streaming: «Ejecutar» con estadillo abre el modal con el esqueleto de vuelos
 * al instante («cargando fotos…»), los parciales (`atom:analisis`, scope `calendario_previo`) rellenan
 * los conteos y `done` quita lo provisional. Eventos con otro `req_id` se descartan y cerrar el modal
 * cancela y se da de baja.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = {
  appVersion: vi.fn(async () => ({ version: '3.4.24' })),
  cloudStatus: vi.fn(async () => ({ configured: true, logged_in: false, estado: 'sin-credencial' })),
  cloudInspecciones: vi.fn(async () => ({ ok: true, origen: 'api', inspecciones: [] })),
  checkUpdate: vi.fn(async () => ({ ok: true, update_available: false })),
  readEstadilloInfo: vi.fn(async () => ({ pilotos: ['Ana'], drones: ['M3T'], num_vuelos: 1 })),
  calendarioPrevioIniciar: vi.fn(async () => ({ started: true })),
  calendarioPrevioCancelar: vi.fn(async () => ({ ok: true })),
  runTask: vi.fn(async () => ({ started: true })),
  revisionIndiceEstado: vi.fn(async () => ({ ok: true, activa: false, estado: null })),
}

const bajas = vi.hoisted(() => ({ n: 0 }))

vi.mock('./bridge', () => ({
  api,
  onResultado: () => () => {},
  whenBridgeReady: () => Promise.resolve(),
  onProgress: () => () => {},
  onCloud: () => () => {},
  onUpdate: () => () => {},
  onControlCarpeta: () => () => {},
  onControlUi: () => () => {},
  registerPicker: vi.fn(),
  isServerMode: () => false,
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
  onCalendarioPrevio: (h) => {
    const w = (e) => { if (e.detail && e.detail.scope === 'calendario_previo') h(e.detail) }
    window.addEventListener('atom:analisis', w)
    return () => { bajas.n += 1; window.removeEventListener('atom:analisis', w) }
  },
}))

// La pantalla de Trabajo real es enorme: basta un botón que llame a `onRun` como PanelOrganizar.
vi.mock('./trabajo/TrabajoScreen', () => ({
  default: ({ onRun }) => (
    <button type="button" onClick={() => onRun('split_images', { origen: '/o', destino: '/d', estadillo: ['/o/e.csv'] }, { x: 1 })}>
      lanzar-organizar
    </button>
  ),
}))

const App = (await import('./App')).default

const vuelo = (extra = {}) => ({
  fila: 1, pb: '1', vuelo: '1', sufijo: null, fecha: '2026-09-20', inicio: '2026-09-20T08:00:00', fin: '2026-09-20T09:00:00',
  generales: false, equipo: 'M3T', piloto: 'Ana', angulo: null, horas: [], n_total: 0, n_giro_desconocido: 0, previo: true, ...extra,
})
const modelo = (vuelos, extra = {}) => ({
  ok: true, previo: true, zona_horaria: 'exif-local', vuelos, libres: [], recomendaciones: [], grupos: [], avisos: [], n_fotos: 0, ...extra,
})
const emitir = (detail) => act(() => { window.dispatchEvent(new CustomEvent('atom:analisis', { detail })) })

describe('App: calendario previo en streaming', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    bajas.n = 0
    localStorage.setItem('atom.sesion.invitado', '1')   // sesión de invitado: sin pantalla de entrada
  })

  async function abrir() {
    render(<App />)
    fireEvent.click(await screen.findByText('lanzar-organizar'))
    await waitFor(() => expect(api.calendarioPrevioIniciar).toHaveBeenCalled())
    return api.calendarioPrevioIniciar.mock.calls[0][3]   // req_id
  }

  it('esqueleto con «cargando fotos…», parcial con conteos y done definitivo', async () => {
    const id = await abrir()
    expect(api.calendarioPrevioIniciar.mock.calls[0].slice(0, 3)).toEqual(['/o', ['/o/e.csv'], { x: 1 }])
    emitir({ kind: 'esqueleto', scope: 'calendario_previo', req_id: id, data: modelo([vuelo()]) })
    expect(await screen.findByTestId('ri-calendario')).toBeTruthy()
    expect(screen.getByTestId('ri-vuelo-equipo').textContent).toBe('M3T · Ana')
    expect(screen.getByTestId('ri-vuelo-fotos').textContent).toMatch(/cargando fotos…/)
    expect(screen.getByTestId('ri-giro').getAttribute('aria-label')).toMatch(/provisional/)
    expect(screen.getByTestId('pf-cal-leyendo')).toBeTruthy()

    const horas = [{ hora: '2026-09-20T08', n_rgb: 3, n_t: 3, n_otro: 0, n_90: 6, n_270: 0, n_0: 0, n_giro_desconocido: 0 }]
    emitir({ kind: 'parcial', scope: 'calendario_previo', req_id: id,
      data: modelo([vuelo({ horas, n_total: 6, angulo: 90 })], { n_fotos: 6 }) })
    await waitFor(() => expect(screen.getByTestId('ri-vuelo-fotos').textContent).toMatch(/3 fotos · cargando…/))
    expect(screen.getByTestId('pf-cal-leyendo')).toBeTruthy()

    emitir({ kind: 'done', scope: 'calendario_previo', req_id: id,
      data: modelo([vuelo({ horas, n_total: 6, angulo: 90 })], { n_fotos: 6, avisos: ['x'] }) })
    await waitFor(() => expect(screen.queryByTestId('pf-cal-leyendo')).toBeNull())
    expect(screen.getByTestId('ri-vuelo-fotos').textContent).toBe('3 fotos')
    expect(screen.getByTestId('ri-giro').getAttribute('aria-label')).not.toMatch(/provisional/)
    expect(bajas.n).toBe(1)   // dado de baja al terminar
  })

  it('descarta eventos con otro req_id y muestra el error visible', async () => {
    const id = await abrir()
    emitir({ kind: 'esqueleto', scope: 'calendario_previo', req_id: id + 100, data: modelo([vuelo()]) })
    expect(screen.queryByTestId('ri-calendario')).toBeNull()
    emitir({ kind: 'error', scope: 'calendario_previo', req_id: id, text: 'estadillo roto' })
    expect(await screen.findByText(/estadillo roto/)).toBeTruthy()
  })

  it('Cancelar cancela en backend y da de baja la suscripción: los eventos tardíos no reabren nada', async () => {
    const id = await abrir()
    emitir({ kind: 'esqueleto', scope: 'calendario_previo', req_id: id, data: modelo([vuelo()]) })
    await screen.findByTestId('ri-calendario')
    fireEvent.click(screen.getByText('Cancelar'))
    expect(api.calendarioPrevioCancelar).toHaveBeenCalled()
    expect(bajas.n).toBe(1)
    emitir({ kind: 'done', scope: 'calendario_previo', req_id: id, data: modelo([vuelo()]) })
    expect(screen.queryByTestId('ri-calendario')).toBeNull()
  })
})
