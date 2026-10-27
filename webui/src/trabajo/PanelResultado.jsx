import { useEffect, useState } from 'react'
import { api, onResultado } from '../bridge'

// mm:ss (o h:mm si pasa de la hora); '' si aún no hay estimación.
export function textoEta(seg) {
  if (seg == null || !Number.isFinite(seg) || seg < 0) return ''
  const t = Math.round(seg)
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = t % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}`
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// `mbps` del backend son megabITS/s; al operario se le enseñan MB/s (megabytes).
export function textoVelocidad(e) {
  if (!e || e.mbps == null || e.fase === 'listando' || e.comprobando) return ''
  const partes = [`${(e.mbps / 8).toFixed(1)} MB/s`]
  if (e.files_per_s != null) partes.push(`${e.files_per_s.toFixed(1)} img/s`)
  const eta = textoEta(e.eta)
  if (eta) partes.push(`ETA ~${eta}`)
  return partes.join(' · ')
}

export function textoProgreso(e) {
  if (e && e.fase === 'listando') return 'Leyendo el bucket…'
  if (e && e.comprobando) return `Comprobando ficheros ${e.comprobando.hechos}/${e.comprobando.total}…`
  if (!e || !e.urgentes || !e.resto) return ''
  return `urgentes ${e.urgentes.hechos}/${e.urgentes.total} · resto ${e.resto.hechos}/${e.resto.total}`
}

// Subida del resultado organizado al bucket de plantas. Se dispara sola al terminar
// cada run (urgencia y, después, normal en segundo plano); este panel es el botón
// manual, el selector de modo y el estado.
export function textoBytes(b) {
  if (b == null || !Number.isFinite(b)) return ''
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`
  return `${Math.round(b / 1e3)} KB`
}

// Texto del desglose barato del destino (clave+tamaño, sin MD5): «urgentes 10 en bucket · 0 pendientes».
export function textoDesglose(g) {
  if (!g || !g.urgentes || !g.resto) return ''
  const t = (n, d) => `${n} ${d.total} (${d.en_bucket} en bucket · ${d.pendientes} pendientes${d.distintos ? ` · ${d.distintos} distintos` : ''})`
  return `${t('Urgentes', g.urgentes)} · ${t('Resto', g.resto)}`
}

export function textoDuracion(seg) {
  if (seg == null || !Number.isFinite(seg) || seg < 0) return ''
  const t = Math.round(seg)
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60
  if (h > 0) return `${h} h ${m} min`
  if (m > 0) return `${m} min ${x} s`
  return `${x} s`
}

const ETIQ_TIPO = { tiff_termica: 'TIFF térmica', termica_jpg: 'Térmica JPG', rgb_crops: 'RGB crops', rgb: 'RGB', csv: 'CSV', estadillos: 'Estadillos', otros: 'Otros' }
const miles = (n) => Number(n || 0).toLocaleString('es-ES')

// Resumen de una pasada a partir de `metricas` del backend. Urgentes y resto por separado.
export function resumenMetricas(m) {
  if (!m) return null
  const grupos = ['urgentes', 'resto'].map((k) => (m[k] ? [k, m[k]] : null)).filter(Boolean)
  if (!grupos.length) return null
  const subidos = grupos.reduce((a, [, g]) => a + (g.subidos || 0), 0)
  const bytes = grupos.reduce((a, [, g]) => a + (g.bytes_subidos || 0), 0)
  const vuelos = Math.max(0, ...grupos.map(([, g]) => g.vuelos || 0))
  const dur = m.duracion_s
  const mbs = dur > 0 ? bytes / 1e6 / dur : null
  const partes = [`${miles(subidos)} ficheros`, textoBytes(bytes)]
  if (dur != null) partes.push(textoDuracion(dur))
  if (mbs != null) partes.push(`${mbs.toFixed(1)} MB/s`)
  if (vuelos) partes.push(`${vuelos} vuelos`)
  return { titulo: partes.join(' · '), grupos }
}

const ETIQ_ESTADO = { ok: 'OK', aviso: 'Aviso', error: 'Error', no_comprobable: 'No comprobable' }

