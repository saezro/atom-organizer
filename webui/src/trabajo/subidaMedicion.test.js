import { describe, it, expect } from 'vitest'
import { añadirMuestra, diagnosticarCuello, estadisticasSerie, saludSubida, ordenarPB } from './subidaMedicion'

const med = (o = {}) => ({ disco_s: 0, red_s: 0, sesion_s: 0, espera_s: 0, token_s: 0, reintentos: { '429': 0, '5xx': 0, auth: 0, http: 0, red: 0 }, ...o })
const ev = (t, bytes, extra = {}) => ({ t, bytes_enviados: bytes, fase: 'urgentes', medidas: med(), hilos_activos: 8, hilos_max: 16, ...extra })

describe('añadirMuestra', () => {
  it('ignora eventos de un backend sin medición (sin t/bytes_enviados)', () => {
    expect(añadirMuestra([], { fase: 'urgentes', mbps: 8 })).toEqual([])
  })

  it('calcula MB/s con la ventana de 5 s, no la media global', () => {
    let s = []
    s = añadirMuestra(s, ev(0, 0))
    s = añadirMuestra(s, ev(10, 100e6))   // 10 MB/s
    s = añadirMuestra(s, ev(20, 100e6))   // parado 10 s
    expect(s[1].mbs).toBeCloseTo(10, 1)
    expect(s[2].mbs).toBeCloseTo(0, 1)    // la media global sería 5 MB/s; "ahora" es 0
  })

  it('mientras lista o comprueba no hay velocidad (null) y el hueco corta la línea', () => {
    let s = añadirMuestra([], ev(0, 0, { fase: 'listando' }))
    s = añadirMuestra(s, ev(1, 0, { comprobando: { hechos: 1, total: 9 } }))
    expect(s.map((m) => m.mbs)).toEqual([null, null])
  })

  it('marca reintentos nuevos por causa y pausas largas', () => {
    let s = añadirMuestra([], ev(0, 0))
    s = añadirMuestra(s, ev(1, 1e6, { medidas: med({ reintentos: { '429': 2, '5xx': 0, auth: 0, http: 0, red: 1 } }) }))
    expect(s[1].marcas).toEqual(expect.arrayContaining([{ tipo: '429', n: 2 }, { tipo: 'red', n: 1 }]))
    s = añadirMuestra(s, ev(2, 2e6, { medidas: med({ reintentos: { '429': 2, '5xx': 0, auth: 0, http: 0, red: 1 }, espera_s: 3.5 }) }))
    expect(s[2].marcas).toHaveLength(1)
    expect(s[2].marcas[0]).toMatchObject({ tipo: 'pausa' })
  })

  it('una segunda pasada (t y bytes reinician) se encadena a la primera', () => {
    let s = añadirMuestra([], ev(0, 0))
    s = añadirMuestra(s, ev(100, 500e6))
    s = añadirMuestra(s, ev(1, 10e6))     // empieza «normal»: t y bytes vuelven a ~0
    const u = s[s.length - 1]
    expect(u.t).toBe(101)
    expect(u.bytes).toBe(510e6)
  })

  it('al pasar del tope se aclara sin perder las marcas', () => {
    let s = []
    for (let i = 0; i < 700; i += 1) {
      s = añadirMuestra(s, ev(i, i * 1e6, i === 3 ? { medidas: med({ reintentos: { '429': 1, '5xx': 0, auth: 0, http: 0, red: 0 } }) } : {}))
    }
    expect(s.length).toBeLessThanOrEqual(600)
    expect(s.flatMap((m) => m.marcas).some((m) => m.tipo === '429')).toBe(true)
  })
})

describe('estadisticasSerie', () => {
  it('media desde el primer envío (no cuenta el tiempo de listar el bucket)', () => {
    let s = añadirMuestra([], ev(0, 0, { fase: 'listando' }))
    s = añadirMuestra(s, ev(20, 0, { fase: 'listando' }))
    s = añadirMuestra(s, ev(30, 100e6))
    const e = estadisticasSerie(s)
    expect(e.media).toBeCloseTo(10, 1)   // 100 MB en los 10 s tras listar, no en 30
    expect(e.bytes).toBe(100e6)
  })
})

describe('diagnosticarCuello', () => {
  const base = { bytes: 2e9, mbpsMedio: 181, ficheros: 1000, hilosMax: 16 }
  it('sin muestra suficiente no opina', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 1 }), bytes: 1e6 })
    expect(d.clave).toBe('medir')
  })
  it('sin medidas devuelve null (no inventa)', () => {
    expect(diagnosticarCuello({ ...base, medidas: null })).toBeNull()
  })
  it('red dominante: «Limitado por red (181 Mbps)»', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 90, disco_s: 6, sesion_s: 4 }) })
    expect(d.clave).toBe('red')
    expect(d.titulo).toBe('Limitado por red (181 Mbps)')
  })
  it('disco dominante', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 30, disco_s: 60, sesion_s: 2 }) })
    expect(d.clave).toBe('disco')
    expect(d.titulo).toBe('Limitado por disco')
  })
  it('esperas por 429 → reintentos del servidor, con la causa', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 50, espera_s: 40, reintentos: { '429': 30, '5xx': 2, auth: 0, http: 0, red: 1 } }) })
    expect(d.clave).toBe('reintentos')
    expect(d.titulo).toBe('Limitado por reintentos del servidor')
    expect(d.detalle).toMatch(/429/)
  })
  it('esperas por cortes sin respuesta HTTP → cortes de red', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 50, espera_s: 40, reintentos: { '429': 0, '5xx': 0, auth: 0, http: 0, red: 12 } }) })
    expect(d.clave).toBe('red-cortes')
  })
  it('ficheros pequeños: el trámite de abrir sesión manda', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 40, sesion_s: 50, disco_s: 10 }) })
    expect(d.clave).toBe('sesion')
  })
  it('reparto parejo → sin cuello claro', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 40, disco_s: 30, sesion_s: 20, espera_s: 10 }) })
    expect(d.clave).toBe('mixto')
  })
  it('los porcentajes del reparto suman ~100', () => {
    const d = diagnosticarCuello({ ...base, medidas: med({ red_s: 50, disco_s: 25, sesion_s: 25 }) })
    expect(d.partes.reduce((a, p) => a + p.pct, 0)).toBeCloseTo(100, 5)
  })
})

