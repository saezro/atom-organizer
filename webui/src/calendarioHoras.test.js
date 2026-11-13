import { test, expect, describe, it } from 'vitest';
import { readFileSync } from 'node:fs';
const assert = { equal: (a, b) => expect(a).toBe(b), deepEqual: (a, b) => expect(a).toEqual(b), ok: (v, m) => expect(v, m).toBeTruthy(), throws: (f) => expect(f).toThrow() };
import {
  parsearHoraCamara, lunesDe, diasDelRango, semanasDe, segmentosDeVuelo, layoutDia, rangoHoras,
  diasVisibles, iconoGiro, textoParidad, formatearGb, formatearHora, etiquetaVuelo, colorDePb,
  tooltipVotos, contarVuelosDia, altoHoraPx, minDuracionParaAlto, anchosColumnas, etiquetaCompacta, formatoBloque,
  modeloDesdeCalendario, textoHorasBloque, motivosAviso, tooltipVuelo, anchoBloquePx, ANCHO_MIN_BLOQUE_PX, PALETA_PB, ANCHO_MIN_DIA_PX, ALTO_HORA_PX, MIN_DURACION_MIN,
} from './calendarioHoras.js';

const vuelo = (ini, fin, extra = {}) => ({ pb: '24', vuelo: '1', sufijo: '', inicio_exif: ini, fin_exif: fin, ...extra });

