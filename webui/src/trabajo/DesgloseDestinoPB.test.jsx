import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import DesgloseDestinoPB from './DesgloseDestinoPB'

const por = [
  { pb: 'PB1', locales: 0, en_bucket: 0, pendientes: 0, distintos: 0, solo_bucket: 5, bytes_local: 0, bytes_pendientes: 0, bytes_solo_bucket: 9 },
  { pb: 'PB2', locales: 10, en_bucket: 10, pendientes: 0, distintos: 0, solo_bucket: 0, bytes_local: 10, bytes_pendientes: 0, bytes_solo_bucket: 0 },
  { pb: 'PB10', locales: 4, en_bucket: 1, pendientes: 2, distintos: 1, solo_bucket: 1, bytes_local: 4, bytes_pendientes: 2e9, bytes_solo_bucket: 1 },
]

describe('DesgloseDestinoPB', () => {
  it('estado por fila y títulos', () => {
    render(<DesgloseDestinoPB porPb={por} />)
    expect(screen.getByTestId('estado-PB1').textContent).toBe('solo en bucket')
    expect(screen.getByTestId('estado-PB2').textContent).toBe('completo')
    expect(screen.getByTestId('estado-PB10').textContent).toBe('falta')
    expect(screen.getByText('Faltan por subir')).toBeTruthy()
    expect(screen.getByText('Ya en bucket')).toBeTruthy()
    expect(screen.getByText('Solo en bucket (otras subidas)')).toBeTruthy()
  })
  it('fila total al pie', () => {
    render(<DesgloseDestinoPB porPb={por} />)
    const t = within(screen.getByTestId('desglose-total')).getAllByRole('cell').map((c) => c.textContent)
    expect(t.slice(0, 5)).toEqual(['14', '11', '2', '1', '6'])
  })
  it('sin datos no pinta', () => {
    const { container } = render(<DesgloseDestinoPB porPb={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
