import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PreflightModal from './PreflightModal.jsx'
import { modeloDesdeCalendario } from './calendarioHoras.js'

const calendario = {
  ok: true, previo: true, zona_horaria: 'exif-local', recomendaciones: [], grupos: [], avisos: ['1 fotos sin hora EXIF legible: no se pintan.'],
  vuelos: [{
    fila: 1, pb: '1', vuelo: '1', sufijo: null, fecha: '2026-09-20', inicio: '2026-09-20T08:00:00', fin: '2026-09-20T08:30:00',
    generales: false, equipo: 'M3T', piloto: 'Ana', angulo: null, horas: [], n_total: 0, n_giro_desconocido: 0, previo: true,
  }],
  libres: [{ clave: 'x.JPG', nombre: 'x.JPG', tipo: 'RGB', ts: '2026-09-20T10:00:00', grupo_id: 'previo', motivo: 'fuera' }],
}

describe('PreflightModal', () => {
  it('muestra el calendario con dron y piloto y el tramo libre en gris', () => {
    const onStart = vi.fn()
    render(<PreflightModal info={{ vuelos: [], num_vuelos: 1 }} calendario={calendario} loading={false} onStart={onStart} onCancel={() => {}} />)
    expect(screen.getByTestId('ri-calendario')).toBeTruthy()
    expect(screen.getByTestId('ri-vuelo-equipo').textContent).toBe('M3T · Ana')
    expect(screen.getByTestId('pf-aviso-dato').textContent).toMatch(/sin hora EXIF/)
    expect(screen.getByTestId('ri-giro').textContent).toBe('?') // el giro se pinta siempre, '?' si no hay dato
    fireEvent.click(screen.getByText('Comenzar'))
    expect(onStart).toHaveBeenCalled()
  })

  it('con el calendario cargando muestra spinner propio y Comenzar/Cancelar siguen activos', () => {
    const onStart = vi.fn()
    const onCancel = vi.fn()
    render(<PreflightModal info={{ vuelos: [], num_vuelos: 1, pilotos: ['Ana'] }} calendario={null} calCargando loading={false} onStart={onStart} onCancel={onCancel} />)
    expect(screen.getByTestId('pf-cal-cargando')).toBeTruthy()
    expect(screen.getByText('Ana')).toBeTruthy()           // la info del estadillo ya está visible
    expect(screen.queryByTestId('ri-calendario')).toBeNull()
    expect(screen.getByText('Comenzar').disabled).toBe(false)
    expect(screen.getByText('Cancelar').disabled).toBe(false)
    fireEvent.click(screen.getByText('Comenzar'))
    fireEvent.click(screen.getByText('Cancelar'))
    expect(onStart).toHaveBeenCalledTimes(1)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('al llegar el calendario desaparece el spinner', () => {
    render(<PreflightModal info={{ vuelos: [] }} calendario={calendario} calCargando={false} loading={false} onStart={() => {}} onCancel={() => {}} />)
    expect(screen.queryByTestId('pf-cal-cargando')).toBeNull()
    expect(screen.getByTestId('ri-calendario')).toBeTruthy()
  })

  it('muestra estado de carga y bloquea Comenzar', () => {
    render(<PreflightModal info={null} calendario={null} loading onStart={() => {}} onCancel={() => {}} />)
    expect(screen.getByText(/Leyendo estadillo/)).toBeTruthy()
    expect(screen.getByText('Cancelar').disabled).toBe(false)
    expect(screen.getByText('Comenzar').disabled).toBe(true)
  })

  it('muestra el error del calendario (ok:false) sin pintar vacío', () => {
    render(<PreflightModal info={{ vuelos: [] }} calendario={{ ok: false, error: 'boom EXIF' }} loading={false} onStart={() => {}} onCancel={() => {}} />)
    expect(screen.getByText(/No se pudo preparar el calendario: .*boom EXIF/)).toBeTruthy()
    expect(screen.queryByTestId('ri-calendario')).toBeNull()
  })

  it('un calendario ok:false lanza un error claro (sin prefijo genérico) y no pinta vacío', () => {
    expect(() => modeloDesdeCalendario({ ok: false, error: 'La carpeta de origen no existe: /x' })).toThrow('La carpeta de origen no existe: /x')
    expect(() => modeloDesdeCalendario(null)).toThrow(/sin ok/)
  })

  it('error al leer el estadillo sigue visible', () => {
    render(<PreflightModal info={{ error: 'no existe' }} calendario={null} loading={false} onStart={() => {}} onCancel={() => {}} />)
    expect(screen.getByText(/No se pudo leer el estadillo: no existe/)).toBeTruthy()
  })
})
