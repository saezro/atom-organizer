import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    colaListar: vi.fn(),
    colaQuitar: vi.fn().mockResolvedValue({ ok: true }),
    colaReintentar: vi.fn().mockResolvedValue({ ok: true }),
    colaReanudar: vi.fn().mockResolvedValue({ ok: true }),
    runCancelar: vi.fn().mockResolvedValue({ ok: true }),
  },
  onProgress: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:progress', w)
    return () => window.removeEventListener('atom:progress', w)
  },
}))
import { api } from '../bridge'
import ColaOrganizar from './ColaOrganizar'

const job = (id, estado, extra = {}) => ({
  id, estado, resumen: '', error: '',
  params: { origen: `/datos/${id}`, destino: `/out/${id}` }, ...extra,
})

beforeEach(() => vi.clearAllMocks())

describe('ColaOrganizar', () => {
  it('no pinta nada con la cola vacía', async () => {
    api.colaListar.mockResolvedValue({ ok: true, jobs: [], pausada: false })
    const { container } = render(<ColaOrganizar />)
    await waitFor(() => expect(api.colaListar).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('lista los trabajos con su estado y resumen', async () => {
    api.colaListar.mockResolvedValue({
      ok: true, pausada: false,
      jobs: [job('a', 'hecho', { resumen: '3 de 3 imágenes · 12 s.' }), job('b', 'en_curso'), job('c', 'pendiente')],
    })
    render(<ColaOrganizar />)
    expect(await screen.findByTestId('cola-job-a')).toBeTruthy()
    expect(screen.getByText('Hecho')).toBeTruthy()
    expect(screen.getByText('En curso')).toBeTruthy()
    expect(screen.getByText('Pendiente')).toBeTruthy()
    expect(screen.getByText(/3 de 3 imágenes/)).toBeTruthy()
  })

  it('en curso solo se puede cancelar (sin Quitar); cancelar llama a runCancelar', async () => {
    api.colaListar.mockResolvedValue({ ok: true, pausada: false, jobs: [job('b', 'en_curso')] })
    render(<ColaOrganizar />)
    const cancelar = await screen.findByText('Cancelar este')
    expect(screen.queryByText('Quitar')).toBeNull()
    fireEvent.click(cancelar)
    await waitFor(() => expect(api.runCancelar).toHaveBeenCalled())
    expect(api.colaQuitar).not.toHaveBeenCalled()
  })

  it('quitar y reintentar llaman al backend con el id', async () => {
    api.colaListar.mockResolvedValue({
      ok: true, pausada: false, jobs: [job('p', 'pendiente'), job('e', 'error', { error: 'Sin espacio' })],
    })
    render(<ColaOrganizar />)
    await screen.findByText('Reintentar')
    expect(screen.getByText('Sin espacio')).toBeTruthy()
    fireEvent.click(screen.getByText('Reintentar'))
    await waitFor(() => expect(api.colaReintentar).toHaveBeenCalledWith('e'))
    fireEvent.click(screen.getAllByText('Quitar')[0])
    await waitFor(() => expect(api.colaQuitar).toHaveBeenCalledWith('p'))
  })

  it('en pausa con pendientes ofrece Reanudar cola', async () => {
    api.colaListar.mockResolvedValue({
      ok: true, pausada: true, jobs: [job('i', 'interrumpido'), job('p', 'pendiente')],
    })
    render(<ColaOrganizar />)
    fireEvent.click(await screen.findByText('Reanudar cola'))
    await waitFor(() => expect(api.colaReanudar).toHaveBeenCalled())
    expect(screen.getByText('Interrumpido')).toBeTruthy()
  })

  it('sin pendientes no ofrece Reanudar aunque esté en pausa', async () => {
    api.colaListar.mockResolvedValue({ ok: true, pausada: true, jobs: [job('i', 'interrumpido')] })
    render(<ColaOrganizar />)
    await screen.findByTestId('cola-job-i')
    expect(screen.queryByText('Reanudar cola')).toBeNull()
  })

  it('se refresca con el evento cola del backend', async () => {
    api.colaListar.mockResolvedValueOnce({ ok: true, pausada: false, jobs: [job('a', 'en_curso')] })
    render(<ColaOrganizar />)
    await screen.findByText('En curso')
    api.colaListar.mockResolvedValue({ ok: true, pausada: false, jobs: [job('a', 'hecho')] })
    act(() => { window.dispatchEvent(new CustomEvent('atom:progress', { detail: { kind: 'cola' } })) })
    expect(await screen.findByText('Hecho')).toBeTruthy()
  })

  it('un error del backend se muestra, no se traga', async () => {
    api.colaListar.mockResolvedValue({ ok: true, pausada: false, jobs: [job('e', 'error')] })
    api.colaReintentar.mockResolvedValueOnce({ ok: false, error: 'Esa carpeta ya está pendiente' })
    render(<ColaOrganizar />)
    fireEvent.click(await screen.findByText('Reintentar'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/ya está pendiente/)
  })

  it('muestra por trabajo si se subirá y el estado de esa subida', async () => {
    api.colaListar.mockResolvedValue({
      ok: true, pausada: false,
      jobs: [
        job('a', 'pendiente', { subir_al_terminar: true, subida: null }),
        job('b', 'hecho', { subir_al_terminar: true, subida: { estado: 'encolada', detalle: '' } }),
        job('c', 'hecho', { subir_al_terminar: true, subida: { estado: 'omitida', detalle: 'no hay inspección elegida.' } }),
        job('d', 'error', { subir_al_terminar: true, subida: null }),
        job('e', 'hecho'), // trabajo viejo sin el campo: no sube, no se muestra nada
      ],
    })
    render(<ColaOrganizar />)
    expect(await screen.findByTestId('cola-subida-a')).toHaveTextContent('Se subirá a la nube al terminar')
    expect(screen.getByTestId('cola-subida-b')).toHaveTextContent('Subida del resultado en cola')
    expect(screen.getByTestId('cola-subida-c')).toHaveTextContent(/No se sube el resultado: no hay inspección/)
    expect(screen.getByTestId('cola-subida-d')).toHaveTextContent('No se subirá')
    expect(screen.queryByTestId('cola-subida-e')).toBeNull()
  })
})
