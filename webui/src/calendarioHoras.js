// Portado de Atom-suite src/components/organizer/nube/propuestaCalendario.js (mismas firmas y errores).
// Añadidos al final: cubetasPorHora, marcasLibres, textoConfianza, modeloDesdePropuesta.
// Utilidades puras de layout del calendario de la propuesta (sin React).
// Hora = «hora cámara» EXIF tal cual, sin zona: se parte la cadena, nunca se interpreta como instante.
// La aritmética de fechas usa Date.UTC sobre componentes numéricos.

export const ANCHO_MIN_DIA_PX = 168;
export const ALTO_HORA_PX = 128; // base; altoHoraPx() lo sube si el día más denso lo necesita
export const ALTO_HORA_MAX_PX = 224;
// Alto mínimo SOLO para que un bloque corto sea visible/clicable; el alto real es la duración real.
export const ALTO_MIN_BLOQUE_PX = 7;
export const MIN_DURACION_MIN = Math.ceil((ALTO_MIN_BLOQUE_PX / ALTO_HORA_PX) * 60); // 4 min con la base
// Umbrales de contenido según el alto real del bloque (px).
export const ALTO_COMPLETO_PX = 44;
export const ALTO_COMPACTO_PX = 22;
export const modoContenido = (altoPx) => (altoPx >= ALTO_COMPLETO_PX ? 'completo' : altoPx >= ALTO_COMPACTO_PX ? 'compacto' : 'minimo');
export const ANCHO_DIA_VACIO_PX = 56;
export const GAP_CARRIL_PX = 3;
export const ANCHO_MIN_BLOQUE_PX = 6;
// Ancho útil de un bloque en su carril; nunca por debajo del mínimo (con muchos carriles saldría <= 0).
export const anchoBloquePx = (anchoCol, nCols) => Math.max(anchoCol / nCols - GAP_CARRIL_PX, ANCHO_MIN_BLOQUE_PX);

export const PALETA_PB = [
  '#38bdf8', '#a78bfa', '#34d399', '#f472b6', '#facc15', '#60a5fa',
  '#2dd4bf', '#c084fc', '#4ade80', '#fb7185', '#a3e635', '#22d3ee',
];

const RE_HORA = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$/;
const MS_DIA = 86400000;

export function parsearHoraCamara(s) {
  const m = typeof s === 'string' ? RE_HORA.exec(s) : null;
  if (!m) throw new Error(`hora cámara inválida: ${JSON.stringify(s)}`);
  return { fecha: `${m[1]}-${m[2]}-${m[3]}`, min: Number(m[4]) * 60 + Number(m[5]) };
}

