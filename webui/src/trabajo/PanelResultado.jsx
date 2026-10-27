import { useEffect, useMemo, useRef, useState } from 'react'
import { api, onResultado } from '../bridge'
import GraficaSubida from './GraficaSubida'
import { añadirMuestra, diagnosticarCuello, estadisticasSerie } from './subidaMedicion'

const nf = (n, d = 1) => Number(n).toLocaleString('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d })
const miles = (n) => Number(n || 0).toLocaleString('es-ES')

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
  const partes = [`${nf(e.mbps / 8)} MB/s`]
  if (e.files_per_s != null) partes.push(`${nf(e.files_per_s)} img/s`)
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

export function textoBytes(b) {
  if (b == null || !Number.isFinite(b)) return ''
  if (b >= 1e9) return `${nf(b / 1e9)} GB`
  if (b >= 1e6) return `${nf(b / 1e6)} MB`
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
const ETIQ_ESTADO = { ok: 'OK', aviso: 'Aviso', error: 'Error', no_comprobable: 'No comprobable' }

const sumaDesgloses = (grupos, clave) => {
  const out = {}
  for (const [, g] of grupos) {
    for (const [k, v] of Object.entries(g[clave] || {})) {
      const d = out[k] || (out[k] = { ficheros: 0, bytes: 0 })
      d.ficheros += v.ficheros || 0
      d.bytes += v.bytes || 0
    }
  }
  return out
}

// Resumen de una pasada a partir de `metricas` del backend. Urgentes y resto por separado.
// `titulo` = «12.122 ficheros · 23,7 GB · 17 min 32 s · 22,6 MB/s · 28 vuelos».
export function resumenMetricas(m) {
  if (!m) return null
  const grupos = ['urgentes', 'resto'].map((k) => (m[k] ? [k, m[k]] : null)).filter(Boolean)
  if (!grupos.length) return null
  const subidos = grupos.reduce((a, [, g]) => a + (g.subidos || 0), 0)
  const bytes = grupos.reduce((a, [, g]) => a + (g.bytes_subidos || 0), 0)
  const porTipo = sumaDesgloses(grupos, 'subidos_por_tipo')
  const porVuelo = sumaDesgloses(grupos, 'subidos_por_vuelo')
  // Con desglose de lo subido, vuelos = los que tuvieron ficheros subidos; sin él (backend
  // antiguo) solo se sabe el máximo de las dos selecciones.
  const vuelos = Object.keys(porVuelo).length || Math.max(0, ...grupos.map(([, g]) => g.vuelos || 0))
  const dur = m.duracion_s
  const mbs = dur > 0 ? bytes / 1e6 / dur : null
  const partes = [`${miles(subidos)} ficheros`, textoBytes(bytes)]
  if (dur != null) partes.push(textoDuracion(dur))
  if (mbs != null) partes.push(`${nf(mbs)} MB/s`)
  if (vuelos) partes.push(`${vuelos} vuelos`)
  return { titulo: partes.join(' · '), grupos, subidos, bytes, dur, mbs, vuelos, porTipo, porVuelo }
}

function Kpi({ etiqueta, valor, sub }) {
  return (
    <div className="sub-kpi">
      <span className="sub-kpi-etq">{etiqueta}</span>
      <span className="sub-kpi-val">{valor}</span>
      {sub && <span className="sub-kpi-sub">{sub}</span>}
    </div>
  )
}

function Barra({ pct }) {
  const p = Math.max(0, Math.min(100, pct))
  return (
    <div className="sub-barra" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p)} aria-label="Progreso de la subida">
      <div className="sub-barra-relleno" style={{ width: `${p}%` }} />
    </div>
  )
}

function Cuello({ diag }) {
  if (!diag) return null
  const visibles = diag.partes.filter((p) => p.pct >= 0.5)
  return (
    <div className={`sub-cuello sub-cuello-${diag.clave}`} role="group" aria-label="Cuello de botella">
      <div className="sub-cuello-cab">
        <span className="sub-cuello-etq">Cuello de botella</span>
        <strong className="sub-cuello-titulo">{diag.titulo}</strong>
      </div>
      <span className="sub-cuello-detalle">{diag.detalle}</span>
      {visibles.length > 0 && diag.clave !== 'medir' && (
        <>
          <div className="sub-reparto" aria-hidden="true">
            {visibles.map((p) => <span key={p.clave} className={`sub-reparto-${p.clave}`} style={{ width: `${p.pct}%` }} title={`${p.label}: ${nf(p.pct, 0)} %`} />)}
          </div>
          <ul className="sub-reparto-leyenda">
            {visibles.map((p) => <li key={p.clave}><i className={`sub-reparto-${p.clave}`} />{p.label} {nf(p.pct, 0)} %</li>)}
          </ul>
        </>
      )}
      <span className="sub-cuello-nota">{diag.advertencia}</span>
    </div>
  )
}

