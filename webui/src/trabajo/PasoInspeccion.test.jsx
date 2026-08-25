import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: { cloudInspecciones: vi.fn() },
}))
import { api } from '../bridge'
import PasoInspeccion from './PasoInspeccion'

beforeEach(() => vi.clearAllMocks())

describe('PasoInspeccion', () => {
  it('carga el catálogo al montar', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' })
    render(<PasoInspeccion ready prefijo="" onChange={() => {}} />)
    await waitFor(() => expect(api.cloudInspecciones).toHaveBeenCalled())
  })

  it('avisa cuando el catálogo falla', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: false, error: 'sin conexión' })
    render(<PasoInspeccion ready prefijo="" onChange={() => {}} />)
    expect(await screen.findByText(/sin conexión/)).toBeTruthy()
  })

  it('una vez elegida muestra el prefijo y deja cambiarlo', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' })
    const onChange = vi.fn()
    render(<PasoInspeccion ready prefijo="ACME--PLANTA--2026--TERMO" onChange={onChange} />)
    expect(await screen.findByDisplayValue('ACME--PLANTA--2026--TERMO')).toBeTruthy()
    fireEvent.click(screen.getByText(/Cambiar/i))
    expect(onChange).toHaveBeenCalledWith('', null)
  })
})
