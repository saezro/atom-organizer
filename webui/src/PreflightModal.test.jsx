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

  it('un vuelo GENERALES no pinta icono de giro ni «?»', () => {
    const cal = { ...calendario, vuelos: [{ ...calendario.vuelos[0], pb: 'GENERALES', generales: true, angulo: null }] }
    render(<PreflightModal info={{ vuelos: [], num_vuelos: 1 }} calendario={cal} loading={false} onStart={() => {}} onCancel={() => {}} />)
    expect(screen.queryByTestId('ri-giro')).toBeNull()
    expect(screen.getByTestId('ri-vuelo').getAttribute('aria-label')).toMatch(/Generales, no gira/)
    expect(screen.getByTestId('ri-vuelo').classList.contains('ri-g-generales')).toBe(true)
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

  describe('calendario en streaming (esqueleto y parciales)', () => {
    const esqueleto = { ...calendario, avisos: [], libres: [] }

    it('el esqueleto pinta los vuelos del estadillo con «cargando fotos…» y marca lo provisional', () => {
      render(<PreflightModal info={{ vuelos: [], num_vuelos: 1 }} calendario={esqueleto} calCargando calParcial loading={false} onStart={() => {}} onCancel={() => {}} />)
      expect(screen.getByTestId('ri-calendario')).toBeTruthy()
      expect(screen.getByTestId('ri-vuelo-equipo').textContent).toBe('M3T · Ana')
      expect(screen.getByTestId('ri-vuelo-fotos').textContent).toBe('cargando fotos…')
      expect(screen.getByTestId('ri-giro').getAttribute('aria-label')).toMatch(/provisional/)
      expect(screen.getByTestId('pf-cal-leyendo')).toBeTruthy()
      expect(screen.queryByTestId('pf-cal-cargando')).toBeNull()      // sin spinner global: ya hay calendario
      expect(screen.getByText('Comenzar').disabled).toBe(false)
    })

    it('un parcial con fotos enseña el conteo con «cargando…» y los avisos como provisionales', () => {
      const horas = [{ hora: '2026-09-20T08', n_rgb: 2, n_t: 2, n_otro: 0, n_90: 4, n_270: 0, n_0: 0, n_giro_desconocido: 0 }]
      const parcial = { ...calendario, vuelos: [{ ...calendario.vuelos[0], horas, n_total: 4, angulo: 90 }] }
      render(<PreflightModal info={{ vuelos: [] }} calendario={parcial} calCargando calParcial loading={false} onStart={() => {}} onCancel={() => {}} />)
      expect(screen.getByTestId('ri-vuelo-fotos').textContent).toBe('2 fotos · cargando…')
      expect(screen.getByTestId('pf-aviso-dato').textContent).toMatch(/provisional/)
    })

    it('terminado (sin calParcial) no queda nada provisional', () => {
      const horas = [{ hora: '2026-09-20T08', n_rgb: 2, n_t: 2, n_otro: 0, n_90: 4, n_270: 0, n_0: 0, n_giro_desconocido: 0 }]
      const fin = { ...calendario, vuelos: [{ ...calendario.vuelos[0], horas, n_total: 4, angulo: 90 }] }
      render(<PreflightModal info={{ vuelos: [] }} calendario={fin} calCargando={false} calParcial={false} loading={false} onStart={() => {}} onCancel={() => {}} />)
      expect(screen.getByTestId('ri-vuelo-fotos').textContent).toBe('2 fotos')
      expect(screen.queryByTestId('pf-cal-leyendo')).toBeNull()
      expect(screen.getByTestId('pf-aviso-dato').textContent).not.toMatch(/provisional/)
    })
  })
})