const utcDeFecha = (f) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(f);
  if (!m) throw new Error(`fecha inválida: ${JSON.stringify(f)}`);
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
};
const fechaDeUtc = (ms) => {
  const d = new Date(ms);
  return `${String(d.getUTCFullYear()).padStart(4, '0')}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
};

export function sumarDias(fecha, n) {
  return fechaDeUtc(utcDeFecha(fecha) + n * MS_DIA);
}

export function lunesDe(fecha) {
  const ms = utcDeFecha(fecha);
  const dow = (new Date(ms).getUTCDay() + 6) % 7; // 0 = lunes
  return fechaDeUtc(ms - dow * MS_DIA);
}

export function diasDelRango(vuelos) {
  const fechas = [];
  for (const v of vuelos) {
    if (v.inicio_exif == null) continue;
    fechas.push(parsearHoraCamara(v.inicio_exif).fecha);
    if (v.fin_exif != null) fechas.push(parsearHoraCamara(v.fin_exif).fecha);
  }
  if (fechas.length === 0) return [];
  const lunes = fechas.map(lunesDe).sort();
  const dias = [];
  for (let l = lunes[0]; l <= lunes[lunes.length - 1]; l = sumarDias(l, 7)) {
    for (let i = 0; i < 7; i++) dias.push(sumarDias(l, i));
  }
  return dias;
}

export const semanasDe = (dias) => dias.filter((_, i) => i % 7 === 0).map(lunesDe);

export function segmentosDeVuelo(v) {
  if (v.inicio_exif == null) return [];
  if (v.fin_exif == null) throw new Error(`vuelo PB${v.pb} V${v.vuelo}: fin_exif ausente con inicio_exif`);
  const ini = parsearHoraCamara(v.inicio_exif);
  const fin = parsearHoraCamara(v.fin_exif);
  const nDias = Math.round((utcDeFecha(fin.fecha) - utcDeFecha(ini.fecha)) / MS_DIA);
  if (nDias < 0 || (nDias === 0 && fin.min < ini.min)) {
    throw new Error(`vuelo PB${v.pb} V${v.vuelo}: fin_exif anterior a inicio_exif`);
  }
  const clave = `${v.pb}|${v.vuelo}|${v.sufijo ?? ''}`;
  const out = [];
  for (let i = 0; i <= nDias; i++) {
    const primero = i === 0;
    const ultimo = i === nDias;
    out.push({
      clave,
      fecha: sumarDias(ini.fecha, i),
      iniMin: primero ? ini.min : 0,
      finMin: ultimo ? fin.min : 1440,
      parte: nDias === 0 ? 'unico' : primero ? 'inicio' : ultimo ? 'fin' : 'medio',
    });
  }
  return out;
}

// Vuelos distintos presentes en un día (cualquier parte: unico/inicio/medio/fin). Los tramos (`tramo`) no cuentan.
export const contarVuelosDia = (segmentos) => new Set(segmentos.filter((s) => !s.tramo).map((s) => s.clave)).size;

// El carril se calcula sobre el rango REAL (minDuracionMin = mínimo ínfimo de visibilidad, en minutos).
export function layoutDia(segmentos, { minDuracionMin = 0 } = {}) {
  const orden = [...segmentos].sort((a, b) => a.iniMin - b.iniMin || a.finMin - b.finMin);
  const salida = [];
  let grupo = [];
  let colsFin = [];
  let finGrupo = -Infinity;
  const cerrar = () => {
    for (const s of grupo) salida.push({ ...s, nCols: colsFin.length });
    grupo = [];
    colsFin = [];
  };
  for (const s of orden) {
    if (grupo.length && s.iniMin >= finGrupo) cerrar();
    let col = colsFin.findIndex((fin) => fin <= s.iniMin);
    if (col === -1) col = colsFin.length;
    const pintadoFin = Math.max(s.finMin, s.iniMin + minDuracionMin);
    colsFin[col] = pintadoFin;
    finGrupo = Math.max(finGrupo, pintadoFin);
    grupo.push({ ...s, col });
  }
  cerrar();
  return salida;
}

// Rango visible. Si el conjunto cabe en <= RANGO_AJUSTADO_MAX_MIN (3 h) se ajusta con ~5 min de margen
// a cada lado (múltiplos de 5), para que un rato corto llene la pantalla; si es más largo, horas enteras
// (las rayas coinciden con las etiquetas y el margen relativo ya es pequeño).
// `desde`/`hasta` (horas enteras que contienen el rango) se mantienen por compatibilidad.
export const MARGEN_RANGO_MIN = 5;
export const RANGO_AJUSTADO_MAX_MIN = 180;
export function rangoHoras(segmentos) {
  if (!segmentos.length) throw new Error('rangoHoras: sin segmentos');
  const min = Math.min(...segmentos.map((s) => s.iniMin));
  const max = Math.max(...segmentos.map((s) => s.finMin));
  let desdeMin;
  let hastaMin;
  if (max - min > RANGO_AJUSTADO_MAX_MIN) {
    desdeMin = Math.floor(min / 60) * 60;
    hastaMin = Math.ceil(max / 60) * 60;
  } else {
    desdeMin = Math.max(0, Math.floor((min - MARGEN_RANGO_MIN) / 5) * 5);
    hastaMin = Math.min(1440, Math.ceil((max + MARGEN_RANGO_MIN) / 5) * 5);
  }
  return { desde: Math.floor(desdeMin / 60), hasta: Math.ceil(hastaMin / 60), desdeMin, hastaMin };
}

// px/hora para que el rango llene el alto disponible (sin tope ALTO_HORA_MAX_PX en este caso).
// altoDispPx = alto útil del cuerpo (sin cabecera); 0/desconocido -> se queda la base.
export function altoHoraAjustado(altoBase, altoDispPx, spanMin) {
  if (!(altoDispPx > 0) || !(spanMin > 0)) return altoBase;
  return Math.max(altoBase, altoDispPx / (spanMin / 60));
}

// La etiqueta de hora va centrada sobre su raya (mitad por encima, mitad por debajo). La última cae justo en el
// borde inferior del rango, así que el cuerpo del día lleva debajo de la escala un relleno que la deja
// entera y con AIRE_ETIQUETA_PX de aire (independiente del alto del modal: el estirado solo escala la escala).
export const ALTO_ETIQUETA_HORA_PX = 12;
export const AIRE_ETIQUETA_PX = 8;
export const PAD_ABAJO_DIA_PX = ALTO_ETIQUETA_HORA_PX / 2 + AIRE_ETIQUETA_PX;

// Marcas del eje (minutos del día): cada hora; si el rango es corto (< 2 h), el paso más fino
// (5/10/15/30/60 min) con al menos ALTO_MIN_ETIQUETA_PX px entre etiquetas.
export const ALTO_MIN_ETIQUETA_PX = 28;
export const PASOS_EJE = [5, 10, 15, 30, 60];
export const PASOS_EJE_ZOOM = [1, 2, 5, 10, 15, 30, 60];
export function marcasEje(desdeMin, hastaMin, altoHora, pasos = PASOS_EJE) {
  let paso = 60;
  if (hastaMin - desdeMin < 120) {
    paso = pasos.find((p) => (p / 60) * altoHora >= ALTO_MIN_ETIQUETA_PX) ?? 60;
  }
  const out = [];
  for (let m = Math.ceil(desdeMin / paso) * paso; m <= hastaMin; m += paso) out.push(m);
  return { paso, marcas: out };
}

// Zoom por hora: rango HH:00-HH+1:00 con ±MARGEN_RANGO_MIN min de margen (acotado al día).
export function rangoZoomHora(hora) {
  if (!Number.isInteger(hora) || hora < 0 || hora > 23) throw new Error(`rangoZoomHora: hora inválida ${JSON.stringify(hora)}`);
  const desdeMin = Math.max(0, hora * 60 - MARGEN_RANGO_MIN);
  const hastaMin = Math.min(1440, (hora + 1) * 60 + MARGEN_RANGO_MIN);
  return { desde: Math.floor(desdeMin / 60), hasta: Math.ceil(hastaMin / 60), desdeMin, hastaMin };
}
// Hora anterior/siguiente acotada a 0..23.
export const horaZoomVecina = (hora, dir) => Math.min(23, Math.max(0, hora + dir));

export const diasVisibles = (anchoPx, anchoMinPx) => Math.max(1, Math.floor(anchoPx / anchoMinPx));

export function iconoGiro(angulo) {
  if (angulo === 90) return 'cw';
  if (angulo === 270) return 'ccw';
  if (angulo === 0) return null;
  throw new Error(`ángulo de giro inválido: ${JSON.stringify(angulo)}`);
}

const entero = (v, campo) => {
  if (!Number.isInteger(v)) throw new Error(`textoParidad: ${campo} ausente o no entero (${v})`);
  return v;
};
export function textoParidad(v) {
  const rgb = entero(v.n_rgb, 'n_rgb');
  const t = entero(v.n_termica, 'n_termica');
  const p = entero(v.n_pares, 'n_pares');
  if (rgb === t && t === p) return { texto: String(p) };
  return { aviso: `⚠ ${rgb} RGB / ${t} T` };
}

export function formatearGb(bytes) {
  if (bytes === null) return '—';
  if (typeof bytes !== 'number' || !Number.isFinite(bytes)) throw new Error(`formatearGb: valor no numérico (${bytes})`);
  return `${(bytes / 1e9).toFixed(2).replace('.', ',')} GB`;
}

// Hora de un bloque. Si dura < 1 min (y hay horas completas del mismo día) se muestran segundos.
export function textoRangoHora(iniStr, finStr, iniMin, finMin, conSegundos = false) {
  const m = (x) => (typeof x === 'string' ? RE_HORA.exec(x) : null);
  const a = m(iniStr);
  const b = m(finStr);
  if (a && b && a[1] + a[2] + a[3] === b[1] + b[2] + b[3]) {
    const sa = Number(a[4]) * 3600 + Number(a[5]) * 60 + Number(a[6]);
    const sb = Number(b[4]) * 3600 + Number(b[5]) * 60 + Number(b[6]);
    if (conSegundos || sb - sa < 60) return `${a[4]}:${a[5]}:${a[6]}-${b[4]}:${b[5]}:${b[6]}`;
  }
  return `${formatearHora(iniMin)}-${formatearHora(finMin)}`;
}

// Línea de fotos de un vuelo: «N fotos» si RGB y T coinciden, si no «N RGB / N T».
export function textoFotos(v) {
  const rgb = v.n_rgb;
  const t = v.n_t;
  if (!Number.isInteger(rgb) || !Number.isInteger(t)) return null;
  return rgb === t ? `${rgb} fotos` : `${rgb} RGB / ${t} T`;
}

export function formatearHora(min) {
  return `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
}

