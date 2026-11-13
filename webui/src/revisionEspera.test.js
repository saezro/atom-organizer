import { beforeEach, describe, expect, it } from 'vitest'
import { paramsConEspera, esperaGuardada, CLAVE_ESPERA_REVISION, guardarEsperaMin, leerEsperaMin, validarEsperaMin } from './revisionEspera'

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

  describe('paramsConEspera', () => {
    const base = { origen: '/x' }
    it('flag apagado (disponible != true): params intactos aunque haya valor guardado', () => {
      guardarEsperaMin('45')
      for (const d of [false, undefined, null]) {
        const r = paramsConEspera('split_images', base, d)
        expect(r.params).toBe(base)
        expect(r.aviso).toBeNull()
      }
    })
    it('otra tarea: intactos', () => {
      guardarEsperaMin('45')
      expect(paramsConEspera('rename_images', base, true).params).toBe(base)
    })
    it('disponible sin valor guardado: intactos', () => {
      expect(paramsConEspera('split_images', base, true).params).toBe(base)
    })
    it('disponible con valor guardado: viaja espera_min', () => {
      guardarEsperaMin('45')
      expect(paramsConEspera('split_images', base, true).params).toEqual({ origen: '/x', revision_indice: { espera_min: 45 } })
    })
    it('valor corrupto: aviso y params intactos', () => {
      localStorage.setItem(CLAVE_ESPERA_REVISION, 'xx')
      const r = paramsConEspera('split_images', base, true)
      expect(r.params).toBe(base)
      expect(r.aviso).toMatch(/ilegible/)
    })
  })
})
