import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    cloudStatus: vi.fn().mockResolvedValue({ configured: true, logged_in: true, email: 'a@b.c' }),
    cloudVerify: vi.fn().mockResolvedValue({ ok: true, text: 'sesión válida' }),
    cloudLogin: vi.fn(), cloudLogout: vi.fn().mockResolvedValue({ ok: true }),
    cloudPrepareStart: vi.fn().mockResolvedValue({ started: true }),
    cloudUpload: vi.fn().mockResolvedValue({ started: true }),
    cloudCancel: vi.fn().mockResolvedValue({ ok: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
  },
  onCloud: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:cloud', w)
    return () => window.removeEventListener('atom:cloud', w)
  },
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
}))
import { api } from '../bridge'
import PanelSubida from './PanelSubida'

const emitir = (canal, detail) =>
  act(() => { window.dispatchEvent(new CustomEvent(canal, { detail })) })

const props = {
  carpeta: '/datos/vuelo', prefijo: 'ACME--P--2026--T', inspeccionId: 7,
  estadilloListo: true, subirEstadillo: vi.fn().mockResolvedValue(undefined), ready: true,
}

beforeEach(() => vi.clearAllMocks())

describe('PanelSubida', () => {
  it('pide el plan en hilo al tener carpeta y prefijo', async () => {
    render(<PanelSubida {...props} />)
    await waitFor(() =>
      expect(api.cloudPrepareStart).toHaveBeenCalledWith('/datos/vuelo', 'ACME--P--2026--T'))
  })

  it('habilita Subir solo cuando el plan llega ok', async () => {
    render(<PanelSubida {...props} />)
    expect(screen.getByText(/Subir al bucket/i).disabled).toBe(true)
    emitir('atom:analisis', { kind: 'done', scope: 'plan', data: { ok: true, files: 30, bytes: 100 } })
    await waitFor(() => expect(screen.getByText(/Subir al bucket/i).disabled).toBe(false))
  })

  it('sube el estadillo antes que las imágenes', async () => {
    render(<PanelSubida {...props} />)
    emitir('atom:analisis', { kind: 'done', scope: 'plan', data: { ok: true, files: 30, bytes: 100 } })
    await waitFor(() => expect(screen.getByText(/Subir al bucket/i).disabled).toBe(false))
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(props.subirEstadillo).toHaveBeenCalled())
    await waitFor(() => expect(api.cloudUpload).toHaveBeenCalled())
  })

  it('no sube imágenes si el estadillo falla', async () => {
    const subirEstadillo = vi.fn().mockRejectedValue(new Error('estadillo inválido'))
    render(<PanelSubida {...props} subirEstadillo={subirEstadillo} />)
    emitir('atom:analisis', { kind: 'done', scope: 'plan', data: { ok: true, files: 30, bytes: 100 } })
    await waitFor(() => expect(screen.getByText(/Subir al bucket/i).disabled).toBe(false))
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    expect(await screen.findByText(/estadillo inválido/)).toBeTruthy()
    expect(api.cloudUpload).not.toHaveBeenCalled()
  })

  it('durante la subida ofrece cancelar', async () => {
    render(<PanelSubida {...props} />)
    emitir('atom:cloud', { kind: 'start', files: 30, bytes: 100, prefix: 'ACME--P--2026--T' })
    emitir('atom:cloud', { kind: 'stats', done: 10, total: 30 })
    fireEvent.click(await screen.findByText(/Cancelar subida/i))
    expect(api.cloudCancel).toHaveBeenCalled()
  })

  it('avisa a onSubidaOk cuando la subida termina bien', async () => {
    const onSubidaOk = vi.fn()
    render(<PanelSubida {...props} onSubidaOk={onSubidaOk} />)
    emitir('atom:cloud', { kind: 'done', ok: true, uploaded: 30, cancelled: false })
    await waitFor(() => expect(onSubidaOk).toHaveBeenCalledWith(expect.objectContaining({ ok: true })))
  })

  // R1: el backend NO resetea `_cancel_analisis` dentro de `cloud_prepare_start`
  // (decisión de diseño deliberada, solo lo limpia `analisis_reset`). Sin este
  // orden, tras una cancelación previa el siguiente análisis del plan aborta
  // al instante en silencio.
  it('llama a analisisReset antes que a cloudPrepareStart', async () => {
    const orden = []
    api.analisisReset.mockImplementation(() => { orden.push('reset'); return Promise.resolve({ ok: true }) })
    api.cloudPrepareStart.mockImplementation(() => { orden.push('start'); return Promise.resolve({ started: true }) })
    render(<PanelSubida {...props} />)
    await waitFor(() => expect(api.cloudPrepareStart).toHaveBeenCalled())
    expect(orden).toEqual(['reset', 'start'])
  })

  // R2: bug preexistente — en modo servidor, tras hacer logout en esta
  // pantalla y volver al kiosco sin remontar `App`, `kioskCloudStatus` se
  // quedaba con `estado: 'ok'` y "Subir en crudo" seguía pulsable sin sesión.
  // `onCloudStatusChange` es el cable que `App` (Task 11) usará para
  // mantener sincronizado su propio estado de nube del kiosco.
  it('avisa a onCloudStatusChange con el estado ya sin sesión tras logout', async () => {
    const onCloudStatusChange = vi.fn()
    api.cloudStatus
      .mockResolvedValueOnce({ configured: true, logged_in: true, email: 'a@b.c' })
      .mockResolvedValueOnce({ configured: true, logged_in: false })
    render(<PanelSubida {...props} onCloudStatusChange={onCloudStatusChange} />)
    await waitFor(() =>
      expect(onCloudStatusChange).toHaveBeenCalledWith(
        expect.objectContaining({ logged_in: true })))
    fireEvent.click(await screen.findByText(/Cerrar sesión/i))
    await waitFor(() => expect(api.cloudLogout).toHaveBeenCalled())
    await waitFor(() =>
      expect(onCloudStatusChange).toHaveBeenCalledWith(
        expect.objectContaining({ logged_in: false })))
  })
})
