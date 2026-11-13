import { beforeEach, describe, expect, it } from 'vitest'
import { esperaGuardada, CLAVE_ESPERA_REVISION, guardarEsperaMin, leerEsperaMin, validarEsperaMin } from './revisionEspera'

describe('revisionEspera', () => {
  beforeEach(() => localStorage.clear())
  it('30 por defecto', () => expect(leerEsperaMin()).toBe(30))
  it('guarda y lee', () => { guardarEsperaMin('45'); expect(leerEsperaMin()).toBe(45) })
  it('valores inválidos lanzan', () => {
    for (const v of ['', 'abc', '0', '-3', '99999']) expect(() => validarEsperaMin(v)).toThrow(/inválida/)
  })
  it('valor guardado corrupto lanza al leer, no vuelve al defecto', () => {
    localStorage.setItem(CLAVE_ESPERA_REVISION, 'xx')
    expect(() => leerEsperaMin()).toThrow()
  })

  it('esperaGuardada: sin valor no se envía nada (params idénticos a hoy)', () => {
    expect(esperaGuardada()).toEqual({ min: null, error: null })
  })
  it('esperaGuardada: valor guardado se devuelve; corrupto da error sin lanzar', () => {
    guardarEsperaMin('12')
    expect(esperaGuardada().min).toBe(12)
    localStorage.setItem(CLAVE_ESPERA_REVISION, 'xx')
    const e = esperaGuardada()
    expect(e.min).toBeNull()
    expect(e.error).toMatch(/inválida/)
  })
})