test('parsearHoraCamara', () => {
  assert.deepEqual(parsearHoraCamara('2026-11-08T10:48:00'), { fecha: '2026-11-08', min: 648 });
  assert.throws(() => parsearHoraCamara('2026-11-08T10:48:00Z'));
});
test('lunesDe', () => {
  assert.equal(lunesDe('2026-11-08'), '2026-11-02');
  assert.equal(lunesDe('2026-11-02'), '2026-11-02');
});
test('diasDelRango', () => {
  const a = diasDelRango([vuelo('2026-11-04T10:00:00', '2026-11-04T11:00:00'), vuelo('2026-11-08T10:00:00', '2026-11-08T11:00:00')]);
  assert.equal(a.length, 7);
  assert.equal(a[0], '2026-11-02');
  const b = diasDelRango([vuelo('2026-11-08T10:00:00', '2026-11-08T11:00:00'), vuelo('2026-11-09T10:00:00', '2026-11-09T11:00:00')]);
  assert.equal(b.length, 14);
  assert.deepEqual(semanasDe(b), ['2026-11-02', '2026-11-09']);
  assert.equal(diasDelRango([vuelo(null, null)]).length, 0);
});
test('segmentos: cruce de medianoche', () => {
  const s = segmentosDeVuelo(vuelo('2026-11-04T23:40:00', '2026-11-05T00:20:00'));
  assert.equal(s.length, 2);
  assert.equal(s[0].clave, s[1].clave);
  assert.deepEqual([s[0].parte, s[0].iniMin, s[0].finMin], ['inicio', 1420, 1440]);
  assert.deepEqual([s[1].parte, s[1].iniMin, s[1].finMin, s[1].fecha], ['fin', 0, 20, '2026-11-05']);
  assert.equal(segmentosDeVuelo(vuelo('2026-11-04T10:00:00', '2026-11-04T11:00:00'))[0].parte, 'unico');
  assert.equal(segmentosDeVuelo(vuelo(null, null)).length, 0);
  assert.equal(segmentosDeVuelo(vuelo('2026-11-04T23:00:00', '2026-11-06T01:00:00')).length, 3);
});
test('layoutDia', () => {
  const seg = (i, f) => ({ clave: `${i}`, fecha: 'x', iniMin: i, finMin: f, parte: 'unico' });
  const sol = layoutDia([seg(600, 660), seg(620, 700)], { minDuracionMin: 23 });
  assert.deepEqual(sol.map((s) => [s.col, s.nCols]), [[0, 2], [1, 2]]);
  const solo = layoutDia([seg(600, 660)], { minDuracionMin: 23 });
  assert.equal(solo[0].nCols, 1);
  const cortos = layoutDia([seg(600, 605), seg(615, 620)], { minDuracionMin: 23 });
  assert.deepEqual(cortos.map((s) => s.nCols), [2, 2]);
  const lejos = layoutDia([seg(600, 605), seg(700, 705)], { minDuracionMin: 23 });
  assert.deepEqual(lejos.map((s) => s.nCols), [1, 1]);
});
test('rangoHoras', () => {
  assert.deepEqual(rangoHoras([{ iniMin: 648, finMin: 671 }, { iniMin: 700, finMin: 1000 }]), { desde: 10, hasta: 17 });
});
test('diasVisibles y constantes', () => {
  assert.equal(diasVisibles(100, 168), 1);
  assert.equal(diasVisibles(1200, 168), 7);
  assert.equal(ANCHO_MIN_DIA_PX, 168); assert.equal(ALTO_HORA_PX, 128); assert.equal(MIN_DURACION_MIN, 19);
});
test('iconoGiro', () => {
  assert.equal(iconoGiro(90), 'cw'); assert.equal(iconoGiro(270), 'ccw'); assert.equal(iconoGiro(0), null);
  assert.throws(() => iconoGiro(undefined)); assert.throws(() => iconoGiro(180));
});
test('textoParidad', () => {
  assert.deepEqual(textoParidad({ n_rgb: 1090, n_termica: 1090, n_pares: 1090 }), { texto: '1090' });
  assert.deepEqual(textoParidad({ n_rgb: 1090, n_termica: 1088, n_pares: 1088 }), { aviso: '⚠ 1090 RGB / 1088 T' });
  assert.throws(() => textoParidad({ n_rgb: 1 }));
});
test('formatos', () => {
  assert.equal(formatearGb(8_310_000_000), '8,31 GB');
  assert.equal(formatearGb(null), '—');
  assert.equal(formatearHora(648), '10:48');
  assert.equal(etiquetaVuelo({ pb: '24', vuelo: '1', sufijo: '' }), 'PB24 · V1');
  assert.equal(tooltipVotos({ n_90: 5, n_270: 1, n_0: 0, pct_ganadora: 83.3 }), '90º: 5 · 270º: 1 · sin girar: 0 · ganadora 83.3 %');
});
test('colorDePb estable y paleta', () => {
  assert.equal(PALETA_PB.length, 12);
  assert.ok(!PALETA_PB.some((c) => c.toLowerCase() === '#ee763c'));
  assert.equal(colorDePb('24'), colorDePb('24'));
  assert.ok(PALETA_PB.includes(colorDePb('24')));
});
test('guarda: sin new Date( sobre cadenas EXIF', () => {
  const src = readFileSync(new URL('./src/calendarioHoras.js', 'file://' + process.cwd() + '/'), 'utf8');
  assert.ok(!/new Date\(\s*(v\.|s\b|f\b|["'`])/.test(src.replace(/new Date\(ms\)/g, '')));
  assert.ok(!/Date\.parse|toISOString/.test(src));
});

test('contarVuelosDia cuenta vuelos distintos, también por medio/fin', () => {
  const segs = segmentosDeVuelo(vuelo('2026-11-08T20:00:00', '2026-11-10T08:00:00'));
  const medio = segs.filter((s) => s.fecha === '2026-11-09');
  assert.equal(medio[0].parte, 'medio');
  assert.equal(contarVuelosDia(medio), 1);
  assert.equal(contarVuelosDia([...medio, ...segmentosDeVuelo(vuelo('2026-11-09T01:00:00', '2026-11-09T02:00:00', { vuelo: '2' }))]), 2);
  assert.equal(contarVuelosDia([]), 0);
});

test('altoHoraPx sube con densidad y respeta tope', () => {
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ iniMin: 600 + i }));
  assert.equal(altoHoraPx([mk(2)]), 128);
  assert.equal(altoHoraPx([mk(6)]), 256 > 224 ? 224 : 256);
  assert.equal(altoHoraPx([mk(4)]), Math.round(128 * 4 / 3));
  assert.equal(minDuracionParaAlto(128), 19);
  assert.equal(minDuracionParaAlto(224), 11);
});
test('layoutDia: clúster independiente y carriles sobre extensión visual', () => {
  const seg = (i, f) => ({ clave: `${i}`, fecha: 'x', iniMin: i, finMin: f, parte: 'unico' });
  // A(600-602) y B(610-612) solapan visualmente con min 19; C(700) queda solo a ancho completo.
  const r = layoutDia([seg(600, 602), seg(610, 612), seg(700, 702)], { minDuracionMin: 19 });
  assert.deepEqual(r.map((s) => s.nCols), [2, 2, 1]);
});
test('anchosColumnas: vacíos estrechos, llenos reparten', () => {
  const a = anchosColumnas([0, 3, 0, 2, 0, 0, 1], 1000, { anchoVacioPx: 56, anchoMinPx: 168 });
  assert.deepEqual(a.filter((x) => x === 56).length, 4);
  assert.equal(Math.round(a.reduce((x, y) => x + y, 0)), 1000);
  const b = anchosColumnas([1, 1, 1, 1, 1, 1, 1], 500, { anchoVacioPx: 56, anchoMinPx: 168 });
  assert.ok(b.every((x) => x === 168));
  assert.deepEqual(anchosColumnas([0, 0, 0, 0, 0, 0, 0, 2], 800).slice(7), [800 > 168 ? 800 - 0 : 168]);
});
test('etiqueta compacta y formato por tamaño', () => {
  assert.equal(etiquetaCompacta({ pb: '25', vuelo: '2', sufijo: '' }), 'PB25·V2');
  assert.equal(etiquetaCompacta({ pb: '25', vuelo: '2', sufijo: 'b' }), 'PB25·V2b');
  assert.deepEqual(formatoBloque(200, 60), { gbEnTitulo: true, linea2: 'completa', iconoPequeno: false });
  assert.deepEqual(formatoBloque(200, 20).linea2, 'rango');
  assert.deepEqual(formatoBloque(70, 60), { gbEnTitulo: false, linea2: 'inicio', iconoPequeno: true });
  assert.equal(textoHorasBloque('inicio', 719, 721), '11:59');
  assert.equal(textoHorasBloque('rango', 719, 721), '11:59-12:01');
});
test('tooltipVuelo incluye todo', () => {
  const t = tooltipVuelo({ pb: '25', vuelo: '2', sufijo: '', n_imagenes: 100, bytes: 2e9, n_90: 1, n_270: 0, n_0: 0, pct_ganadora: 100 },
    { ini: 719, fin: 721, giro: 'cw', paridad: '100' });
  for (const x of ['PB25·V2', '11:59-12:01', '100 imágenes', '2,00 GB', '90º horario']) assert.ok(t.includes(x), x);
});

test('anchoBloquePx: con muchos carriles nunca baja del minimo', () => {
  assert.equal(anchoBloquePx(200, 2), 97);
  for (const n of [10, 12, 40]) {
    assert.ok(anchoBloquePx(60, n) >= ANCHO_MIN_BLOQUE_PX);
  }
  assert.equal(anchoBloquePx(30, 10), ANCHO_MIN_BLOQUE_PX);
});

// --- Añadidos de la revisión del índice ---
import fixture from '../e2e/harness/fixtures/propuesta-revision.json';
import { cubetasPorHora, marcasLibres, textoConfianza, modeloDesdePropuesta } from './calendarioHoras.js';

test('marcasLibres: hora de cámara sin zona -> minuto del día, sin Date', () => {
  const libres = [
    { clave: 'a', ts: '2026-05-04T10:12:03', grupo_id: 'g' },
    { clave: 'b', ts: '2026-05-05T10:12:03', grupo_id: 'g' },
    { clave: 'c', ts: null, grupo_id: 'g' },
  ];
  assert.deepEqual(marcasLibres(libres, '2026-05-04'), [{ min: 612, clave: 'a', grupo_id: 'g' }]);
});
test('cubetasPorHora: sin horas reales no inventa nada', () => {
  assert.deepEqual(cubetasPorHora({ horas: null }), []);
  assert.deepEqual(cubetasPorHora({ horas: [{ hora: 9, n_rgb: 1, n_t: 2, n_otro: 0 }] }).length, 1);
});
test('textoConfianza', () => {
  assert.equal(textoConfianza('alta'), 'confianza alta');
  assert.throws(() => textoConfianza('segura'));
});
test('modeloDesdePropuesta(fixture)', () => {
  const m = modeloDesdePropuesta(fixture);
  assert.equal(m.vuelos.length, 6);
  assert.equal(m.recomendaciones.length, 3);
  assert.equal(m.recomendaciones.filter((r) => r.aplicada).length, 1);
  assert.ok(m.libres.length >= 40);
  assert.equal(m.espera.estado, 'esperando');
});

import { tramosLibres, HUECO_CORTE_TRAMO_S } from './calendarioHoras.js';
test('tramosLibres: consecutivas juntas, hueco o grupo distinto cortan, sin hora aparte', () => {
  const f = (c, ts, g = 'g') => ({ clave: c, nombre: c, tipo: 'RGB', ts, grupo_id: g, motivo: 'x' });
  const t = tramosLibres([
    f('a', '2026-05-04T10:00:00'), f('b', '2026-05-04T10:00:10'),
    f('c', `2026-05-04T10:0${Math.floor((10 + HUECO_CORTE_TRAMO_S + 1) / 60)}:${String((10 + HUECO_CORTE_TRAMO_S + 1) % 60).padStart(2, '0')}`),
    f('d', '2026-05-04T10:00:20', 'h'), f('e', null, 'z'),
  ]);
  assert.equal(t.length, 4);
  assert.deepEqual(t.find((x) => x.claves.includes('a')).claves, ['a', 'b']);
  assert.equal(t.find((x) => x.claves.includes('e')).fecha, null);
});

import { vueloQueContiene } from './calendarioHoras.js';
test('vueloQueContiene: tramo dentro de un vuelo se detecta; fuera no', () => {
  const v = [{ pb: '1', vuelo: '1', sufijo: '', inicio: '2026-05-04T09:00:00', fin: '2026-05-04T10:00:00' }];
  const tr = (ini, fin) => ({ fecha: '2026-05-04', iniMin: ini, finMin: fin });
  assert.ok(vueloQueContiene(tr(9 * 60 + 30, 9 * 60 + 31), v));
  assert.equal(vueloQueContiene(tr(10 * 60 + 5, 10 * 60 + 6), v), null);
  assert.equal(vueloQueContiene({ fecha: null }, v), null);
});
test('fixture: ninguna foto libre con hora cae dentro de un vuelo', () => {
  const m = modeloDesdePropuesta(fixture);
  const dentro = tramosLibres(m.libres).filter((t) => vueloQueContiene(t, m.vuelos));
  assert.equal(dentro.length, 0);
});

test('motivosAviso: RGB distinto de T, sin giro, ambos, ninguno', () => {
  assert.deepEqual(motivosAviso({ n_rgb: 10, n_t: 10, angulo: 90 }), []);
  assert.deepEqual(motivosAviso({ n_rgb: 10, n_t: 10, angulo: 0 }), []);
  assert.deepEqual(motivosAviso({ n_rgb: 10, n_t: 8, angulo: 270 }), ['RGB ≠ T']);
  assert.deepEqual(motivosAviso({ n_rgb: 10, n_t: 10, angulo: null }), ['sin giro']);
  assert.deepEqual(motivosAviso({ n_rgb: 10, n_t: 8, angulo: undefined }), ['RGB ≠ T', 'sin giro']);
});

describe('modeloDesdeCalendario', () => {
  const base = () => ({
    ok: true, espera: { activa: true, estado: 'esperando', deadline_epoch: 1000, inactividad_s: 0 },
    vuelos: [
      { fila: 1, pb: 3, vuelo: 2, sufijo: null, inicio: '2026-06-01T10:00:00', fin: '2026-06-01T10:30:00', angulo: 90,
        n_total: 5, n_giro_desconocido: 2,
        horas: [{ hora: '2026-06-01T10', n_rgb: 2, n_t: 3, n_otro: 0 }] },
      { fila: 2, pb: 4, vuelo: 1, inicio: null, fin: null, n_total: 7, horas: [] },
    ],
    libres: [], horas_sin_fila: [{ pb: 5, vuelo: 1, horas: [] }],
    recomendaciones: [{ id: 'g:1:vecino', grupo_id: 'g', n_fotos: 2, fila: 1, pb: 3, vuelo: 2, motivo: 'vecino', distancia_s: 10, confianza: 'alta', explicacion: 'x' }],
  })

  it('mapea vuelos, recomendaciones y espera (epoch s -> ms)', () => {
    const m = modeloDesdeCalendario(base(), { esperaMin: 30 })
    expect(m.vuelos).toHaveLength(1)
    expect(m.vuelos[0]).toMatchObject({ pb: 3, vuelo: 2, sufijo: '', n_rgb: 2, n_t: 3, fila: 1 })
    expect(m.recomendaciones[0]).toMatchObject({ id: 'g:1:vecino', grupo_id: 'g', aplicada: false })
    expect(m.espera).toEqual({ estado: 'esperando', deadline_epoch: 1_000_000, espera_min: 30 })
  })

  it('generales viaja del calendario al Modelo', () => {
    const j = base()
    j.vuelos[0].generales = true
    expect(modeloDesdeCalendario(j).vuelos[0].generales).toBe(true)
    delete j.vuelos[0].generales
    expect(modeloDesdeCalendario(j).vuelos[0].generales).toBe(false)
  })

  it('espera_min: el del evento si lo hay; si no, la espera_s real del backend (tras recuperar la pausa)', () => {
    const j = base()
    j.espera = { ...j.espera, espera_s: 600 }
    expect(modeloDesdeCalendario(j, { esperaMin: 5 }).espera.espera_min).toBe(5)
    expect(modeloDesdeCalendario(j, { esperaMin: null }).espera.espera_min).toBe(10)
    delete j.espera.espera_s
    expect(modeloDesdeCalendario(j).espera.espera_min).toBeUndefined()
  })

  it('avisa de giro desconocido, filas sin hora y horas sin fila (nunca en silencio)', () => {
    const { avisos } = modeloDesdeCalendario(base())
    expect(avisos.some((a) => /2 fotos.*giro/.test(a))).toBe(true)
    expect(avisos.some((a) => /fila 2.*no se pinta/.test(a))).toBe(true)
    expect(avisos.some((a) => /PB5/.test(a))).toBe(true)
  })

  it('respuesta con error o sin listas lanza', () => {
    expect(() => modeloDesdeCalendario({ ok: false, error: 'x' })).toThrow(/x/)
    expect(() => modeloDesdeCalendario({ ok: true, vuelos: [] })).toThrow(/libres/)
  })

  it('sin pausa activa no hay espera', () => {
    const j = base(); j.espera = { activa: false, estado: null, deadline_epoch: null }
    expect(modeloDesdeCalendario(j).espera).toBeNull()
  })
})
