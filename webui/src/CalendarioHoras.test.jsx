import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import CalendarioHoras, { infoGiro } from './CalendarioHoras.jsx'
import { ALTO_ETIQUETA_HORA_PX, AIRE_ETIQUETA_PX } from './calendarioHoras.js'

const vuelo = (fila, ini, fin, extra = {}) => ({
  fila, pb: String(fila), vuelo: '1', sufijo: null, inicio: ini, fin, angulo: 0, generales: false, previo: true, ...extra,
})
const tramo = { id: 't0', fecha: '2026-05-04', iniMin: 660, finMin: 661, ini: '2026-05-04T11:00:00', fin: '2026-05-04T11:00:30', n: 3, claves: ['a', 'b', 'c'] }

describe('CalendarioHoras: color por ángulo', () => {
  it('cada ángulo, sin dato y GENERALES llevan su clase de color', () => {
    const vs = [
      vuelo(1, '2026-05-04T09:00:00', '2026-05-04T09:30:00', { angulo: 0 }),
      vuelo(2, '2026-05-04T10:00:00', '2026-05-04T10:30:00', { angulo: 90 }),
      vuelo(3, '2026-05-04T11:00:00', '2026-05-04T11:30:00', { angulo: 270 }),
      vuelo(4, '2026-05-04T12:00:00', '2026-05-04T12:30:00', { angulo: null }),
      vuelo(5, '2026-05-04T13:00:00', '2026-05-04T13:30:00', { angulo: null, generales: true }),
    ]
    render(<CalendarioHoras vuelos={vs} tramos={[]} seleccion={null} onSeleccion={() => {}} />)
    const cls = screen.getAllByTestId('ri-vuelo').map((e) => [...e.classList].find((c) => c.startsWith('ri-g-')))
    expect(cls).toEqual(['ri-g-cero', 'ri-g-cw', 'ri-g-ccw', 'ri-g-falta', 'ri-g-generales'])
    expect(infoGiro(90).tipo).toBe('cw')
  })
})

describe('CalendarioHoras: tramos y eje por día', () => {
  const vs = [
    vuelo(1, '2026-05-04T09:00:00', '2026-05-04T10:00:00'),
    vuelo(2, '2026-05-05T15:00:00', '2026-05-05T16:00:00'),
  ]
  it('un tramo gris de 1 minuto lleva texto (qué es y nº de fotos)', () => {
    render(<CalendarioHoras vuelos={vs} tramos={[tramo]} seleccion={null} onSeleccion={() => {}} />)
    const b = screen.getByTestId('ri-tramo-bloque')
    expect(b.textContent).toMatch(/Sin colocar/)
    expect(b.textContent).toMatch(/3 fotos/)
  })
  it('el eje de cada día solo pinta sus horas, no 0-24', () => {
    render(<CalendarioHoras vuelos={vs} tramos={[tramo]} seleccion={null} onSeleccion={() => {}} />)
    const horas = (f) => within(screen.getByTestId(`ri-dia-${f}`)).queryAllByTestId('ri-hora').map((e) => Number(e.textContent.slice(0, 2)))
    const d1 = horas('2026-05-04')
    const d2 = horas('2026-05-05')
    expect(d1.length).toBeGreaterThan(0)
    expect(Math.min(...d1)).toBeGreaterThanOrEqual(9)
    expect(Math.max(...d1)).toBeLessThanOrEqual(11)
    expect(Math.min(...d2)).toBeGreaterThanOrEqual(15)
    expect(Math.max(...d2)).toBeLessThanOrEqual(16)
  })
  it('la última etiqueta de hora queda entera con >= 8 px de aire bajo ella', () => {
    render(<CalendarioHoras vuelos={vs} tramos={[tramo]} seleccion={null} onSeleccion={() => {}} />)
    for (const f of ['2026-05-04', '2026-05-05']) {
      const cuerpo = within(screen.getByTestId(`ri-dia-${f}`)).getAllByTestId('ri-hora')[0].parentElement
      const etiquetas = within(cuerpo).getAllByTestId('ri-hora')
      const alto = parseFloat(cuerpo.style.height)
      const ultima = Math.max(...etiquetas.map((e) => parseFloat(e.style.top)))
      // la etiqueta va centrada sobre su raya: su borde inferior es top + alto/2
      expect(ultima + ALTO_ETIQUETA_HORA_PX / 2).toBeLessThanOrEqual(alto - AIRE_ETIQUETA_PX)
    }
  })
})
