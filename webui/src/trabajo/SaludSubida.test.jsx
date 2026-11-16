import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { SemaforoSubida, TablaPorPB } from './SaludSubida'
import { textoListado } from './ProgresoSubida'

describe('TablaPorPB', () => {
  const por = [
    { pb: 'General', ficheros_hechos: 1, ficheros_total: 1, bytes_done: 5, bytes_total: 5, fallidas: 0, terminado: true },
    { pb: 'PB10', ficheros_hechos: 2, ficheros_total: 4, bytes_done: 1e9, bytes_total: 2e9, fallidas: 1, terminado: false },
    { pb: 'PB2', ficheros_hechos: 3, ficheros_total: 3, bytes_done: 3e9, bytes_total: 3e9, fallidas: 0, terminado: true },
  ]

  it('ordena PB numérico, marca ✓ en terminados y enseña fallidas', () => {
    render(<TablaPorPB porPb={por} />)
    const filas = within(screen.getByTestId('tabla-por-pb')).getAllByRole('row').slice(1)
    expect(filas.map((f) => within(f).getAllByRole('rowheader')[0].textContent.trim())).toEqual(['PB2 ✓', 'PB10', 'General ✓'])
    expect(filas[1].textContent).toContain('2 / 4')
    expect(filas[1].textContent).toContain('1,00 / 2,00 GB')
  })

  it('no pinta nada sin datos', () => {
    const { container } = render(<TablaPorPB porPb={undefined} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('SemaforoSubida y texto del listado', () => {
  it('pinta nivel, título y motivo', () => {
    render(<SemaforoSubida salud={{ nivel: 'rojo', titulo: 'Parado', motivo: '0 bytes nuevos en 61 s' }} />)
    const el = screen.getByTestId('semaforo-subida')
    expect(el.dataset.nivel).toBe('rojo')
    expect(el.textContent).toContain('Parado')
    expect(el.textContent).toContain('0 bytes nuevos en 61 s')
  })

  it('«Listando bucket: N objetos (p páginas)»', () => {
    expect(textoListado({ listados: 12500, paginas: 3 })).toBe('Listando bucket: 12.500 objetos (3 páginas)')
    expect(textoListado({ listados: 10, paginas: 1 })).toBe('Listando bucket: 10 objetos (1 página)')
    expect(textoListado({})).toBe('')
  })
})
