import { useEffect, useState } from 'react'
import { api, onResultado } from '../bridge'

export function textoProgreso(e) {
  if (!e || !e.urgentes || !e.resto) return ''
  return `urgentes ${e.urgentes.hechos}/${e.urgentes.total} · resto ${e.resto.hechos}/${e.resto.total}`
}

// Subida del resultado organizado al bucket de plantas. Se dispara sola al terminar
// cada run (urgencia y, después, normal en segundo plano); este panel es el botón
// manual, el selector de modo y el estado.
export default function PanelResultado({ carpeta, inspeccionId, ready }) {
  const [modo, setModo] = useState('urgencia')
  const [subiendo, setSubiendo] = useState(false)
  const [estado, setEstado] = useState(null)
  const [resumen, setResumen] = useState(null)
  const [error, setError] = useState('')
  const [aviso, setAviso] = useState('')

  useEffect(
    () =>
      onResultado((d) => {
        switch (d.kind) {
          case 'start':
            setSubiendo(true); setError(''); setAviso(''); setResumen(null)
            break
          case 'estado':
            setSubiendo(true); setEstado(d)
            break
          case 'done':
            setEstado(d)
            if (!d.continua) { setSubiendo(false); setResumen(d) }
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

  const puede = ready && carpeta && inspeccionId && !subiendo

  async function lanzar() {
    setError('')
    try {
      const r = await api.resultadoSubir(carpeta, inspeccionId, modo, false)
      if (r && r.started === false) setError(r.reason || 'No se pudo iniciar la subida.')
    } catch (e) {
      setError(String(e?.message || e))
    }
  }

  const nConf = resumen?.conflictos?.length || 0
  const nFall = resumen?.fallidas?.length || 0
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
      <span className="field-hint">
        Urgencia sube lo necesario para el análisis. Normal sube además el resto, sin repetir lo ya subido.
      </span>
      <div className="field-row">
        <button type="button" className="btn-ghost" onClick={lanzar} disabled={!puede}>Subir al bucket</button>
        {subiendo && <button type="button" className="btn-ghost" onClick={() => api.resultadoCancelar()}>Cancelar</button>}
      </div>
      {!(carpeta && inspeccionId) && <span className="field-hint">Elige la inspección y la carpeta de salida para poder subir.</span>}
      {estado && <span className="field-hint" aria-live="polite">{textoProgreso(estado)}</span>}
      {completa && <span className="field-hint hint-ok">Subida completa</span>}
      {resumen?.cancelled && <span className="field-hint hint-warn">Subida cancelada. Lo ya subido se conserva.</span>}
      {nConf > 0 && <span className="field-hint hint-warn">{nConf} en conflicto (existen con otro contenido y no se pisan)</span>}
      {nFall > 0 && (
        <span className="field-hint hint-warn">
          {nFall} fallida{nFall === 1 ? '' : 's'}{' '}
          <button type="button" className="btn-ghost" onClick={lanzar} disabled={!puede}>Reintentar fallidas</button>
        </span>
      )}
      {(resumen?.avisos || []).map((a) => <span key={a} className="field-hint hint-warn">{a}</span>)}
      {aviso && <span className="field-hint hint-warn">{aviso}</span>}
      {error && <span className="field-hint hint-warn" role="alert">{error}</span>}
    </div>
  )
}
