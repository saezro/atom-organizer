import { useState } from 'react'

// Modal de progreso por fases. Reemplaza la lluvia de "." por un checklist
// estructurado con el nombre de la planta, la fase en curso + barra %, y el
// log crudo escondido tras "ver detalle". Estado calculado en App a partir de
// los eventos `atom:progress` (plant / plan / phase / progress / done / error).

const ICON = { done: '✓', active: '●', pending: '⏳', error: '✗' }

// Duración legible: "8.4 s" o "1 min 12 s".
function fmtDur(s) {
  if (s == null) return ''
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return `${m} min ${r} s`
}

export default function ProgressModal({
  plant,
  phases,
  progress,
  imgCount,
  detail,
  finished,
  onClose,
}) {
  const [showDetail, setShowDetail] = useState(false)

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card">
        <h2 className="pm-title">
          Procesando planta: <span className="pm-plant">{plant || '—'}</span>
        </h2>

        <ul className="pm-phases">
          {phases.length === 0 && (
            <li className="pm-phase pm-active">
              <span className="pm-ico">●</span>
              <span className="pm-name">Preparando…</span>
            </li>
          )}
          {phases.map((p, i) => (
            <li key={i} className={'pm-phase pm-' + p.status}>
              <span className="pm-ico">{ICON[p.status]}</span>
              <span className="pm-name">{p.name}</span>
              {/* Tiempo de la fase (visible en cuanto se cierra). */}
              {p.duration != null && (
                <span className="pm-dur">{fmtDur(p.duration)}</span>
              )}
              {p.errors > 0 && (
                <span className="pm-errbadge">{p.errors} err</span>
              )}
              {p.status === 'active' && !finished && (
                <div className="pm-sub">
                  <div className="pm-bar">
                    <div className="pm-bar-fill" style={{ width: `${progress}%` }} />
                  </div>
                  <span className="pm-pct">
                    {imgCount > 0 ? `${imgCount} img · ` : ''}
                    {progress}%
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>

        {finished && (
          <p
            className={
              'pm-status ' +
              (!finished.ok ? 'err' : finished.warn ? 'warn' : 'ok')
            }
          >
            {!finished.ok
              ? `✗ ${finished.msg || 'Error'}`
              : finished.warn
                ? finished.kind === 'warning'
                  ? `⚠ Terminado con avisos: imágenes fuera del estadillo en SIN_ORDENAR${finished.elapsed != null ? ` · ${fmtDur(finished.elapsed)}` : ''}`
                  : `⚠ Terminado con ${finished.errors} ${finished.errors === 1 ? 'error' : 'errores'}${finished.elapsed != null ? ` · ${fmtDur(finished.elapsed)}` : ''}`
                : `✓ Proceso terminado${finished.elapsed != null ? ` · ${fmtDur(finished.elapsed)}` : ''}`}
          </p>
        )}

        {detail.length > 0 && (
          <div className="pm-detail">
            <button
              type="button"
              className="pm-detail-toggle"
              onClick={() => setShowDetail((v) => !v)}
              aria-expanded={showDetail}
            >
              <span aria-hidden="true">{showDetail ? '▾' : '▸'}</span> ver detalle (log crudo)
            </button>
            {showDetail && <pre className="pm-log">{detail.join('\n')}</pre>}
          </div>
        )}

        <div className="pm-actions">
          <button
            type="button"
            className="btn-run pm-close"
            disabled={!finished}
            onClick={onClose}
          >
            {finished ? 'Cerrar' : 'Procesando…'}
          </button>
        </div>
      </div>
    </div>
  )
}
