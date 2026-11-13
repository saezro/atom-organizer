// Portado de Atom-suite src/components/organizer/nube/propuestaCalendario.js (mismas firmas y errores).
// Añadidos al final: cubetasPorHora, marcasLibres, textoConfianza, modeloDesdePropuesta.
// Utilidades puras de layout del calendario de la propuesta (sin React).
// Hora = «hora cámara» EXIF tal cual, sin zona: se parte la cadena, nunca se interpreta como instante.
// La aritmética de fechas usa Date.UTC sobre componentes numéricos.

export const ANCHO_MIN_DIA_PX = 168;
export const ALTO_HORA_PX = 128; // base; altoHoraPx() lo sube si el día más denso lo necesita
export const ALTO_HORA_MAX_PX = 224;
export const ALTO_MIN_BLOQUE_PX = 40; // 2 líneas legibles
export const MIN_DURACION_MIN = Math.ceil((ALTO_MIN_BLOQUE_PX / ALTO_HORA_PX) * 60); // 19 min con la base
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

// Vuelos distintos presentes en un día (cualquier parte: unico/inicio/medio/fin).
export const contarVuelosDia = (segmentos) => new Set(segmentos.map((s) => s.clave)).size;

export function layoutDia(segmentos, { minDuracionMin = MIN_DURACION_MIN } = {}) {
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

export function rangoHoras(segmentos) {
  if (!segmentos.length) throw new Error('rangoHoras: sin segmentos');
  const min = Math.min(...segmentos.map((s) => s.iniMin));
  const max = Math.max(...segmentos.map((s) => s.finMin));
  return { desde: Math.floor(min / 60), hasta: Math.ceil(max / 60) };
}

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
  if (!json || json.ok !== true) throw new Error(`calendario no disponible: ${json?.error ?? 'respuesta sin ok'}`);
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
    });
  }
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
