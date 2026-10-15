import { describe, it, expect } from 'vitest'
import { AEROTOOLS, OTROS, SPLIT_ADVANCED } from './schema.js'

const nombres = (secciones) => secciones.flatMap((s) => s.fields.map((f) => f.name))

describe('schema sin margen de asignación', () => {
  it('ya no hay campo seconds_range en ninguna sección', () => {
    for (const secciones of [AEROTOOLS, OTROS, SPLIT_ADVANCED]) {
      expect(nombres(secciones)).not.toContain('seconds_range')
    }
  })

  it('los márgenes de log/imagen-estadillo de Aerotools se conservan', () => {
    expect(nombres(AEROTOOLS)).toContain('seconds_range_image_estad')
    expect(nombres(AEROTOOLS)).toContain('seconds_range_log_estad')
  })
})