export const etiquetaVuelo = (v) => `PB${v.pb} · V${v.vuelo}${v.sufijo ? v.sufijo : ''}`;

export function colorDePb(pb) {
  let h = 5381;
  for (const ch of String(pb)) h = ((h * 33) ^ ch.charCodeAt(0)) >>> 0;
  return PALETA_PB[h % PALETA_PB.length];
}

export const tooltipVotos = (v) =>
  `90º: ${v.n_90} · 270º: ${v.n_270} · sin girar: ${v.n_0} · ganadora ${v.pct_ganadora} %`;

// px/hora: base, o más si en alguna hora de algún día empiezan muchos vuelos (>3 por hora).
// segsPorDia: Array de arrays de segmentos {iniMin}.
export function altoHoraPx(segsPorDia) {
  let max = 0;
  for (const segs of segsPorDia) {
    const porHora = new Map();
    for (const s of segs) {
      const h = Math.floor(s.iniMin / 60);
      porHora.set(h, (porHora.get(h) ?? 0) + 1);
    }
    for (const n of porHora.values()) max = Math.max(max, n);
  }
  const px = Math.round(ALTO_HORA_PX * Math.max(1, max / 3));
  return Math.min(ALTO_HORA_MAX_PX, px);
}

// Duración mínima pintada (min) para que un bloque mida al menos ALTO_MIN_BLOQUE_PX con ese px/hora.
export const minDuracionParaAlto = (altoHora) => Math.ceil((ALTO_MIN_BLOQUE_PX / altoHora) * 60);