// `onActividad({subiendo, pct, error})` avisa a quien pinta la pestaña (punto de actividad).
// El panel se queda montado aunque su pestaña no esté a la vista para no perder la gráfica.
export default function PanelResultado({ carpeta, inspeccionId, ready, variante = 'tras-organizar', onActividad }) {
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
  // «Al terminar, subir el resto»: lo ejecuta el backend al acabar urgentes (marcada por defecto).
  const [encadenar, setEncadenar] = useState(true)
  const encadenarRef = useRef(true)
  // El backend confirmó que esta subida tiene fase de resto (evento `start`).
  const [hayResto, setHayResto] = useState(false)
  // Ficheros que el Organizer no sabe clasificar (no se suben): [{ruta, motivo}] o null = sin consultar.
  const [sinClasDestino, setSinClasDestino] = useState(null)
  // Cronómetro de la subida en curso y serie para la gráfica.
  const [inicio, setInicio] = useState(null)
  const [ahora, setAhora] = useState(0)
  const [serie, setSerie] = useState([])

  useEffect(
    () =>
      onResultado((d) => {
        switch (d.kind) {
          case 'start':
            setHayResto(Boolean(d.resto))
            // Casilla desmarcada entre pulsar y arrancar (aún sin subida en curso): se reenvía.
            if (d.resto && encadenarRef.current === false && typeof api.resultadoEncadenar === 'function') {
              Promise.resolve(api.resultadoEncadenar(false)).catch(() => {})
            }
            setSubiendo(true); setError(''); setAviso(''); setResumen(null); setEstado(null); setInicio(Date.now()); setAhora(Date.now()); setSerie([])
            break
          case 'estado':
            setSubiendo(true); setEstado(d)
            setSerie((s) => añadirMuestra(s, d))
            break
          case 'done':
            setEstado(d)
            if (!d.continua) { setSubiendo(false); setResumen(d); setTick((n) => n + 1) }
            break
          case 'encadenar':
            setEncadenar(Boolean(d.activo)); encadenarRef.current = Boolean(d.activo)
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
    setSinClasDestino(null)
    if (!(ready && carpeta && inspeccionId) || sinCred || subiendo || typeof api.resultadoInspeccionarDestino !== 'function') return undefined
    api.resultadoInspeccionarDestino(carpeta, inspeccionId)
      .then((d) => {
        if (!vivo) return
        setDesglose(d && d.ok !== false ? d.desglose || null : null)
        setSinClasDestino(d && d.ok !== false && Array.isArray(d.sin_clasificar_lista) ? d.sin_clasificar_lista : null)
      })
      .catch(() => { if (vivo) { setDesglose(null); setSinClasDestino(null) } })
    return () => { vivo = false }
  }, [ready, carpeta, inspeccionId, sinCred, subiendo, tick])
  const puede = ready && carpeta && inspeccionId && !subiendo && !sinCred

  async function revisar(m = 'urgencia') {
    setModo(m)
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

  function cambiarEncadenar(v) {
    setEncadenar(v)
    encadenarRef.current = v
    if (subiendo && typeof api.resultadoEncadenar === 'function') {
      Promise.resolve(api.resultadoEncadenar(v))
        .then((r) => { if (r && r.ok === false) setHayResto(false) })
        .catch((e) => setError(String(e?.message || e)))
    }
  }

  async function lanzar(modoForzado) {
    const m = typeof modoForzado === 'string' ? modoForzado : modo
    setPrevia(null)
    setError('')
    try {
      const seguir = m === 'urgencia' && encadenar && !(desglose?.resto && nResto === 0)
      const r = await api.resultadoSubir(carpeta, inspeccionId, m, seguir)
      if (r && r.started === false) setError(r.reason || 'No se pudo iniciar la subida.')
    } catch (e) {
      setError(String(e?.message || e))
    }
  }

  const nConf = resumen?.conflictos?.length || 0
  const nFall = resumen?.fallidas?.length || 0
  const rest = desglose?.resto
  const nResto = rest ? rest.pendientes + rest.distintos : 0
  const urg = desglose?.urgentes
  const nUrg = urg ? urg.pendientes + urg.distintos : 0
  const sinClas = sinClasDestino ?? resumen?.sin_clasificar_lista ?? []
  const nSin = sinClas.length
  const todoSubido = Boolean(urg && rest) && nUrg === 0 && nResto === 0 && nSin === 0
  const restoEnCola = subiendo && hayResto && encadenar && modo === 'urgencia' && estado?.modo !== 'normal'
  const card = resumen ? resumenMetricas(resumen.metricas) : null
  const completa = resumen && resumen.ok && !resumen.cancelled && nSin === 0
  const est = useMemo(() => estadisticasSerie(serie), [serie])

  // Progreso del modo en curso (urgencia = solo urgentes; normal = ambos).
  const enNormal = estado?.modo === 'normal'
  const fHechos = estado ? (estado.urgentes?.hechos || 0) + (enNormal ? estado.resto?.hechos || 0 : 0) : 0
  const fTotal = estado ? (estado.urgentes?.total || 0) + (enNormal ? estado.resto?.total || 0 : 0) : 0
  const pct = estado?.bytes_total > 0 ? (estado.bytes_done / estado.bytes_total) * 100 : fTotal > 0 ? (fHechos / fTotal) * 100 : 0
  const preparando = estado && (estado.fase === 'listando' || estado.comprobando)

  // Diagnóstico: en vivo con el último evento; al terminar, con las métricas finales.
  const medidasDiag = resumen?.metricas?.medidas || estado?.medidas
  const diag = useMemo(() => {
    if (!medidasDiag) return null
    const bytes = resumen?.metricas?.bytes_enviados ?? estado?.bytes_enviados ?? 0
    return diagnosticarCuello({
      medidas: medidasDiag, bytes,
      mbpsMedio: est.media != null ? est.media * 8 : null,
      ficheros: resumen?.metricas ? card?.subidos : estado?.ficheros_subidos,
      hilosMax: resumen?.metricas?.hilos_max ?? estado?.hilos_max,
    })
  }, [medidasDiag, resumen, estado, est.media, card])

  useEffect(() => {
    if (onActividad) onActividad({ subiendo, pct: Math.round(pct), error: Boolean(error) })
  }, [onActividad, subiendo, pct, error])

  const bucketCol = (titulo, d) => (
    <div className="sub-estado-col">
      <span className="sub-estado-tit">{titulo}</span>
      {d ? (
        <>
          <span className="sub-estado-num"><strong>{miles(d.en_bucket)}</strong> / {miles(d.total)} ficheros en bucket{d.bytes_total != null ? ` · ${textoBytes(d.bytes_total)} del grupo` : ''}</span>
          <span className={'sub-estado-sub' + (d.pendientes ? ' sub-pend' : '')}>
            {d.pendientes ? `${miles(d.pendientes)} pendientes · ${textoBytes(d.bytes_pendientes)}` : nSin > 0 ? `Sin pendientes subibles · ${miles(nSin)} sin clasificar` : 'Nada pendiente'}
          </span>
          {d.distintos > 0 && <span className="sub-estado-sub sub-aviso">{miles(d.distintos)} con otro tamaño en el bucket</span>}
        </>
      ) : <span className="sub-estado-sub">—</span>}
    </div>
  )

  return (
    <div className="sub" data-variante={variante}>
      <header className="sub-cab">
        <div className="sub-cab-tit">
          <h3 className="sub-titulo">Resultado en el bucket</h3>
          {estado?.prefix || previa?.destino?.prefijo ? (
            <span className="sub-destino"><strong>Destino: gs://plantas_pv_nl/{(estado?.prefix || previa.destino.prefijo).replace(/^\/+|\/+$/g, '')}/</strong></span>
          ) : (
            <span className="sub-destino-vacio">
              {variante === 'ya-organizado'
                ? 'Se sube tal cual la carpeta ya organizada, sin volver a organizar.'
                : 'Sube la carpeta organizada al bucket de la inspección.'}
            </span>
          )}
        </div>
        {!subiendo && puede && (
          <button type="button" className="btn-ghost sub-actualizar" onClick={() => setTick((n) => n + 1)}>Actualizar estado</button>
        )}
      </header>

      <div className="sub-estado" aria-label="Estado del bucket">
        {bucketCol('Urgentes', desglose?.urgentes)}
        {bucketCol('Resto', desglose?.resto)}
        <div className="sub-estado-col">
          <span className="sub-estado-tit">Conflictos</span>
          <span className={'sub-estado-num' + (nConf ? ' sub-aviso' : '')}><strong>{resumen ? nConf : '—'}</strong>{resumen ? ' en la última pasada' : ''}</span>
          <span className="sub-estado-sub">Existen con otro contenido; no se pisan.</span>
        </div>
      </div>
      {nSin > 0 && (
        <div className="field-hint hint-warn sub-sin-clasificar" role="alert">
          <strong>{miles(nSin)} fichero(s) sin clasificar, no se subirán</strong>
          <details>
            <summary>{miles(nSin)} fichero(s) sin clasificar — ver lista</summary>
            <ul>
              {sinClas.map((x) => <li key={x.ruta}>{x.ruta} — {x.motivo}</li>)}
            </ul>
          </details>
        </div>
      )}
      {desglose && <span className="sr-only">{textoDesglose(desglose)}</span>}

      <div className="sub-acciones">
        <button type="button" className="btn-run" onClick={() => revisar('urgencia')} disabled={!puede || Boolean(previa) || (Boolean(urg) && nUrg === 0)}
          title={urg && nUrg === 0 ? (nSin ? 'Sin urgentes pendientes' : 'Todo subido') : undefined}>Publicar urgentes</button>
        <label className="check sub-encadenar">
          <input type="checkbox" checked={encadenar && !(!subiendo && rest && nResto === 0)} disabled={!subiendo && Boolean(rest) && nResto === 0}
            onChange={(e) => cambiarEncadenar(e.target.checked)} />
          <span>Al terminar, subir el resto</span>
        </label>
        {restoEnCola && <span className="sub-resto-cola" aria-live="polite">Resto en cola</span>}
        <button type="button" className="btn-ghost" onClick={() => { setModo('normal'); lanzar('normal') }} disabled={!puede || !nResto} title={rest && !nResto ? (nSin ? 'Sin resto pendiente' : 'Todo subido') : undefined}>
          Subir el resto{nResto > 0 ? ` (${miles(nResto)} · ${textoBytes(rest.bytes_pendientes)})` : ''}
        </button>
        <button type="button" className="btn-ghost" onClick={() => revisar('normal')} disabled={!puede || Boolean(previa) || todoSubido}
          title={todoSubido ? 'Todo subido' : undefined}>Subir todo</button>
        {subiendo && <button type="button" className="btn-ghost sub-cancelar" onClick={() => api.resultadoCancelar()}>Cancelar</button>}
      </div>
      <span className="field-hint">
        Urgentes = lo necesario para el análisis. El resto se omite si ya está en el bucket; «Subir todo» hace ambos sin repetir.
      </span>

      {cred && sinCred && <span className="field-hint hint-warn" role="alert">{cred.reason || 'No hay credencial para publicar.'}</span>}
      {previa?.cargando && <span className="field-hint" aria-live="polite">Revisando antes de subir…</span>}
      {previa && !previa.cargando && (() => {
        const hayError = previa.checks.some((c) => c.estado === 'error') || !previa.okPre || previa.destino?.ok === false
        const d = previa.destino
        return (
          <div className="sub-previa" role="group" aria-label="Aviso antes de subir">
            <span className="field-label">Antes de subir ({modo === 'normal' ? 'urgentes y resto' : 'solo urgentes'})</span>
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
              <div className="field-hint hint-warn sub-destino-total">
                <strong>Destino completo: {miles(d.n_objetos)} objetos / {textoBytes(d.bytes)}</strong>
                {' '}(total de todo lo que hay en el bucket, no de esta subida)
                {d.sobrescribe?.length ? (
                  <details>
                    <summary>{miles(d.sobrescribe.length)} ficheros pequeños (CSV, estadillos, índice) que se sobrescriben — ver lista</summary>
                    <span>{d.sobrescribe.join(', ')}</span>
                  </details>
                ) : null}
              </div>
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

      {(subiendo || (estado && !resumen)) && (
        <section className="sub-progreso" aria-label="Subida en curso">
          <div className="sub-progreso-cab">
            <span className="sub-pct">{preparando ? '…' : `${Math.floor(pct)} %`}</span>
            <span className="sub-fase" aria-live="polite">
              {estado ? textoProgreso(estado) : 'Calculando…'}
            </span>
          </div>
          <Barra pct={preparando ? 0 : pct} />
          {estado && !preparando && (
            <div className="sub-kpis">
              <Kpi etiqueta="Datos" valor={`${textoBytes(estado.bytes_done)} / ${textoBytes(estado.bytes_total)}`}
                sub={estado.bytes_enviados != null ? `${textoBytes(estado.bytes_enviados)} enviados ahora` : null} />
              <Kpi etiqueta="Ficheros" valor={`${miles(fHechos)} / ${miles(fTotal)}`}
                sub={estado.files_per_s != null ? `${nf(estado.files_per_s)} img/s` : null} />
              <Kpi etiqueta="Transcurrido" valor={textoDuracion(inicio ? (ahora - inicio) / 1000 : estado.t)} />
              <Kpi etiqueta="Tiempo restante" valor={estado.eta != null ? `~${textoEta(estado.eta)}` : '—'} />
              <Kpi etiqueta="Velocidad ahora" valor={est.actual != null ? `${nf(est.actual)} MB/s` : '—'} />
              <Kpi etiqueta="Velocidad media" valor={est.media != null ? `${nf(est.media)} MB/s` : '—'}
                sub={estado.hilos_max ? `${estado.hilos_activos ?? 0}/${estado.hilos_max} hilos activos` : null} />
            </div>
          )}
          <GraficaSubida serie={serie} />
          <Cuello diag={diag} />
        </section>
      )}

      {completa && <span className="field-hint hint-ok">Subida completa</span>}
      {resumen && resumen.ok && !resumen.cancelled && nSin > 0 && (
        <span className="field-hint hint-warn">Subida incompleta: hay ficheros sin clasificar</span>
      )}
      {card && (
        <section className="sub-resumen" role="group" aria-label="Resumen de la subida">
          <span className="sub-resumen-etq">Resumen</span>
          <strong className="sub-resumen-titulo">{card.titulo}</strong>
          <div className="sub-resumen-grupos">
            {card.grupos.map(([k, g]) => (
              <span key={k} className="field-hint">
                {k === 'urgentes' ? 'Urgentes' : 'Resto'}: {miles(g.subidos)} subidos ({textoBytes(g.bytes_subidos)}) · {miles(g.omitidos)} ya en bucket ({textoBytes(g.bytes_omitidos)}) · {miles(g.pendientes - g.subidos)} sin subir
                {resumen.metricas.conflictos_n ? ` · ${resumen.metricas.conflictos_n} conflictos` : ''}
              </span>
            ))}
          </div>
          <div className="sub-resumen-desgloses">
            {Object.keys(card.porTipo).length > 0 && (
              <table className="sub-tabla" aria-label="Subido por tipo">
                <thead><tr><th scope="col">Tipo</th><th scope="col">Ficheros</th><th scope="col">Tamaño</th></tr></thead>
                <tbody>
                  {Object.entries(card.porTipo).sort((a, b) => b[1].bytes - a[1].bytes).map(([t, v]) => (
                    <tr key={t}><th scope="row">{ETIQ_TIPO[t] || t}</th><td>{miles(v.ficheros)}</td><td>{textoBytes(v.bytes)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
            {Object.keys(card.porVuelo).length > 0 && (() => {
              const filas = Object.entries(card.porVuelo).sort((a, b) => a[0].localeCompare(b[0], 'es', { numeric: true }))
              const maxB = Math.max(...filas.map(([, v]) => v.bytes), 1)
              return (
                <div className="sub-vuelos" role="table" aria-label="Subido por vuelo">
                  {filas.map(([v, d]) => (
                    <div className="sub-vuelo" role="row" key={v}>
                      <span className="sub-vuelo-n" role="rowheader">{v}</span>
                      <span className="sub-vuelo-b" aria-hidden="true"><i style={{ width: `${(d.bytes / maxB) * 100}%` }} /></span>
                      <span className="sub-vuelo-v" role="cell">{miles(d.ficheros)} · {textoBytes(d.bytes)}</span>
                    </div>
                  ))}
                </div>
              )
            })()}
          </div>
        </section>
      )}
      {resumen && !subiendo && serie.length >= 2 && (
        <section className="sub-resumen-grafica" aria-label="Gráfica de la subida">
          <GraficaSubida serie={serie} />
          {!card && <Cuello diag={diag} />}
        </section>
      )}
      {resumen && !subiendo && card && <Cuello diag={diag} />}
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
