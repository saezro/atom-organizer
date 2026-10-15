import { describe, it, expect } from 'vitest'
import { textoTerminadoConProblemas } from './textoCierre.js'

describe('textoTerminadoConProblemas', () => {
  it('errores con detalle muestra los 3 primeros y cuenta el resto', () => {
    const t = textoTerminadoConProblemas({
      kind: 'errors', errors: 4,
      detalle_errores: ['Incoherente PB1_V1: T 81, meta 79', 'b', 'c', 'd'],
    })
    expect(t).toBe('✗ Terminado con 4 fallos: Incoherente PB1_V1: T 81, meta 79 | b | c (+1 más)')
  })

  it('errores sin detalle conserva el texto de siempre', () => {
    expect(textoTerminadoConProblemas({ kind: 'errors', errors: 1, detalle_errores: [] }))
      .toBe('✗ Terminado con 1 fallo')
  })

  it('avisos NO afirma SIN_ORDENAR si no hay detalle', () => {
    const t = textoTerminadoConProblemas({ kind: 'warning', warnings: 2, detalle_avisos: [] })
    expect(t).toBe('⚠ Terminado con 2 avisos')
    expect(t).not.toMatch(/SIN_ORDENAR/)
  })

  it('avisos con detalle lo muestra', () => {
    const t = textoTerminadoConProblemas({ kind: 'warning', warnings: 1, detalle_avisos: ['x tarda'] })
    expect(t).toBe('⚠ Terminado con 1 aviso: x tarda')
  })

  it('tolera campos ausentes', () => {
    expect(textoTerminadoConProblemas({ kind: 'warning' })).toBe('⚠ Terminado con avisos')
  })

  it('"+N más" usa el total real aunque el detalle venga truncado', () => {
    const t = textoTerminadoConProblemas({
      kind: 'errors', errors: 25, n_detalle_errores: 25,
      detalle_errores: Array.from({ length: 10 }, (_, i) => `e${i}`),
    })
    expect(t).toBe('✗ Terminado con 25 fallos: e0 | e1 | e2 (+22 más)')
  })

  it('avisos: cuenta y "+N más" salen del total del detalle', () => {
    const t = textoTerminadoConProblemas({
      kind: 'warning', warnings: 1, n_detalle_avisos: 12,
      detalle_avisos: Array.from({ length: 10 }, (_, i) => `w${i}`),
    })
    expect(t).toBe('⚠ Terminado con 12 avisos: w0 | w1 | w2 (+9 más)')
  })

  it('errors undefined se trata como 0', () => {
    expect(textoTerminadoConProblemas({ kind: 'errors' })).toBe('✗ Terminado con 0 fallos')
  })
})
