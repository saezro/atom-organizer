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
})