// Anchos de columna (px) por día. Se calcula por semana (bloques de 7): los días vacíos van
// estrechos y los llenos reparten el resto, con un mínimo de anchoMinPx (si no caben, hay scroll).
export function anchosColumnas(nVuelosPorDia, anchoDisponiblePx, { anchoVacioPx = ANCHO_DIA_VACIO_PX, anchoMinPx = ANCHO_MIN_DIA_PX } = {}) {
  const out = [];
  for (let i = 0; i < nVuelosPorDia.length; i += 7) {
    const sem = nVuelosPorDia.slice(i, i + 7);
    const llenos = sem.filter((n) => n > 0).length;
    const resto = anchoDisponiblePx - (sem.length - llenos) * anchoVacioPx;
    const ancho = llenos ? Math.max(anchoMinPx, resto / llenos) : 0;
    for (const n of sem) out.push(n > 0 ? ancho : anchoVacioPx);
  }
  return out;
}

export const etiquetaCompacta = (v) => `PB${v.pb}·V${v.vuelo}${v.sufijo ? v.sufijo : ''}`;

// Qué mostrar en un bloque según su tamaño en px. PB y V siempre completos.
export function formatoBloque(anchoPx, altoPx) {
  const ancho = anchoPx >= 150;
  return {
    gbEnTitulo: anchoPx >= 130,
    linea2: ancho && altoPx >= 36 ? 'completa' : anchoPx >= 84 ? 'rango' : 'inicio',
    iconoPequeno: anchoPx < 110,
  };
}

