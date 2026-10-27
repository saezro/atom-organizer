import { useEffect, useMemo, useState } from 'react'

// Progreso por fases de la subida del resultado (modelo visual: ReportsProgress de la Suite).
// Anillo con % + mm:ss, stepper vertical (pending/running/done/skipped/error), chips y log técnico.

const nf = (n, d = 1) => Number(n).toLocaleString('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d })
const miles = (n) => Number(n || 0).toLocaleString('es-ES')

export const FASES = [
  { id: 'prechequeo', label: 'Prechequeo y destino' },
  { id: 'listando', label: 'Leyendo bucket' },
  { id: 'comprobando', label: 'Comprobando' },
  { id: 'urgentes', label: 'Urgentes' },
  { id: 'resto', label: 'Resto' },
  { id: 'verificando', label: 'Verificando' },
]

export function mmss(seg) {
  const t = Math.max(0, Math.floor(seg || 0))
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`
}

const bytesTxt = (b) => {
  if (b == null || !Number.isFinite(b)) return ''
  if (b >= 1e9) return `${nf(b / 1e9)} GB`
  if (b >= 1e6) return `${nf(b / 1e6)} MB`
  return `${Math.round(b / 1e3)} KB`
}
const etaTxt = (seg) => {
  if (seg == null || !Number.isFinite(seg) || seg < 0) return ''
  const t = Math.round(seg)
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}` : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// Índice de fase (0..5) de un evento de progreso. Usa `etapa` del backend; sin ella
// (backend antiguo) la deduce de `fase`/`comprobando`. La comprobación del grupo «resto»
// cuenta dentro de la fase Resto (la fase 3 es la del grupo urgentes).
export function indiceFase(e) {
  if (!e) return 0
  const et = e.etapa || (e.comprobando ? 'comprobando' : e.fase)
  switch (et) {
    case 'listando': return 1
    case 'comprobando': return e.fase === 'resto' ? 4 : 2
    case 'urgentes': return 3
    case 'resto': return 4
    case 'verificando': return 5
    default: return 1
  }
}

const resumenGrupo = (m, g) => {
  if (!m) return ''
  const sub = m.subidos || 0
  const om = m.omitidos || 0
  if (sub === 0 && (m.pendientes || 0) === 0) return om > 0 ? 'Todo ya estaba en el bucket' : 'Nada que subir'
  return `${miles(sub)} subidos · ${miles(om)} ya en bucket`
}

// Estado de cada fase. `fin` = null (en vivo) | 'ok' | 'cancelado' | 'error'.
export function calcularFases({ cur, vis, max, restoPrevisto, fin, ultimo, compr, resumen }) {
  const visitadas = new Set(vis)
  const actual = fin === 'ok' ? FASES.length : (fin ? max : cur)
  const m = resumen?.metricas
  return FASES.map((f, i) => {
    let estado, detalle = ''
    if (i < actual || (fin === 'ok')) {
      estado = (i === 2 || i === 4) && !visitadas.has(i) ? 'skipped' : 'done'
    } else if (i === actual) {
      estado = fin ? 'error' : 'running'
    } else {
      estado = i === 4 && !restoPrevisto ? 'skipped' : 'pending'
    }
    if (i === 4 && !restoPrevisto && estado !== 'done' && estado !== 'running') estado = 'skipped'
    if (estado === 'skipped') {
      detalle = i === 2 ? 'Nada que comprobar' : (restoPrevisto ? 'No se sube el resto en esta pasada' : 'Sin resto en esta subida')
    } else if (estado === 'running') {
      if (i === 0) detalle = 'Preparando…'
      else if (i === 1) detalle = 'Consultando objetos…'
      else if (i === 5) detalle = 'Comprobando de nuevo el bucket…'
      else if (ultimo?.comprobando) detalle = `Comprobando ${miles(ultimo.comprobando.hechos)} / ${miles(ultimo.comprobando.total)}`
      else {
        const g = ultimo?.[FASES[i].id]
        detalle = g ? `${miles(g.hechos)} / ${miles(g.total)}` : ''
      }
    } else if (estado === 'done' || estado === 'error') {
      if (i === 0) detalle = 'Destino listo'
      else if (i === 1) detalle = 'Bucket leído'
      else if (i === 2) detalle = compr ? `${miles(compr.total)} comprobados` : ''
      else if (i === 3 || i === 4) {
        const g = FASES[i].id
        detalle = m?.[g] ? resumenGrupo(m[g], g) : (ultimo?.[g] ? `${miles(ultimo[g].hechos)} / ${miles(ultimo[g].total)}` : '')
      } else if (i === 5 && resumen) {
        if (resumen.truncado) { estado = 'error'; detalle = 'Listado truncado: no verificable' }
        else if (resumen.verificado === false) { estado = 'error'; detalle = 'No se pudo verificar' }
        else if ((resumen.faltantes || 0) > 0) { estado = 'error'; detalle = `${miles(resumen.faltantes)} sin subir` }
        else detalle = 'Todo verificado'
      }
      if (estado === 'error' && fin && i === actual && !detalle) detalle = fin === 'cancelado' ? 'Cancelada' : 'Interrumpida'
      if (estado === 'error' && fin === 'cancelado' && i === actual) detalle = 'Cancelada'
    }
    return { ...FASES[i], estado, detalle }
  })
}

const COL = { running: '#EE763C', done: '#34d399', skipped: '#38bdf8', error: '#f87171', pending: 'rgba(255,255,255,0.35)' }

function Icono({ estado }) {
  const p = { viewBox: '0 0 24 24', width: 20, height: 20, fill: 'none', stroke: COL[estado], strokeWidth: 2.2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' }
  if (estado === 'done') return <svg {...p}><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.7 2.7L16 9.5" /></svg>
  if (estado === 'error') return <svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5M12 16.2v.1" /></svg>
  if (estado === 'skipped') return <svg {...p}><path d="M6 6l8 6-8 6zM18 6v12" /></svg>
  if (estado === 'running') return <svg {...p} className="espera-spinner"><path d="M12 3a9 9 0 1 1-9 9" /></svg>
  return <svg {...p}><circle cx="12" cy="12" r="8" /></svg>
}

const ETQ_ESTADO = { pending: 'Pendiente', running: 'En curso', done: 'Completada', skipped: 'Omitida', error: 'Con error' }
const R = 52
const CIRC = 2 * Math.PI * R

const trazaInicial = (inicio) => ({ cur: 0, max: 0, vis: [0], compr: null, tiempos: { 0: { ini: inicio || Date.now() } }, log: [], finTs: null })

export default function ProgresoSubida({
  ultimo, resumen, card, subiendo, error, inicio, ahora, pct = 0, est, hayResto, modo,
  nSin = 0, fallidasN = 0, conflictosN = 0,
}) {
  const [traza, setTraza] = useState(() => trazaInicial(inicio))
  const fin = subiendo ? null : resumen ? (resumen.cancelled ? 'cancelado' : resumen.ok ? 'ok' : 'error') : error ? 'error' : null

  const t0 = () => (inicio ? Math.max(0, (Date.now() - inicio) / 1000) : 0)

  useEffect(() => { setTraza(trazaInicial(inicio)) }, [inicio])

  useEffect(() => {
    if (!ultimo) return
    const idx = indiceFase(ultimo)
    const now = Date.now()
    setTraza((t) => {
      const compr = ultimo.comprobando ? { hechos: ultimo.comprobando.hechos, total: ultimo.comprobando.total } : t.compr
      if (t.cur === idx) return compr === t.compr ? t : { ...t, compr }
      const tiempos = { ...t.tiempos }
      if (tiempos[t.cur] && tiempos[t.cur].fin == null) tiempos[t.cur] = { ...tiempos[t.cur], fin: now }
      tiempos[idx] = tiempos[idx] ? { ...tiempos[idx], fin: null } : { ini: now }
      return {
        ...t, cur: idx, max: Math.max(t.max, idx), compr, tiempos,
        vis: t.vis.includes(idx) ? t.vis : [...t.vis, idx],
        log: [...t.log, `[${mmss(t0())}] ${FASES[idx].label}`],
      }
    })
  }, [ultimo])

  useEffect(() => {
    if (!fin) return
    const now = Date.now()
    setTraza((t) => {
      if (t.finTs) return t
      const tiempos = { ...t.tiempos }
      if (tiempos[t.cur] && tiempos[t.cur].fin == null) tiempos[t.cur] = { ...tiempos[t.cur], fin: now }
      const txt = fin === 'ok' ? 'FIN' : fin === 'cancelado' ? 'FIN (cancelada)' : 'FIN (con problemas)'
      return { ...t, tiempos, finTs: now, log: [...t.log, `[${mmss(t0())}] ${txt}`] }
    })
  }, [fin])

  const restoPrevisto = Boolean(hayResto) || (ultimo?.modo || resumen?.modo || modo) === 'normal'
  const fases = useMemo(
    () => calcularFases({ cur: traza.cur, vis: traza.vis, max: traza.max, restoPrevisto, fin, ultimo, compr: traza.compr, resumen }),
    [traza, restoPrevisto, fin, ultimo, resumen]
  )
  const actual = fases.find((f) => f.estado === 'running') || null

  // --- anillo y cabecera ---
  const preparando = !fin && (traza.cur <= 2)
  const ok = fin === 'ok' && nSin === 0 && !fases.some((f) => f.estado === 'error')
  const pctMostrar = fin === 'ok' ? 100 : Math.max(0, Math.min(100, pct))
  const colorAnillo = fin ? (ok ? COL.done : fin === 'cancelado' ? COL.skipped : COL.error) : COL.running
  const nuevos = card?.subidos ?? null
  let titulo
  if (!fin) titulo = actual ? actual.label : 'Subiendo'
  else if (fin === 'cancelado') titulo = 'Subida cancelada'
  else if (fin === 'error' && !resumen) titulo = 'La subida se ha interrumpido'
  else if (fin === 'error') titulo = 'Subida con problemas'
  else if (nSin > 0) titulo = 'Subida incompleta: hay ficheros sin clasificar'
  else if (!ok) titulo = 'Subida con problemas'
  else if (nuevos === 0 && fallidasN === 0) titulo = 'Todo ya estaba en el bucket'
  else titulo = 'Subida completa'

  const trans = fin
    ? (resumen?.metricas?.duracion_s ?? ((traza.finTs || Date.now()) - (inicio || traza.finTs || Date.now())) / 1000)
    : inicio ? (ahora - inicio) / 1000 : ultimo?.t

  // --- chips ---
  const enNormal = ultimo?.modo === 'normal'
  const fH = ultimo ? (ultimo.urgentes?.hechos || 0) + (enNormal ? ultimo.resto?.hechos || 0 : 0) : 0
  const fT = ultimo ? (ultimo.urgentes?.total || 0) + (enNormal ? ultimo.resto?.total || 0 : 0) : 0
  const chips = []
  const chip = (clave, etq, val, sub) => chips.push({ clave, etq, val, sub })
  if (fin && card) {
    chip('subidos', 'Subidos', miles(card.subidos), bytesTxt(card.bytes))
    const om = card.grupos.reduce((a, [, g]) => a + (g.omitidos || 0), 0)
    chip('bucket', 'Ya en bucket', miles(om))
    chip('fallidas', 'Fallidas', miles(fallidasN), null, fallidasN > 0)
    if (card.mbs != null) chip('mbs', 'Velocidad media', `${nf(card.mbs)} MB/s`)
  } else if (fin) {
    chip('fallidas', 'Fallidas', miles(fallidasN))
    if (conflictosN) chip('conflictos', 'Conflictos', miles(conflictosN))
  } else if (ultimo && !preparando) {
    chip('subidos', 'Subidos', miles(ultimo.ficheros_subidos || 0))
    chip('bucket', 'Ya en bucket', miles(Math.max(0, fH - (ultimo.ficheros_subidos || 0))))
    chip('fallidas', 'Fallidas', miles(ultimo.fallidas || 0))
    chip('datos', 'Datos', `${bytesTxt(ultimo.bytes_done)} / ${bytesTxt(ultimo.bytes_total)}`,
      ultimo.bytes_enviados != null ? `${bytesTxt(ultimo.bytes_enviados)} enviados ahora` : null)
    chip('ficheros', 'Ficheros', `${miles(fH)} / ${miles(fT)}`, ultimo.files_per_s != null ? `${nf(ultimo.files_per_s)} img/s` : null)
    chip('eta', 'Tiempo restante', ultimo.eta != null ? `~${etaTxt(ultimo.eta)}` : '—')
    chip('ahora', 'Velocidad ahora', est?.actual != null ? `${nf(est.actual)} MB/s` : '—')
    chip('media', 'Velocidad media', est?.media != null ? `${nf(est.media)} MB/s` : '—',
      ultimo.hilos_max ? `${ultimo.hilos_activos ?? 0}/${ultimo.hilos_max} hilos activos` : null)
    if (ultimo.grupo?.eta != null && ultimo.grupo.nombre) chip('grupo', `ETA ${ultimo.grupo.nombre}`, `~${etaTxt(ultimo.grupo.eta)}`)
    if (ultimo.fichero_actual) chip('actual', 'Fichero actual', ultimo.fichero_actual)
  }

  const logLineas = [
    ...traza.log,
    ...(fin && resumen ? [
      ...(resumen.fallidas || []).map((f) => `FALLIDA ${f[0]}: ${f[1]}`),
      ...(resumen.conflictos || []).map((c) => `CONFLICTO ${c[0]}: ${c[1]}`),
    ] : []),
    ...(fin === 'error' && !resumen && error ? [`ERROR: ${error}`] : []),
  ]

  return (
    <section className={`pu pu-${fin || 'vivo'}`} aria-label="Progreso de la subida por fases" data-fin={fin || ''}>
      <div className="pu-cab">
        <div className={'pu-anillo' + (actual?.id === 'verificando' ? ' pu-anillo-pulso' : '')}>
          <svg viewBox="0 0 120 120" width="120" height="120" role="progressbar" aria-label="Progreso de la subida"
               aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pctMostrar)}>
            <circle cx="60" cy="60" r={R} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="8" />
            <circle cx="60" cy="60" r={R} fill="none" stroke={colorAnillo} strokeWidth="8" strokeLinecap="round"
                    strokeDasharray={`${CIRC * (preparando ? 0 : pctMostrar / 100)} ${CIRC}`} transform="rotate(-90 60 60)"
                    style={{ transition: 'stroke-dasharray 0.4s ease' }} />
          </svg>
          <div className="pu-anillo-txt">
            <span className="pu-pct">{preparando ? '…' : `${Math.floor(pctMostrar)} %`}</span>
            <span className="pu-tiempo">{mmss(trans)}</span>
          </div>
        </div>
        <div className="pu-titulos">
          <strong className={`pu-titulo${fin ? (ok ? ' pu-titulo-ok' : fin === 'cancelado' ? ' pu-titulo-skipped' : ' pu-titulo-error') : ''}`} aria-live="polite">{titulo}</strong>
          {!fin && ultimo && <span className="pu-sub">{textoSub(ultimo)}</span>}
          {fin && error && <span className="pu-sub pu-sub-error">{error}</span>}
        </div>
      </div>

      <ol className="pu-pasos" aria-label="Fases de la subida">
        {fases.map((f, i) => {
          const tm = traza.tiempos[i]
          const seg = tm && tm.ini ? ((tm.fin ?? (f.estado === 'running' ? ahora || Date.now() : tm.ini)) - tm.ini) / 1000 : null
          return (
            <li key={f.id} className={`pu-paso pu-paso-${f.estado}`} data-fase={f.id} data-estado={f.estado}>
              <span className="pu-paso-ico"><Icono estado={f.estado} /></span>
              <span className="pu-paso-cuerpo">
                <span className="pu-paso-nombre">{f.label}<span className="sr-only"> ({ETQ_ESTADO[f.estado]})</span></span>
                {f.detalle && <span className="pu-paso-detalle">{f.detalle}</span>}
              </span>
              {seg != null && f.estado !== 'skipped' && f.estado !== 'pending' && <span className="pu-paso-t">{mmss(seg)}</span>}
            </li>
          )
        })}
      </ol>

      {chips.length > 0 && (
        <div className="pu-chips">
          {chips.map((c) => (
            <span key={c.clave} className={`pu-chip pu-chip-${c.clave}`}>
              <span className="pu-chip-etq">{c.etq}</span>
              <span className="pu-chip-val">{c.val}</span>
              {c.sub && <span className="pu-chip-sub">{c.sub}</span>}
            </span>
          ))}
        </div>
      )}

      {logLineas.length > 0 && (
        <details className="pu-log">
          <summary>Ver log técnico</summary>
          <pre>{logLineas.join('\n')}</pre>
        </details>
      )}
    </section>
  )
}

function textoSub(e) {
  if (e.fase === 'verificando') return 'Verificando en el bucket…'
  if (e.fase === 'listando') return 'Leyendo el bucket…'
  if (e.comprobando) return `Comprobando ficheros ${e.comprobando.hechos}/${e.comprobando.total}…`
  if (!e.urgentes || !e.resto) return ''
  return `urgentes ${e.urgentes.hechos}/${e.urgentes.total} · resto ${e.resto.hechos}/${e.resto.total}`
}
