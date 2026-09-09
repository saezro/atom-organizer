import { describe, it, expect } from 'vitest'
import { rotLine } from '../ProgressModal.jsx'

describe('rotLine — resumen de rotación del run', () => {
  it('dice "sin giro" explícito cuando no se giró ninguna', () => {
    expect(rotLine({ rot270: 0, rot90: 0, rot_none: 2024 }))
      .toBe('sin giro · 2024 imágenes tal cual')
  })

  it('singular con una sola imagen', () => {
    expect(rotLine({ rot270: 0, rot90: 0, rot_none: 1 }))
      .toBe('sin giro · 1 imagen tal cual')
  })

  it('desglosa cuando hay mezcla', () => {
    expect(rotLine({ rot270: 1094, rot90: 0, rot_none: 930 }))
      .toBe('1094 giradas 270° · 930 sin girar')
  })

  it('vacío si no hay datos todavía', () => {
    expect(rotLine(null)).toBe('')
    expect(rotLine({ rot270: 0, rot90: 0, rot_none: 0 })).toBe('')
  })
})