export function textoHorasBloque(modo, iniMin, finMin) {
  return modo === 'inicio' ? formatearHora(iniMin) : `${formatearHora(iniMin)}-${formatearHora(finMin)}`;
}

export function tooltipVuelo(v, { ini, fin, giro, paridad } = {}) {
  const partes = [etiquetaCompacta(v), `${formatearHora(ini)}-${formatearHora(fin)}`];
  if (Number.isInteger(v.n_imagenes)) partes.push(`${v.n_imagenes} imágenes`);
  if (v.bytes !== undefined) partes.push(formatearGb(v.bytes));
  partes.push(giro === 'cw' ? 'gira 90º horario' : giro === 'ccw' ? 'gira 270º antihorario' : 'sin giro');
  if (paridad) partes.push(paridad);
  partes.push(tooltipVotos(v));
  return partes.join(' · ');
}

// ---------------------------------------------------------------------------
// Añadidos de la revisión del índice (sin Date: horas de cámara EXIF sin zona).

export const CONFIANZAS = ['alta', 'media', 'baja'];

export function textoConfianza(c) {
  if (!CONFIANZAS.includes(c)) throw new Error(`confianza inválida: ${JSON.stringify(c)}`);
  return `confianza ${c}`;
}

// Fotos por hora de un vuelo: {hora, n_rgb, n_t, n_otro}. Sin `horas` (vuelo de propuesta v1) -> [].
export function cubetasPorHora(vuelo) {
  return Array.isArray(vuelo.horas) ? vuelo.horas : [];
}

// Marcas de fotos libres de un día: minuto del día de cada grupo con hora de cámara.
export function marcasLibres(libres, dia) {
  const out = [];
  for (const f of libres) {
    if (f.ts == null) continue;
    const h = parsearHoraCamara(f.ts);
    if (h.fecha === dia) out.push({ min: h.min, clave: f.clave, grupo_id: f.grupo_id });
  }
  return out.sort((a, b) => a.min - b.min);
}

export function modeloDesdePropuesta(propuesta) {
  const vuelos = propuesta.vuelos
    .filter((v) => v.inicio_exif != null)
    .map((v, i) => ({
      pb: v.pb, vuelo: v.vuelo, sufijo: v.sufijo ?? '', inicio: v.inicio_exif, fin: v.fin_exif,
      angulo: v.angulo, n_rgb: v.n_rgb, n_t: v.n_termica, horas: (propuesta._arnes?.horas ?? {})[`${v.pb}|${v.vuelo}`] ?? null, fila: i + 1,
    }));
  const rev = propuesta.revision_indice ?? { aplicadas: [], sin_aplicar: [] };
  const rec = (r, aplicada) => ({
    id: r.id, grupo_id: r.grupo_id, n_fotos: r.n_fotos, fila: r.fila, pb: r.pb, vuelo: r.vuelo,
    motivo: r.motivo, distancia_s: r.distancia_s, confianza: r.confianza, explicacion: r.explicacion, aplicada,
  });
  const arnes = propuesta._arnes ?? {};
  return {
    vuelos,
    libres: arnes.libres ?? [],
    recomendaciones: [...rev.aplicadas.map((r) => rec(r, true)), ...rev.sin_aplicar.map((r) => rec(r, false))],
    espera: arnes.espera ?? null,
  };
}