describe('saludSubida (semáforo)', () => {
  // Muestra con hora local `rx` (s): relativa a ahora = 1000.
  const m = (rx, bytes, extra = {}) => ({ rx, t: rx, bytes, mbs: 10, fallidas: 0, etapa: 'urgentes', ...extra })
  const AHORA = 1000

  it('VERDE: sube con normalidad', () => {
    const serie = [m(900, 0), m(950, 500e6), m(1000, 1000e6)]
    const r = saludSubida({ serie, ahora: AHORA })
    expect(r.nivel).toBe('verde')
    expect(r.titulo).toBe('Subiendo bien')
  })

  it('ÁMBAR Lento: <20 % de la media de los 2 min previos durante >30 s', () => {
    const serie = [m(880, 0, { mbs: 50 }), m(900, 1e9, { mbs: 50 }), m(920, 2e9, { mbs: 50 }),
      m(950, 2.01e9, { mbs: 3 }), m(975, 2.02e9, { mbs: 3 }), m(1000, 2.03e9, { mbs: 3 })]
    const r = saludSubida({ serie, ahora: AHORA })
    expect(r.nivel).toBe('ambar')
    expect(r.titulo).toBe('Lento')
    expect(r.motivo).toMatch(/3 MB\/s/)
    expect(r.motivo).toMatch(/50 MB\/s/)
  })

  it('no es lento si la bajada dura menos de 30 s', () => {
    const serie = [m(900, 0, { mbs: 50 }), m(950, 2e9, { mbs: 50 }), m(985, 2.01e9, { mbs: 3 }), m(1000, 2.02e9, { mbs: 3 })]
    expect(saludSubida({ serie, ahora: AHORA }).nivel).toBe('verde')
  })

  it('ÁMBAR Reintentando: las fallidas crecen en el último minuto', () => {
    const serie = [m(900, 0), m(950, 500e6, { fallidas: 1 }), m(1000, 1000e6, { fallidas: 3 })]
    const r = saludSubida({ serie, ahora: AHORA })
    expect(r.nivel).toBe('ambar')
    expect(r.titulo).toBe('Reintentando')
    expect(r.motivo).toMatch(/2 fallidas nuevas/)
  })

  it('ROJO Parado: 0 bytes nuevos en 60 s (aunque no lleguen eventos)', () => {
    const serie = [m(900, 1e9), m(930, 1e9), m(940, 1e9)]
    const r = saludSubida({ serie, ahora: AHORA })
    expect(r.nivel).toBe('rojo')
    expect(r.titulo).toBe('Parado')
    expect(r.motivo).toMatch(/0 bytes nuevos en \d+ s/)
  })

  it('no hay «Parado» mientras lista o verifica, aunque no haya bytes', () => {
    const serie = [m(900, 1e9, { etapa: 'listando' }), m(990, 1e9, { etapa: 'listando' })]
    expect(saludSubida({ serie, ahora: AHORA }).nivel).toBe('verde')
    const v = [m(900, 1e9, { etapa: 'verificando' }), m(990, 1e9, { etapa: 'verificando' })]
    expect(saludSubida({ serie: v, ahora: AHORA }).nivel).toBe('verde')
  })

  it('ROJO por error con el texto del error; null sin datos', () => {
    const r = saludSubida({ serie: [], ahora: AHORA, error: 'Token caducado' })
    expect(r).toMatchObject({ nivel: 'rojo', titulo: 'Error', motivo: 'Token caducado' })
    expect(saludSubida({ serie: [], ahora: AHORA })).toBeNull()
  })
})

describe('ordenarPB', () => {
  it('numérico y General al final', () => {
    const o = ordenarPB([{ pb: 'PB10' }, { pb: 'General' }, { pb: 'PB2' }])
    expect(o.map((g) => g.pb)).toEqual(['PB2', 'PB10', 'General'])
  })
})

describe('añadirMuestra: ficheros y eta', () => {
  it('guarda ficheros hechos/total y eta', () => {
    const s = añadirMuestra([], ev(1, 10, { modo: 'normal', urgentes: { hechos: 2, total: 5 }, resto: { hechos: 1, total: 10 }, eta: 90 }))
    expect(s[0].ficheros).toEqual({ hechos: 3, total: 15 })
    expect(s[0].eta).toBe(90)
  })
  it('sin dato: ficheros y eta null', () => {
    const s = añadirMuestra([], ev(1, 10))
    expect(s[0].ficheros).toBeNull()
    expect(s[0].eta).toBeNull()
  })
})
