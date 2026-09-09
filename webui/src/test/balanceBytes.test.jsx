import { describe, it, expect } from 'vitest'
import { balanceLine } from '../ProgressModal.jsx'

describe('balanceLine — resumen del balance de bytes del run', () => {
  it('vacío si no hay datos todavía', () => {
    expect(balanceLine(null)).toBe('')
    expect(balanceLine(undefined)).toBe('')
    expect(balanceLine({ entrada: 0, salida: 0, imagenes: 0 })).toBe('')
  })

  it('caso normal con GB, porcentaje e imágenes', () => {
    expect(
      balanceLine({ entrada: 31457280000, salida: 9437184000, imagenes: 2024 })
    ).toBe('Entrada 29.3 GB → salida 8.8 GB · 30 % del original (2024 imágenes)')
  })

  it('singular con una sola imagen', () => {
    expect(
      balanceLine({ entrada: 31457280000, salida: 9437184000, imagenes: 1 })
    ).toBe('Entrada 29.3 GB → salida 8.8 GB · 30 % del original (1 imagen)')
  })

  it('sin porcentaje cuando la salida es 0 o falta', () => {
    expect(balanceLine({ entrada: 31457280000, salida: 0, imagenes: 2024 }))
      .toBe('Entrada 29.3 GB (2024 imágenes)')
    expect(balanceLine({ entrada: 31457280000, imagenes: 2024 }))
      .toBe('Entrada 29.3 GB (2024 imágenes)')
  })
})