// Hueco máximo (s) entre dos fotos libres consecutivas para que sigan en el mismo tramo.
// Mayor hueco, o cambio de grupo del backend, corta el tramo. (La Suite no agrupa por tiempo.)
export const HUECO_CORTE_TRAMO_S = 300;

const segundosDeDia = (ts) => {
  const m = /T(\d{2}):(\d{2}):(\d{2})$/.exec(ts);
  return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
};

// Agrupa las fotos libres en tramos: consecutivas en el tiempo (mismo día y grupo, hueco <= HUECO_CORTE_TRAMO_S).
// Las fotos sin hora forman un tramo por grupo (fecha null). Un tramo es la unidad de selección y asignación.
export function tramosLibres(libres, hueco = HUECO_CORTE_TRAMO_S) {
  const conHora = libres.filter((f) => f.ts != null).map((f) => ({ f, h: parsearHoraCamara(f.ts), s: segundosDeDia(f.ts) }));
  conHora.sort((a, b) => a.f.grupo_id.localeCompare(b.f.grupo_id) || a.f.ts.localeCompare(b.f.ts));
  const tramos = [];
  let cur = null;
  for (const x of conHora) {
    if (!cur || cur.grupo_id !== x.f.grupo_id || cur.fecha !== x.h.fecha || x.s - cur.finS > hueco) {
      cur = { grupo_id: x.f.grupo_id, fecha: x.h.fecha, iniS: x.s, finS: x.s, fotos: [] };
      tramos.push(cur);
    }
    cur.finS = x.s;
    cur.fotos.push(x.f);
  }
  const out = tramos.map((t, i) => ({
    id: `t${i}`, grupo_id: t.grupo_id, fecha: t.fecha, iniMin: Math.floor(t.iniS / 60), finMin: Math.ceil(t.finS / 60),
    ini: t.fotos[0].ts, fin: t.fotos[t.fotos.length - 1].ts, claves: t.fotos.map((f) => f.clave), n: t.fotos.length,
    motivo: t.fotos[0].motivo,
  }));
  out.sort((a, b) => a.fecha.localeCompare(b.fecha) || a.iniMin - b.iniMin);
  out.forEach((t, i) => { t.id = `t${i}`; });
  const sinHora = new Map();
  for (const f of libres) if (f.ts == null) {
    if (!sinHora.has(f.grupo_id)) sinHora.set(f.grupo_id, []);
    sinHora.get(f.grupo_id).push(f);
  }
  for (const [g, fs] of sinHora) {
    out.push({ id: `s-${g}`, grupo_id: g, fecha: null, iniMin: null, finMin: null, ini: null, fin: null, claves: fs.map((f) => f.clave), n: fs.length, motivo: fs[0].motivo });
  }
  return out;
}

// Vuelo del estadillo en cuyo rango real cae el tramo, o null. Una foto libre no debería caer dentro
// de un vuelo: si pasa con datos reales se avisa de forma visible, no se pinta como tramo normal.
export function vueloQueContiene(tramo, vuelos) {
  if (!tramo.fecha) return null;
  for (const v of vuelos) {
    for (const s of segmentosDeVuelo({ pb: v.pb, vuelo: v.vuelo, sufijo: v.sufijo, inicio_exif: v.inicio, fin_exif: v.fin })) {
      if (s.fecha === tramo.fecha && tramo.iniMin < s.finMin && tramo.finMin >= s.iniMin) return v;
    }
  }
  return null;
}

