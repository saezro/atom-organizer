import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import GraficaSubida, { textoEtaLegible } from './GraficaSubida'
import { añadirMuestra } from './subidaMedicion'

const ev = (t, b, x = {}) => ({ t, bytes_enviados: b, fase: 'urgentes', hilos_activos: 6, hilos_max: 12, modo: 'urgentes', urgentes: { hechos: 4, total: 9 }, ...x })

describe('GraficaSubida leyenda', () => {
  it('muestra hilos, ficheros, ETA y fallidas en rojo', () => {
    let s = añadirMuestra([], ev(0, 0))
    s = añadirMuestra(s, ev(10, 1e8, { eta: 125, fallidas: 2 }))
    render(<GraficaSubida serie={s} />)
    expect(screen.getByTestId('leyenda-hilos').textContent).toContain('6/12')
    expect(screen.getByTestId('leyenda-ficheros').textContent).toContain('4/9')
    expect(screen.getByTestId('leyenda-eta').textContent).toContain('2 min 05 s')
    expect(screen.getByTestId('leyenda-fallidas').style.color).not.toBe('')
  })
  it('sin ETA muestra —', () => {
    let s = añadirMuestra([], ev(0, 0))
    s = añadirMuestra(s, ev(10, 1e8))
    render(<GraficaSubida serie={s} />)
    expect(screen.getByTestId('leyenda-eta').textContent).toContain('—')
    expect(screen.getByTestId('leyenda-fallidas').style.color).toBe('')
  })
  it('textoEtaLegible', () => {
    expect(textoEtaLegible(null)).toBe('—')
    expect(textoEtaLegible(30)).toBe('30 s')
    expect(textoEtaLegible(3900)).toBe('1 h 05 min')
  })
})
