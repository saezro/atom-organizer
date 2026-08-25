import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    pickFolder: vi.fn(),
    pickFile: vi.fn(),
    folderIsEmpty: vi.fn().mockResolvedValue({ empty: true }),
    detectSuffixesStart: vi.fn().mockResolvedValue({ started: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
  },
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
}))
import { api } from '../bridge'
import PanelOrganizar from './PanelOrganizar'

function emitirAnalisis(detail) {
  act(() => { window.dispatchEvent(new CustomEvent('atom:analisis', { detail })) })
}

beforeEach(() => vi.clearAllMocks())

describe('PanelOrganizar', () => {
  it('arranca la autodetección de sufijos cuando llega el origen', async () => {
    const { rerender } = render(
      <PanelOrganizar origen="" estadillos={[]} ready running={false} onRun={() => {}} />)
    rerender(
      <PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    await waitFor(() => expect(api.detectSuffixesStart).toHaveBeenCalledWith('/datos/vuelo'))
  })

  it('llama a analisisReset antes que a detectSuffixesStart', async () => {
    const orden = []
    api.analisisReset.mockImplementation(() => { orden.push('reset'); return Promise.resolve({ ok: true }) })
    api.detectSuffixesStart.mockImplementation(() => { orden.push('start'); return Promise.resolve({ started: true }) })
    const { rerender } = render(
      <PanelOrganizar origen="" estadillos={[]} ready running={false} onRun={() => {}} />)
    rerender(
      <PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    await waitFor(() => expect(api.detectSuffixesStart).toHaveBeenCalled())
    expect(orden).toEqual(['reset', 'start'])
  })

  it('rellena los sufijos con el resultado del evento done', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'done', scope: 'suffixes', data: { ok: true, thermal: '_T', rgb: '_W' } })
    await waitFor(() => expect(screen.getByDisplayValue('_T')).toBeTruthy())
    expect(screen.getByDisplayValue('_W')).toBeTruthy()
  })

  it('muestra el avance mientras escanea y permite cancelar', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'scan', scope: 'suffixes', done: 500 })
    expect(await screen.findByText(/500/)).toBeTruthy()
    fireEvent.click(screen.getByText(/Cancelar/i))
    expect(api.analisisCancel).toHaveBeenCalled()
  })

  it('el botón Ejecutar está desactivado sin carpeta final', () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    expect(screen.getByText(/Ejecutar/i).disabled).toBe(true)
  })

  it('ejecuta split_images con los parámetros correctos', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    const onRun = vi.fn()
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx']} ready running={false} onRun={onRun} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    fireEvent.click(screen.getByText(/Ejecutar/i))
    expect(onRun).toHaveBeenCalledWith(
      'split_images',
      expect.objectContaining({ origen: '/datos/vuelo', destino: '/datos/final', estadillo: ['/e.xlsx'] }),
      expect.anything(),
    )
  })
})