// `variante`: 'tras-organizar' (por defecto, dentro de PanelOrganizar) o
// 'ya-organizado' (flujo «Subir ya organizado» de TrabajoScreen). Mismo panel,
// solo cambia el texto de ayuda.
export default function PanelResultado({ carpeta, inspeccionId, ready, variante = 'tras-organizar' }) {
  const [modo, setModo] = useState('urgencia')
  const [subiendo, setSubiendo] = useState(false)
  const [estado, setEstado] = useState(null)
  const [resumen, setResumen] = useState(null)
  const [error, setError] = useState('')
  const [aviso, setAviso] = useState('')
  // Credencial de subida ({ok, reason}); null = aún sin comprobar.
  const [cred, setCred] = useState(null)
  // Aviso pre-subida: null | {cargando} | {checks, destino, error?}
  const [previa, setPrevia] = useState(null)
  const [igualmente, setIgualmente] = useState(false)
  // Desglose del destino (derivado del bucket, sobrevive a reiniciar la app).
  const [desglose, setDesglose] = useState(null)
  const [tick, setTick] = useState(0)
  // Cronómetro y velocidad media de la subida en curso.
  const [inicio, setInicio] = useState(null)
  const [ahora, setAhora] = useState(0)
  const [muestras, setMuestras] = useState([])

  useEffect(
    () =>
      onResultado((d) => {
        switch (d.kind) {
          case 'start':
            setSubiendo(true); setError(''); setAviso(''); setResumen(null); setInicio(Date.now()); setAhora(Date.now()); setMuestras([])
            break
          case 'estado':
            setSubiendo(true); setEstado(d)
            if (d.mbps != null) setMuestras((m) => [...m.slice(-199), d.mbps])
            break
          case 'done':
            setEstado(d)
            if (!d.continua) { setSubiendo(false); setResumen(d); setTick((n) => n + 1) }
            break
          case 'aviso':
            setAviso(d.text || '')
            break
          case 'error':
            setSubiendo(false); setError(d.text || 'Error al subir el resultado.')
            break
          default:
        }
      }),
    []
  )

  async function comprobarCred() {
    try {
      if (typeof api.resultadoCredencial !== 'function') throw new Error('método resultadoCredencial no disponible')
      const r = await api.resultadoCredencial()
      return r && typeof r.ok === 'boolean' ? r : { ok: false, reason: 'No se pudo comprobar el login: respuesta vacía' }
    } catch (e) {
      return { ok: false, reason: `No se pudo comprobar el login: ${String(e?.message || e)}` }
    }
  }

  // Al cambiar el destino y al volver el foco a la ventana (el login puede
  // haber caducado o hacerse fuera mientras tanto).
  useEffect(() => {
    let vivo = true
    setPrevia(null)
    setIgualmente(false)
    const refrescar = async () => {
      const r = await comprobarCred()
      if (vivo) setCred(r)
    }
    refrescar()
    window.addEventListener('focus', refrescar)
    return () => { vivo = false; window.removeEventListener('focus', refrescar) }
  }, [carpeta])

  useEffect(() => {
    if (!subiendo) return undefined
    const id = setInterval(() => setAhora(Date.now()), 1000)
    return () => clearInterval(id)
  }, [subiendo])

  const sinCred = !cred || cred.ok === false

  // Estado del destino al elegir inspección+carpeta y al acabar cada subida.
  useEffect(() => {
    let vivo = true
    setDesglose(null)
    if (!(ready && carpeta && inspeccionId) || sinCred || subiendo || typeof api.resultadoInspeccionarDestino !== 'function') return undefined
    api.resultadoInspeccionarDestino(carpeta, inspeccionId)
      .then((d) => { if (vivo) setDesglose(d && d.ok !== false ? d.desglose || null : null) })
      .catch(() => { if (vivo) setDesglose(null) })
    return () => { vivo = false }
  }, [ready, carpeta, inspeccionId, sinCred, subiendo, tick])
  const puede = ready && carpeta && inspeccionId && !subiendo && !sinCred

  async function revisar() {
    setError('')
    setIgualmente(false)
    setPrevia({ cargando: true })
    const c = await comprobarCred()
    setCred(c)
    if (!c.ok) { setPrevia(null); return }
    try {
      const [pre, destino] = await Promise.all([
        api.resultadoPrechequeo(carpeta),
        api.resultadoInspeccionarDestino(carpeta, inspeccionId),
      ])
      if (!pre || typeof pre !== 'object' || !Array.isArray(pre.checks)) {
        throw new Error('Respuesta inesperada del prechequeo de la subida')
      }
      if (!destino || typeof destino !== 'object') {
        throw new Error('Respuesta inesperada al inspeccionar el destino')
      }
      setPrevia({ checks: pre.checks, okPre: pre.ok !== false, destino })
    } catch (e) {
      setPrevia(null)
      setError(String(e?.message || e))
    }
  }

  async function lanzar(modoForzado) {
    const m = typeof modoForzado === 'string' ? modoForzado : modo
    setPrevia(null)
    setError('')
    try {
      const r = await api.resultadoSubir(carpeta, inspeccionId, m, false)
      if (r && r.started === false) setError(r.reason || 'No se pudo iniciar la subida.')
    } catch (e) {
      setError(String(e?.message || e))
    }
  }

  const nConf = resumen?.conflictos?.length || 0
  const nFall = resumen?.fallidas?.length || 0
  const rest = desglose?.resto
  const nResto = rest ? rest.pendientes + rest.distintos : 0
  const card = resumen ? resumenMetricas(resumen.metricas) : null
  const mediaMBs = muestras.length ? muestras.reduce((a, b) => a + b, 0) / muestras.length / 8 : null
  const completa = resumen && resumen.ok && !resumen.cancelled

  return (
    <div className="field">
      <span className="field-label">Resultado en el bucket</span>
      <div role="radiogroup" aria-label="Modo de subida" className="field-row">
        {[['urgencia', 'Urgencia'], ['normal', 'Normal']].map(([v, t]) => (
          <label key={v} className="check">
            <input type="radio" name="modo-resultado" checked={modo === v} onChange={() => setModo(v)} disabled={subiendo} aria-label={t} />
            <span>{t}</span>
          </label>
        ))}
      </div>
      {variante === 'ya-organizado' && (
        <span className="field-hint">Se sube tal cual la carpeta ya organizada, sin volver a organizar.</span>
      )}
      <span className="field-hint">
        Urgencia sube lo necesario para el análisis. Normal sube además el resto, sin repetir lo ya subido.
      </span>
      <div className="field-row">
        <button type="button" className="btn-ghost" onClick={revisar} disabled={!puede || Boolean(previa)}>Publicar resultado</button>
        {subiendo && <button type="button" className="btn-ghost" onClick={() => api.resultadoCancelar()}>Cancelar</button>}
      </div>
      {desglose && !subiendo && <span className="field-hint">{textoDesglose(desglose)}</span>}
      {nResto > 0 && !subiendo && (
        <div className="field-row">
          <button type="button" className="btn-run" onClick={() => { setModo('normal'); lanzar('normal') }} disabled={!puede}>
            Subir el resto ({nResto} ficheros · {textoBytes(rest.bytes_pendientes)})
          </button>
          <span className="field-hint">Modo normal: se omite lo que ya está en el bucket.</span>
        </div>
      )}
      {cred && sinCred && <span className="field-hint hint-warn" role="alert">{cred.reason || 'No hay credencial para publicar.'}</span>}
      {previa?.cargando && <span className="field-hint" aria-live="polite">Revisando antes de subir…</span>}
      {previa && !previa.cargando && (() => {
        const hayError = previa.checks.some((c) => c.estado === 'error') || !previa.okPre || previa.destino?.ok === false
        const d = previa.destino
        return (
          <div className="field" role="group" aria-label="Aviso antes de subir">
            <span className="field-label">Antes de subir</span>
            {previa.checks.map((c) => (
              <span key={c.id} className={`field-hint ${c.estado === 'ok' ? 'hint-ok' : c.estado === 'no_comprobable' ? '' : 'hint-warn'}`}>
                <strong>{ETIQ_ESTADO[c.estado] || c.estado}</strong> · {c.titulo}{c.detalle ? `: ${c.detalle}` : ''}
              </span>
            ))}
            {d?.ok === false && (
              <span className="field-hint hint-warn" role="alert">{d.error || 'No se pudo inspeccionar el destino en el bucket.'}</span>
            )}
            {d?.desglose && <span className="field-hint">{textoDesglose(d.desglose)}</span>}
            {d?.existe && (
              <span className="field-hint hint-warn">
                Ya hay {d.n_objetos} objetos / {textoBytes(d.bytes)} en el bucket
                {d.sobrescribe?.length ? `; se sobrescriben: ${d.sobrescribe.join(', ')}` : ''}
                {d.detalle ? `. ${d.detalle}` : ''}
              </span>
            )}
            {d && !d.existe && d.detalle && <span className="field-hint">{d.detalle}</span>}
            {hayError && (
              <label className="check">
                <input type="checkbox" checked={igualmente} onChange={(e) => setIgualmente(e.target.checked)} />
                <span>Subir igualmente</span>
              </label>
            )}
            <div className="field-row">
              <button type="button" className="btn-run" onClick={lanzar} disabled={hayError && !igualmente}>Confirmar y subir</button>
              <button type="button" className="btn-ghost" onClick={() => setPrevia(null)}>Cancelar aviso</button>
            </div>
          </div>
        )
      })()}
      {!(carpeta && inspeccionId) && <span className="field-hint">Elige la inspección y la carpeta de salida para poder publicar.</span>}
      {subiendo && !estado && <span className="field-hint" aria-live="polite">Calculando…</span>}
      {estado?.prefix && <span className="field-hint hint-destino"><strong>Destino: gs://plantas_pv_nl/{estado.prefix.replace(/^\/+|\/+$/g, '')}/</strong></span>}
      {estado && <span className="field-hint" aria-live="polite">{textoProgreso(estado)}</span>}
      {subiendo && inicio && (
        <span className="field-hint" aria-live="off">
          Transcurrido {textoDuracion((ahora - inicio) / 1000)}
          {mediaMBs != null ? ` · velocidad media ${mediaMBs.toFixed(1)} MB/s` : ''}
          {estado?.bytes_total ? ` · ${textoBytes(estado.bytes_done)} / ${textoBytes(estado.bytes_total)}` : ''}
        </span>
      )}
      {estado && textoVelocidad(estado) && <span className="field-hint">{textoVelocidad(estado)}</span>}
      {completa && <span className="field-hint hint-ok">Subida completa</span>}
      {card && (
        <div className="field" role="group" aria-label="Resumen de la subida">
          <span className="field-label">Resumen</span>
          <span className="field-hint hint-ok"><strong>{card.titulo}</strong></span>
          {card.grupos.map(([k, g]) => (
            <span key={k} className="field-hint">
              {k === 'urgentes' ? 'Urgentes' : 'Resto'}: {miles(g.subidos)} subidos ({textoBytes(g.bytes_subidos)}) · {miles(g.omitidos)} ya en bucket ({textoBytes(g.bytes_omitidos)}) · {miles(g.pendientes - g.subidos)} sin subir
              {resumen.metricas.conflictos_n ? ` · ${resumen.metricas.conflictos_n} conflictos` : ''}
              {' · '}{Object.entries(g.por_tipo || {}).map(([t, v]) => `${ETIQ_TIPO[t] || t} ${miles(v.ficheros)}`).join(', ')}
              {g.vuelos ? ` · ${g.vuelos} vuelos` : ''}
            </span>
          ))}
        </div>
      )}
      {resumen?.cancelled && <span className="field-hint hint-warn">Subida cancelada. Lo ya subido se conserva.</span>}
      {nConf > 0 && <span className="field-hint hint-warn">{nConf} en conflicto (existen con otro contenido y no se pisan)</span>}
      {nFall > 0 && (
        <span className="field-hint hint-warn">
          {nFall} fallida{nFall === 1 ? '' : 's'}{' '}
          <button type="button" className="btn-ghost" onClick={lanzar} disabled={!puede}>Resubir fallidas</button>
        </span>
      )}
      {(resumen?.avisos || []).map((a) => <span key={a} className="field-hint hint-warn">{a}</span>)}
      {aviso && <span className="field-hint hint-warn">{aviso}</span>}
      {error && <span className="field-hint hint-warn" role="alert">{error}</span>}
    </div>
  )
}
