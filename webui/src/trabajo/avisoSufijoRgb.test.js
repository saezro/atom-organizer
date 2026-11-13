import { describe, it, expect } from 'vitest'
import { avisoSufijoRgb, normToken } from './avisoSufijoRgb'

const det = { ok: true, thermal: '_T', tokens: { _T: 10, _W: 10 } }

describe('avisoSufijoRgb', () => {
  it('normaliza token', () => {
    expect(normToken('z')).toBe('_Z')
    expect(normToken(' _z ')).toBe('_Z')
    expect(normToken('')).toBe('')
  })
  it('avisa si el sufijo no existe y lista lo que sí hay', () => {
    expect(avisoSufijoRgb('_Z', det)).toBe('Esta tanda no tiene imágenes «_Z»: se procesarán como RGB las «_W» (10)')
  })
  it('sin tokens utilizables, mensaje genérico', () => {
    expect(avisoSufijoRgb('z', { ok: true, thermal: '_T', tokens: { _T: 3 } })).toBe('Esta tanda no tiene imágenes «_Z»; el resto va a RGB')
  })
  it('no avisa si existe, está vacío o no hay detección', () => {
    expect(avisoSufijoRgb('_W', det)).toBe('')
    expect(avisoSufijoRgb('', det)).toBe('')
    expect(avisoSufijoRgb('_Z', null)).toBe('')
    expect(avisoSufijoRgb('_Z', { ok: false })).toBe('')
  })
})