// Motivos por los que un vuelo se pinta en ámbar (mismo criterio que el color: paridad RGB/T y giro sin dato).
// Giro válido = 0, 90 o 270; cualquier otro valor (null/ausente) es «sin giro».
export function motivosAviso(v) {
  const motivos = [];
  if (v.n_rgb !== v.n_t) motivos.push('RGB ≠ T');
  if (![0, 90, 270].includes(v.angulo)) motivos.push('sin giro');
  return motivos;
}

// Calendario del backend (`revision_indice_calendario`, spec §5) -> Modelo de RevisionIndiceModal.
// `deadline_epoch` llega en SEGUNDOS (time.time()); el modal compara con `Date.now()` en ms.
// Sin fallbacks: una respuesta sin `ok` o sin las listas esperadas lanza; lo que el calendario
// no puede pintar (horas ambiguas, giro sin voto, filas sin hora) sale en `avisos`, nunca se calla.
export function modeloDesdeCalendario(json, { esperaMin = null } = {}) {
  if (!json || json.ok !== true) throw new Error(json?.error ? String(json.error) : 'calendario no disponible: respuesta sin ok');
  for (const k of ['vuelos', 'libres', 'recomendaciones']) {
    if (!Array.isArray(json[k])) throw new Error(`calendario inválido: falta la lista '${k}'`);
  }
  const avisos = [];
  const suma = (horas, campo) => horas.reduce((a, h) => a + (h[campo] ?? 0), 0);
  const vuelos = [];
  for (const v of json.vuelos) {
    const horas = Array.isArray(v.horas) ? v.horas : [];
    if (v.inicio == null || v.fin == null) {
      if ((v.n_total ?? 0) > 0) avisos.push(`La fila ${v.fila} (PB${v.pb} · V${v.vuelo}) tiene ${v.n_total} fotos pero el estadillo no da su hora: no se pinta.`);
      continue;
    }
    if ((v.n_giro_desconocido ?? 0) > 0) {
      avisos.push(`${v.n_giro_desconocido} fotos de PB${v.pb} · V${v.vuelo}${v.sufijo ?? ''} no tienen giro conocido (sin voto o fuera de 0/90/270).`);
    }
    vuelos.push({
      pb: v.pb, vuelo: v.vuelo, sufijo: v.sufijo ?? '', inicio: v.inicio, fin: v.fin, angulo: v.angulo,
      n_rgb: suma(horas, 'n_rgb'), n_t: suma(horas, 'n_t'), horas, fila: v.fila,
      n_giro_desconocido: v.n_giro_desconocido ?? 0, generales: v.generales === true,
      equipo: v.equipo ?? null, piloto: v.piloto ?? null, previo: v.previo === true,
    });
  }
  for (const a of json.avisos ?? []) avisos.push(a);
  for (const a of json.horas_sin_fila ?? []) {
    avisos.push(`Fotos de PB${a.pb} · V${a.vuelo} cuya hora no decide la fila del estadillo: no se asignan a ninguna fila.`);
  }
  const rec = (r) => ({
    id: r.id, grupo_id: r.grupo_id, n_fotos: r.n_fotos, fila: r.fila, pb: r.pb, vuelo: r.vuelo,
    motivo: r.motivo, distancia_s: r.distancia_s, confianza: r.confianza, explicacion: r.explicacion, aplicada: false,
  });
  const e = json.espera;
  const espera = e && e.activa && e.deadline_epoch != null
    ? { estado: e.estado, deadline_epoch: e.deadline_epoch * 1000, espera_min: esperaMin ?? (e.espera_s != null ? e.espera_s / 60 : undefined) }
    : null;
  return { vuelos, libres: json.libres, recomendaciones: json.recomendaciones.map(rec), espera, avisos };
}
