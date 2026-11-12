import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  sinColocarListar: vi.fn(),
  sinColocarAsignar: vi.fn(),
}))
vi.mock('./bridge', () => ({ api }))

import SinColocarModal from './SinColocarModal.jsx'

const vecinoAnt = { fila: 3, pb: '12', vuelo: '2', fecha: '2026-05-04',
  hora_inicio: '2026-05-04T09:00:00', hora_fin: '2026-05-04T09:40:00', distancia_s: 600 }
const vecinoSig = { fila: 4, pb: '12', vuelo: '3', fecha: '2026-05-04',
  hora_inicio: '2026-05-04T10:30:00', hora_fin: '2026-05-04T11:00:00', distancia_s: 1200 }
const grupo = (extra = {}) => ({
  grupo_id: 'g-1', ejecucion: 1, motivo: 'fuera-de-ventana',
  hora_inicio: '2026-05-04T09:50:00', hora_fin: '2026-05-04T10:10:00',
  n_fotos: 5, fotos_por_tipo: { RGB: 3, TERMICA: 2 }, estado: 'pendiente', decision: null,
  vecino_anterior: vecinoAnt, vecino_siguiente: vecinoSig, ...extra,
})
const filas = [
  { ejecucion: 1, fila: 3, pb: '12', vuelo: '2', fecha: '2026-05-04', hora_inicio: '2026-05-04T09:00:00', hora_fin: '2026-05-04T09:40:00' },
  { ejecucion: 1, fila: 4, pb: '12', vuelo: '3', fecha: '2026-05-04', hora_inicio: '2026-05-04T10:30:00', hora_fin: '2026-05-04T11:00:00' },
]

beforeEach(() => {
  api.sinColocarListar.mockReset()
  api.sinColocarAsignar.mockReset()
})

describe('SinColocarModal', () => {
  it('muestra el grupo con sus vecinos, franja y fotos por tipo', async () => {
    api.sinColocarListar.mockResolvedValue({ ok: true, grupos: [grupo()], filas })
    render(<SinColocarModal destino="/out" onClose={() => {}} />)
    expect(await screen.findByText('09:50–10:10')).toBeTruthy()
    expect(screen.getByText(/5 fotos \(3 RGB · 2 TERMICA\)/)).toBeTruthy()
    expect(screen.getByText(/fila 3 \(PB12\/V2, 09:00–09:40, a 10 min\)/)).toBeTruthy()
    expect(screen.getByText(/fila 4 \(PB12\/V3, 10:30–11:00, a 20 min\)/)).toBeTruthy()
    expect(screen.getByText(/hora del dron/)).toBeTruthy()
  })

  it('sin vecinos: sin botones anterior/siguiente activos y lo dice', async () => {
    api.sinColocarListar.mockResolvedValue({
      ok: true, filas,
      grupos: [grupo({ vecino_anterior: null, vecino_siguiente: null })],
    })
    render(<SinColocarModal destino="/out" onClose={() => {}} />)
    expect(await screen.findByText(/No hay filas vecinas/)).toBeTruthy()
    expect(screen.getByText(/Colocar en fila anterior/).disabled).toBe(true)
    expect(screen.getByText(/Colocar en fila siguiente/).disabled).toBe(true)
    expect(screen.getByText('Dejar en SIN_ORDENAR').disabled).toBe(false)
  })

  it('asignar a la fila anterior llama al bridge, refresca y ofrece relanzar', async () => {
    api.sinColocarListar
      .mockResolvedValueOnce({ ok: true, grupos: [grupo()], filas })
      .mockResolvedValue({ ok: true, grupos: [], filas: [] })
    api.sinColocarAsignar.mockResolvedValue({
      ok: true, resultado: { pendientes_apply: 5, recolocadas: 0, avisos: [] },
    })
    const onRelanzar = vi.fn()
    render(<SinColocarModal destino="/out" onClose={() => {}} onRelanzar={onRelanzar} />)
    fireEvent.click(await screen.findByText(/Colocar en fila anterior/))
    await waitFor(() => expect(api.sinColocarAsignar).toHaveBeenCalledWith('g-1', 3, '/out'))
    expect(await screen.findByText(/No quedan fotos sin colocar/)).toBeTruthy()
    fireEvent.click(screen.getByText('Relanzar aplicar'))
    expect(onRelanzar).toHaveBeenCalled()
  })

  it('«Dejar en SIN_ORDENAR» manda fila null y no pide relanzar', async () => {
    api.sinColocarListar.mockResolvedValue({ ok: true, grupos: [grupo()], filas })
    api.sinColocarAsignar.mockResolvedValue({ ok: true, resultado: { pendientes_apply: 5 } })
    render(<SinColocarModal destino="/out" onClose={() => {}} onRelanzar={() => {}} />)
    fireEvent.click(await screen.findByText('Dejar en SIN_ORDENAR'))
    await waitFor(() => expect(api.sinColocarAsignar).toHaveBeenCalledWith('g-1', null, '/out'))
    expect(screen.queryByText('Relanzar aplicar')).toBeNull()
  })

  it('el selector coloca en una fila elegida', async () => {
    api.sinColocarListar.mockResolvedValue({ ok: true, grupos: [grupo()], filas })
    api.sinColocarAsignar.mockResolvedValue({ ok: true, resultado: { pendientes_apply: 5 } })
    render(<SinColocarModal destino="/out" onClose={() => {}} />)
    const sel = await screen.findByLabelText(/Fila del estadillo/)
    fireEvent.change(sel, { target: { value: '4' } })
    fireEvent.click(screen.getByText('Colocar'))
    await waitFor(() => expect(api.sinColocarAsignar).toHaveBeenCalledWith('g-1', 4, '/out'))
  })

  it('error del bridge al asignar se muestra, no se traga', async () => {
    api.sinColocarListar.mockResolvedValue({ ok: true, grupos: [grupo()], filas })
    api.sinColocarAsignar.mockResolvedValue({ ok: false, error: 'ErrorSinColocar: fila inexistente' })
    render(<SinColocarModal destino="/out" onClose={() => {}} />)
    fireEvent.click(await screen.findByText(/Colocar en fila siguiente/))
    expect((await screen.findByRole('alert')).textContent).toMatch(/fila inexistente/)
    expect(screen.getByText('09:50–10:10')).toBeTruthy()
  })

  it('error al listar se muestra', async () => {
    api.sinColocarListar.mockResolvedValue({ ok: false, error: 'ErrorSinColocar: no hay manifiesto' })
    render(<SinColocarModal destino="/out" onClose={() => {}} />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/no hay manifiesto/)
  })
})
