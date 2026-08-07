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

// Desglose de lo analizado en la fase: "34 de 120 img · 90 RGB · 30 térmicas".
// El total y el reparto solo aparecen cuando el pipeline los ha anunciado.
function statsLine(s) {
  if (!s) return ''
  const parts = []
  if (s.total > 0) parts.push(`${Math.min(s.done, s.total)} de ${s.total} img`)
  else if (s.done > 0) parts.push(`${s.done} img`)
  if (s.rgb > 0) parts.push(`${s.rgb} RGB`)
  if (s.termica > 0) parts.push(`${s.termica} ${s.termica === 1 ? 'térmica' : 'térmicas'}`)
  return parts.join(' · ')
}

// Resumen de rotación del run: qué se ha girado y en qué sentido.
function rotLine(s) {
  if (!s) return ''
  const total = s.rot270 + s.rot90 + s.rot_none
  if (total === 0) return ''
  const parts = []
  if (s.rot270 > 0) parts.push(`${s.rot270} giradas 270°`)
  if (s.rot90 > 0) parts.push(`${s.rot90} giradas 90°`)
  if (s.rot_none > 0) parts.push(`${s.rot_none} sin girar`)
  return parts.join(' · ')
}

export default function ProgressModal({
  plant,
  phases,
  progress,
  stats,
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
                <>
                  <div className="pm-sub">
                    <div className="pm-bar">
                      <div className="pm-bar-fill" style={{ width: `${progress}%` }} />
                    </div>
                    <span className="pm-pct">{progress}%</span>
                  </div>
                  {statsLine(stats) && (
                    <div className="pm-stats">{statsLine(stats)}</div>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>

        {/* Rotación: acumulado del run, visible durante y después del proceso. */}
        {rotLine(stats) && (
          <p className="pm-rot">
            <span className="pm-rot-label">Rotación:</span> {rotLine(stats)}
          </p>
        )}

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
