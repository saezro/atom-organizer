import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import RevisionIndiceModal from './RevisionIndiceModal.jsx'

const vuelo = (pb, vuelo, ini, fin, fila) => ({ pb, vuelo, sufijo: '', inicio: ini, fin, angulo: 0, n_rgb: 10, n_t: 10, horas: null, fila })
const libre = (i, gid = 'g1') => ({ clave: `PRUEBA/f${i}.jpg`, nombre: `f${i}.jpg`, tipo: 'RGB', ts: `2026-05-04T10:4${i}:00`, grupo_id: gid, motivo: 'fuera-de-ventana' })
const rec = (extra = {}) => ({ id: 'g1:2:vecino', grupo_id: 'g1', n_fotos: 3, fila: 2, pb: '3', vuelo: '2', motivo: 'vecino', distancia_s: 45, confianza: 'alta', explicacion: 'vecino a 45 s', aplicada: false, ...extra })
const modeloBase = (extra = {}) => ({
  vuelos: [vuelo('1', '1', '2026-05-04T09:00:00', '2026-05-04T10:00:00', 1), vuelo('3', '2', '2026-05-04T11:00:00', '2026-05-04T12:00:00', 2)],
  libres: [libre(1), libre(2), libre(3)],
  recomendaciones: [rec()],
  espera: { estado: 'esperando', deadline_epoch: 1_000_000 + (9 * 60 + 41) * 1000, espera_min: 30 },
  ...extra,
})
const apiFalsa = () => ({
  latido: vi.fn(), aceptar: vi.fn().mockResolvedValue({ ok: true }), asignar: vi.fn().mockResolvedValue({ ok: true }),
  continuar: vi.fn().mockResolvedValue({ ok: true }), cancelar: vi.fn().mockResolvedValue({ ok: true }),
})

