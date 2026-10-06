import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../bridge', () => ({ api: { cloudUpload: vi.fn() } }))
import { api } from '../bridge'
import cloudUploadConfirmando from './cloudUploadConfirmando'

describe('cloudUploadConfirmando', () => {
  beforeEach(() => {
    api.cloudUpload.mockReset()
    api.cloudUpload.mockResolvedValueOnce({ requiere_confirmacion: true, lote_anterior: 'L1' })
  })

  it('doble confirmación: con las dos aceptadas reintenta forzando', async () => {
    api.cloudUpload.mockResolvedValueOnce({ started: true })
    const confirmar = vi.fn().mockResolvedValue(true)
    const r = await cloudUploadConfirmando('/c', 'P', 1, confirmar)
    expect(confirmar).toHaveBeenCalledTimes(2)
    expect(confirmar.mock.calls[0][0]).toMatch(/lote L1/)
    expect(r).toEqual({ started: true })
    expect(api.cloudUpload).toHaveBeenLastCalledWith('/c', false, 'P', 1, true)
  })

  it('si se rechaza la segunda no sube', async () => {
    const confirmar = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false)
    const r = await cloudUploadConfirmando('/c', 'P', 1, confirmar)
    expect(r.started).toBe(false)
    expect(api.cloudUpload).toHaveBeenCalledTimes(1)
  })
})
