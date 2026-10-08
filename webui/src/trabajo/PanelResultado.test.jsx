import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    resultadoSubir: vi.fn().mockResolvedValue({ started: true }),
    resultadoCancelar: vi.fn().mockResolvedValue({ ok: true }),
  },
  onResultado: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:resultado', w)
    return () => window.removeEventListener('atom:resultado', w)
  },
}))
import { api } from '../bridge'
import PanelResultado, { textoProgreso } from './PanelResultado'

const emitir = (detail) => act(() => { window.dispatchEvent(new CustomEvent('atom:resultado', { detail })) })

beforeEach(() => vi.clearAllMocks())

describe('textoProgreso', () => {
  it('formatea «urgentes X/Y · resto X/Y»', () => {
    expect(textoProgreso({ urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 25 } }))
      .toBe('urgentes 3/10 · resto 0/25')
  })
  it('vacío sin datos', () => { expect(textoProgreso(null)).toBe('') })
})

describe('PanelResultado', () => {
  it('con Urgencia por defecto, el botón lanza resultadoSubir(carpeta, id, urgencia, false)', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(screen.getByRole('radio', { name: 'Urgencia' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Publicar resultado' }))
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', false))
  })

  it('modo Normal manda «normal»', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    fireEvent.click(screen.getByRole('radio', { name: 'Normal' }))
    fireEvent.click(screen.getByRole('button', { name: 'Publicar resultado' }))
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'normal', false))
  })

  it('sin carpeta o sin inspección el botón está deshabilitado y lo explica', () => {
    render(<PanelResultado carpeta="" inspeccionId={null} ready />)
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
    expect(screen.getByText(/Elige la inspección/)).toBeInTheDocument()
  })

  it('pinta el progreso y deshabilita el botón mientras sube', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'estado', fase: 'urgentes', urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 25 } })
    expect(await screen.findByText('urgentes 3/10 · resto 0/25')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument()
  })

  it('muestra conflictos y fallidas y permite reintentar', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'done', ok: false, modo: 'urgencia', continua: false,
      urgentes: { hechos: 4, total: 5 }, resto: { hechos: 0, total: 3 },
      conflictos: [['P/TERMICA/A_T.tiff', 'ya existe con otro contenido']],
      fallidas: [['P/RGB/B_W_CROP.JPG', 'boom']], avisos: ['2 fichero(s) sin clasificar'], sin_clasificar: 2 })
    expect(await screen.findByText(/1 en conflicto/)).toBeInTheDocument()
    expect(screen.getByText(/1 fallida/)).toBeInTheDocument()
    expect(screen.getByText(/sin clasificar/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resubir fallidas' }))
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', false))
  })

  it('un done con continua:true no cierra el estado «subiendo»', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'done', ok: true, modo: 'urgencia', continua: true,
      urgentes: { hechos: 5, total: 5 }, resto: { hechos: 0, total: 3 }, conflictos: [], fallidas: [], avisos: [] })
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
    emitir({ kind: 'done', ok: true, modo: 'normal', continua: false,
      urgentes: { hechos: 5, total: 5 }, resto: { hechos: 3, total: 3 }, conflictos: [], fallidas: [], avisos: [] })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar resultado' })).not.toBeDisabled())
    expect(screen.getByText('Subida completa')).toBeInTheDocument()
  })

  it('muestra errores y avisos tal cual', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'error', text: 'La inspección no tiene planta o año asignados en ATOM Suite' })
    expect(await screen.findByText(/no tiene planta o año/)).toBeInTheDocument()
    emitir({ kind: 'aviso', text: 'No se sube el resultado al bucket: no hay inspección elegida.' })
    expect(await screen.findByText(/no hay inspección elegida/)).toBeInTheDocument()
  })

  it('ignora eventos de otros canales (no hay scope mezclado)', () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    act(() => { window.dispatchEvent(new CustomEvent('atom:cloud', { detail: { kind: 'done', ok: true } })) })
    expect(screen.queryByText('Subida completa')).toBeNull()
  })

  it('si resultadoSubir rechaza (kiosco sin el método) el error se ve', async () => {
    api.resultadoSubir.mockRejectedValueOnce(new Error('método no expuesto'))
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    fireEvent.click(screen.getByRole('button', { name: 'Publicar resultado' }))
    expect(await screen.findByText(/método no expuesto/)).toBeInTheDocument()
  })
})