describe('RevisionIndiceModal', () => {
  it('cuenta atrás con ahora fijo', () => {
    render(<RevisionIndiceModal modelo={modeloBase()} api={apiFalsa()} ahora={() => 1_000_000} />)
    expect(screen.getByTestId('ri-cuenta-atras').textContent).toMatch(/se continuará solo en 9:41 si no tocas nada/i)
  })
  it('chip de recomendación y chip de aplicada', () => {
    const m = modeloBase({ recomendaciones: [rec(), rec({ id: 'g2:1:vecino', aplicada: true })] })
    render(<RevisionIndiceModal modelo={m} api={apiFalsa()} ahora={() => 1_000_000} />)
    expect(screen.getAllByText('→ PB3/V2 · a 45 s · confianza alta').length).toBe(2)
    expect(screen.getAllByText('aplicado automáticamente').length).toBe(1)
  })
  it('aceptar las de alta llama a api.aceptar([id]) y latido', async () => {
    const api = apiFalsa()
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    fireEvent.click(screen.getByText('Aceptar las de alta confianza'))
    await waitFor(() => expect(api.aceptar).toHaveBeenCalledWith(['g1:2:vecino']))
    expect(api.latido).toHaveBeenCalled()
  })
  it('el tramo se asigna completo con api.asignar(claves del tramo, fila)', async () => {
    const api = apiFalsa()
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    fireEvent.click(screen.getByTestId('ri-tramo-t0'))
    fireEvent.change(screen.getByLabelText('Vuelo destino'), { target: { value: '2' } })
    fireEvent.click(screen.getByText('Meter tramo en vuelo'))
    await waitFor(() => expect(api.asignar).toHaveBeenCalledWith(['PRUEBA/f1.jpg', 'PRUEBA/f2.jpg', 'PRUEBA/f3.jpg'], 2))
  })
  it('no hay forma de asignar una foto suelta: sin casillas y sin tramo no se habilita', () => {
    const api = apiFalsa()
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    expect(screen.queryAllByRole('checkbox').length).toBe(0)
    fireEvent.change(screen.getByLabelText('Vuelo destino'), { target: { value: '2' } })
    expect(screen.getByText('Meter tramo en vuelo').disabled).toBe(true)
    expect(api.asignar).not.toHaveBeenCalled()
  })
  it('fotos separadas más del hueco de corte forman tramos distintos', () => {
    const libres = [libre(1), { ...libre(2), ts: '2026-05-04T13:00:00' }]
    render(<RevisionIndiceModal modelo={modeloBase({ libres })} api={apiFalsa()} ahora={() => 1_000_000} />)
    expect(screen.getByText(/2 fotos sin colocar en 2 tramos/)).toBeTruthy()
  })
  it('aceptar la recomendación del tramo seleccionado', async () => {
    const api = apiFalsa()
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    fireEvent.click(screen.getByTestId('ri-tramo-t0'))
    fireEvent.click(screen.getByText('Aceptar recomendación del tramo'))
    await waitFor(() => expect(api.aceptar).toHaveBeenCalledWith(['g1:2:vecino']))
  })
  it('error {ok:false} visible', async () => {
    const api = apiFalsa()
    api.asignar.mockResolvedValue({ ok: false, error: 'no se pudo asignar: grupo inexistente' })
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    fireEvent.click(screen.getByTestId('ri-tramo-t0'))
    fireEvent.change(screen.getByLabelText('Vuelo destino'), { target: { value: '1' } })
    fireEvent.click(screen.getByText('Meter tramo en vuelo'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/grupo inexistente/)
  })
  it('desatendida: aviso con aplicadas y sin aplicar', () => {
    const m = modeloBase({
      espera: { estado: 'desatendida', deadline_epoch: 1, espera_min: 30 },
      recomendaciones: [rec({ aplicada: true }), rec({ id: 'b', confianza: 'media' }), rec({ id: 'c', confianza: 'baja' })],
    })
    render(<RevisionIndiceModal modelo={m} api={apiFalsa()} ahora={() => 5_000_000} />)
    expect(screen.getByTestId('ri-desatendida').textContent).toBe('Sin respuesta en 30 min: aplicada 1 recomendación de confianza alta; 2 quedan en SIN_ORDENAR.')
  })
  it('continuar y cancelar', async () => {
    const api = apiFalsa()
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    fireEvent.click(screen.getByText('Continuar'))
    await waitFor(() => expect(api.continuar).toHaveBeenCalledWith('tal_cual'))
    fireEvent.click(screen.getByText('Cancelar run'))
    await waitFor(() => expect(api.cancelar).toHaveBeenCalled())
  })
  it('cada vuelo muestra su flecha de giro y «?» si falta el dato', () => {
    const vs = [
      { ...vuelo('1', '1', '2026-05-04T09:00:00', '2026-05-04T10:00:00', 1), angulo: 0 },
      { ...vuelo('2', '1', '2026-05-04T11:00:00', '2026-05-04T12:00:00', 2), angulo: 90 },
      { ...vuelo('3', '1', '2026-05-04T13:00:00', '2026-05-04T14:00:00', 3), angulo: 270 },
      { ...vuelo('4', '1', '2026-05-04T15:00:00', '2026-05-04T16:00:00', 4), angulo: undefined },
    ]
    render(<RevisionIndiceModal modelo={modeloBase({ vuelos: vs })} api={apiFalsa()} ahora={() => 1_000_000} />)
    expect(screen.getAllByTestId('ri-giro').map((e) => e.getAttribute('aria-label'))).toEqual(['Sin giro (0º)', 'Gira 90º horario', 'Gira 270º antihorario', 'Giro desconocido'])
    expect(screen.getByText('?')).toBeTruthy()
    expect(screen.getByTestId('ri-leyenda')).toBeTruthy()
  })
  it('sin pendientes', () => {
    render(<RevisionIndiceModal modelo={modeloBase({ libres: [], recomendaciones: [], espera: null })} api={apiFalsa()} />)
    expect(screen.getByText(/No quedan fotos sin colocar ni recomendaciones/)).toBeTruthy()
    expect(screen.getByTestId('ri-calendario')).toBeTruthy()
    expect(screen.getByLabelText('Vuelo destino')).toBeTruthy()
  })

  it('«Dejar en SIN_ORDENAR» manda fila null; no hay opción engañosa de «Fotos generales»', async () => {
    const api = apiFalsa()
    render(<RevisionIndiceModal modelo={modeloBase()} api={api} ahora={() => 1_000_000} />)
    expect(screen.queryByText('Fotos generales')).toBeNull()
    fireEvent.click(screen.getByTestId('ri-tramo-t0'))
    fireEvent.change(screen.getByLabelText('Vuelo destino'), { target: { value: 'sin_ordenar' } })
    fireEvent.click(screen.getByText('Meter tramo en vuelo'))
    await waitFor(() => expect(api.asignar).toHaveBeenCalledWith(['PRUEBA/f1.jpg', 'PRUEBA/f2.jpg', 'PRUEBA/f3.jpg'], null))
  })
  it('una fila de generales del estadillo se elige por su fila real', async () => {
    const api = apiFalsa()
    const m = modeloBase()
    m.vuelos = [...m.vuelos, { ...vuelo('GEN', '1', '2026-05-04T13:00:00', '2026-05-04T14:00:00', 5), generales: true }]
    render(<RevisionIndiceModal modelo={m} api={api} ahora={() => 1_000_000} />)
    expect(screen.getByText(/Fila 5 · PBGEN · V1 · fotos generales/)).toBeTruthy()
    fireEvent.click(screen.getByTestId('ri-tramo-t0'))
    fireEvent.change(screen.getByLabelText('Vuelo destino'), { target: { value: '5' } })
    fireEvent.click(screen.getByText('Meter tramo en vuelo'))
    await waitFor(() => expect(api.asignar).toHaveBeenCalledWith(expect.any(Array), 5))
  })
  it('desatendida sin dato de espera no inventa los 30 min', () => {
    const m = modeloBase({ espera: { estado: 'desatendida', deadline_epoch: 1 } })
    render(<RevisionIndiceModal modelo={m} api={apiFalsa()} ahora={() => 1_000_000} />)
    expect(screen.getByTestId('ri-desatendida').textContent).toMatch(/^Sin respuesta durante la espera: aplicada/)
  })
})
