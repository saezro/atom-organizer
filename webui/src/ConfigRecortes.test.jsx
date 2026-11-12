import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConfigRecortes, { parsePct, claveDe, filasRecortes } from './ConfigRecortes'

const TABLA = [{ modelo: 'H30T', camara: 'Z', porcentaje: 48.5, origen: 'tabla', nota: 'zoom' },
               { modelo: 'M4T', camara: 'W', porcentaje: 70, origen: 'tabla', nota: '' }]

describe('ConfigRecortes', () => {
  it('parsePct admite coma y punto y rechaza fuera de rango', () => {
    expect(parsePct('48,5')).toBe(48.5)
    expect(parsePct('48.5')).toBe(48.5)
    expect(parsePct('0')).toBeNull()
    expect(parsePct('101')).toBeNull()
    expect(parsePct('abc')).toBeNull()
  })
  it('claveDe: W = MODELO, Z = MODELO_Z', () => {
    expect(claveDe('h30t', 'Z')).toBe('H30T_Z')
    expect(claveDe('m4t', 'W')).toBe('M4T')
  })
  it('el override gana a la tabla y conserva la nota', () => {
    const f = filasRecortes(TABLA, { H30T_Z: '50' }).find((r) => r.modelo === 'H30T')
    expect(f).toMatchObject({ origen: 'override', pct: '50', nota: 'zoom' })
  })
  it('añade override con coma decimal y lo quita', () => {
    const onChange = vi.fn()
    render(<ConfigRecortes tabla={TABLA} overrides={{}} onChange={onChange} loaded />)
    expect(screen.getAllByText('tabla')).toHaveLength(2)
    fireEvent.change(screen.getByPlaceholderText(/Modelo/), { target: { value: 'm30t' } })
    fireEvent.change(screen.getByLabelText('Cámara del override'), { target: { value: 'Z' } })
    fireEvent.change(screen.getByPlaceholderText(/%/), { target: { value: '48,5' } })
    fireEvent.click(screen.getByText('Añadir override'))
    expect(onChange).toHaveBeenCalledWith({ M30T_Z: '48,5' })
  })
  it('% inválido no añade', () => {
    const onChange = vi.fn()
    render(<ConfigRecortes tabla={TABLA} overrides={{}} onChange={onChange} loaded />)
    fireEvent.change(screen.getByPlaceholderText(/Modelo/), { target: { value: 'x' } })
    fireEvent.click(screen.getByText('Añadir override'))
    expect(onChange).not.toHaveBeenCalled()
  })
  it('quitar override', () => {
    const onChange = vi.fn()
    render(<ConfigRecortes tabla={TABLA} overrides={{ M4T: '60' }} onChange={onChange} loaded />)
    expect(screen.getByText('override')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Quitar override M4T W'))
    expect(onChange).toHaveBeenCalledWith({})
  })
})
