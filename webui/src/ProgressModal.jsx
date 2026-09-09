import { useEffect, useState } from 'react'

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

// Desglose específico de la fase de Índice: viene con un payload distinto
// (sin `done`, con `rgb_extra`/`sin_asignar`/`sin_timestamp`/`vuelos`, ver
// `atom_core/indice.py`). Se detecta por la presencia de `vuelos`, que solo
// emite esta fase.
function indiceStatsLine(s) {
  const parts = []
  if (s.total > 0) parts.push(`${s.total} img`)
  if (s.rgb > 0) parts.push(`${s.rgb} RGB`)
  if (s.rgb_extra > 0) parts.push(`${s.rgb_extra} RGB extra`)
  if (s.termica > 0) {
    parts.push(`${s.termica} ${s.termica === 1 ? 'térmica' : 'térmicas'}`)
  }
  if (s.sin_asignar > 0) parts.push(`${s.sin_asignar} sin asignar`)
  if (s.sin_timestamp > 0) parts.push(`${s.sin_timestamp} sin timestamp`)
  if (s.vuelos > 0) parts.push(`${s.vuelos} ${s.vuelos === 1 ? 'vuelo' : 'vuelos'}`)
  return parts.join(' · ')
}

// Desglose de lo analizado en la fase: "34 de 120 img · 90 RGB · 30 térmicas".
// El total y el reparto solo aparecen cuando el pipeline los ha anunciado.
function statsLine(s) {
  if (!s) return ''
  if (s.vuelos != null) return indiceStatsLine(s)
  const parts = []
  // `done` no existe en todos los payloads (p.ej. el de Índice, cubierto
  // arriba); sin este `?? s.total` el Math.min da NaN en pantalla.
  if (s.total > 0) parts.push(`${Math.min(s.done ?? s.total, s.total)} de ${s.total} img`)
  else if (s.done > 0) parts.push(`${s.done} img`)
  if (s.rgb > 0 && s.rgb !== s.done) parts.push(`${s.rgb} RGB`)
  if (s.termica > 0 && s.termica !== s.done) {
    parts.push(`${s.termica} ${s.termica === 1 ? 'térmica' : 'térmicas'}`)
  }
  return parts.join(' · ')
}

// Velocidad de procesado en formato español: coma decimal, una cifra.
function fmtImgPorSegundo(v) {
  if (v == null) return ''
  return `${v.toFixed(1).replace('.', ',')} img/s`
}

// ETA de la fase activa a partir de su progreso y momento de arranque. Vacío
// hasta que hay señal suficiente (progreso >=5% y >=3s transcurridos) para no
// mostrar estimaciones absurdas nada más arrancar la fase. Heurística de
// cliente: solo se usa de FALLBACK cuando el backend no manda `eta_segundos`
// (ver `etaLine`).
function etaText(startedAt, progress, now) {
  if (startedAt == null || progress < 5) return ''
  const elapsed = (now - startedAt) / 1000
  if (elapsed < 3) return ''
  const eta = (elapsed * (100 - progress)) / progress
  return `queda ~${fmtDur(Math.round(eta))}`
}

// ETA + velocidad reales del backend (`eta_segundos`/`img_por_segundo` en
// `atom_core/apply.py`), con la heurística de cliente como fallback en las
// fases que no los emiten (p.ej. Índice).
function etaLine(stats, startedAt, progress, now) {
  if (stats && stats.eta_segundos != null) {
    const parts = [`~${fmtDur(stats.eta_segundos)} restantes`]
    const vel = fmtImgPorSegundo(stats.img_por_segundo)
    if (vel) parts.push(vel)
    return parts.join(' · ')
  }
  return etaText(startedAt, progress, now)
}

// Megabytes legibles: pasa a GB cuando supera 1024 MB.
function fmtMB(mb) {
  if (mb == null) return ''
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

// Velocidad de disco: un decimal si es menor de 10 MB/s, entero si no.
function fmtMBps(v) {
  if (v == null) return ''
  return v < 10 ? `${v.toFixed(1)} MB/s` : `${Math.round(v)} MB/s`
}

// Línea discreta de recursos por fase: "48 MB/s · CPU 21%".
function recursosLine(r) {
  if (!r) return ''
  const parts = []
  if (r.mb_por_segundo != null) parts.push(fmtMBps(r.mb_por_segundo))
  if (r.cpu_pct != null) parts.push(`CPU ${Math.round(r.cpu_pct)}%`)
  return parts.join(' · ')
}

// Decimal en formato español (coma en vez de punto).
function fmtDecimalEs(v, decimals) {
  if (v == null) return ''
  return v.toFixed(decimals).replace('.', ',')
}

// Texto destacado del veredicto de cuello de botella. Sin `live` es el
// resumen breve del run ya terminado (detalle aparte en `veredictoDetalle`).
// Con `live` (payload de `recursos_vivo`) lleva las cifras incrustadas, tal
// cual las pidió Rodrigo, porque se pinta sola mientras el run sigue en curso.
function veredictoTexto(v, live) {
  if (!live) {
    if (v === 'disco') return '⚠ Cuello de botella: disco'
    if (v === 'cpu') return '⚠ Cuello de botella: CPU'
    if (v === 'mixto') return 'Cuello de botella: mixto (disco y CPU)'
    return ''
  }
  const { mb_por_segundo, cpu_pct, nucleos, tipo_disco } = live
  if (v === 'disco') {
    const disco = tipo_disco === 'HDD' || tipo_disco === 'SSD' ? ` (${tipo_disco})` : ''
    return `⚠ El disco es el cuello de botella — ${fmtDecimalEs(mb_por_segundo, 1)} MB/s${disco}`
  }
  if (v === 'cpu') {
    return `⚠ La CPU es el cuello de botella — ${Math.round(cpu_pct)}% de ${nucleos} núcleos`
  }
  if (v === 'mixto') {
    return `Cuello de botella mixto (disco y CPU) — ${fmtDecimalEs(mb_por_segundo, 1)} MB/s · ${Math.round(cpu_pct)}% CPU`
  }
  return ''
}

// Detalle en pequeño bajo el veredicto: totales de disco y CPU del run.
function veredictoDetalle(r) {
  if (!r) return ''
  const parts = []
  if (r.mb_leidos != null) parts.push(`${fmtMB(r.mb_leidos)} leídos`)
  if (r.mb_escritos != null) parts.push(`${fmtMB(r.mb_escritos)} escritos`)
  if (r.mb_por_segundo != null) parts.push(fmtMBps(r.mb_por_segundo))
  if (r.cpu_pct != null) {
    parts.push(
      `CPU media ${Math.round(r.cpu_pct)}%${r.nucleos != null ? ` de ${r.nucleos} núcleos` : ''}`
    )
  }
  return parts.join(' · ')
}

// Resumen de rotación del run: qué se ha girado y en qué sentido.
// Cuando el criterio de yaw no manda girar NINGUNA imagen (hay plantas
// enteras así), la línea tiene que decirlo explícitamente en vez de dejar
// solo un "N sin girar" que se lee como si algo se hubiera girado.
export function rotLine(s) {
  if (!s) return ''
  const rot270 = s.rot270 || 0
  const rot90 = s.rot90 || 0
  const sinGirar = s.rot_none || 0
  const total = rot270 + rot90 + sinGirar
  if (total === 0) return ''
  if (rot270 === 0 && rot90 === 0) {
    return `sin giro · ${sinGirar} ${sinGirar === 1 ? 'imagen' : 'imágenes'} tal cual`
  }
  const parts = []
  if (rot270 > 0) parts.push(`${rot270} giradas 270°`)
  if (rot90 > 0) parts.push(`${rot90} giradas 90°`)
  if (sinGirar > 0) parts.push(`${sinGirar} sin girar`)
  return parts.join(' · ')
}

export default function ProgressModal({
  plant,
  phases,
  progress,
  stats,
  detail,
  finished,
  recursosTotales,
  maquina,
  onClose,
}) {
  const [showDetail, setShowDetail] = useState(false)
  const [now, setNow] = useState(Date.now())
  const hasActive = phases.some((p) => p.status === 'active')

  // Reloj para el ETA de la fase activa: solo corre mientras haya algo que
  // estimar, para no dejar un interval huérfano tras terminar.
  useEffect(() => {
    if (!hasActive || finished) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [hasActive, finished])

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card">
        <h2 className="pm-title">
          Procesando planta: <span className="pm-plant">{plant || '—'}</span>
        </h2>

        {/* Sonda inicial de la máquina (disco/CPU/RAM), antes de arrancar. */}
        {maquina && maquina.texto && (
          <p className={'pm-maquina' + (maquina.maquina_ocupada ? ' pm-maquina-ocupada' : '')}>
            {maquina.texto}
          </p>
        )}

        <ul className="pm-phases">
          {phases.length === 0 && (
            <li className="pm-phase pm-active">
              <span className="pm-ico pm-ico-spin">●</span>
              <span className="pm-name">Preparando…</span>
            </li>
          )}
          {phases.map((p, i) => (
            <li key={i} className={'pm-phase pm-' + p.status}>
              <span className={'pm-ico' + (p.status === 'active' ? ' pm-ico-spin' : '')}>
                {ICON[p.status]}
              </span>
              <span className="pm-name">{p.name}</span>
              {/* Tiempo de la fase (visible en cuanto se cierra). */}
              {p.duration != null && (
                <span className="pm-dur">{fmtDur(p.duration)}</span>
              )}
              {p.errors > 0 && (
                <span className="pm-errbadge">{p.errors} err</span>
              )}
              {/* Métricas de disco/CPU de la fase ya cerrada (discreto). */}
              {p.recursos && recursosLine(p.recursos) && (
                <div className="pm-recursos">{recursosLine(p.recursos)}</div>
              )}
              {p.status === 'active' && !finished && (
                <>
                  <div className="pm-sub">
                    <div className="pm-bar">
                      <div
                        className={
                          'pm-bar-fill' + (progress === 0 ? ' pm-bar-fill-indeterminate' : '')
                        }
                        style={progress > 0 ? { width: `${progress}%` } : undefined}
                      />
                    </div>
                    <span className="pm-pct">{progress > 0 ? `${progress}%` : '…'}</span>
                    {etaLine(stats, p.startedAt, progress, now) && (
                      <span className="pm-eta">{etaLine(stats, p.startedAt, progress, now)}</span>
                    )}
                  </div>
                  {statsLine(stats) ? (
                    <div className="pm-stats">{statsLine(stats)}</div>
                  ) : (
                    p.startedAt != null &&
                    (now - p.startedAt) / 1000 >= 3 && (
                      <div className="pm-stats">escaneando…</div>
                    )
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

        {/* Veredicto de cuello de botella EN VIVO (fase todavía en curso). */}
        {!finished && stats && stats.recursos_vivo && stats.recursos_vivo.veredicto && (
          <p className="pm-veredicto-vivo">
            {veredictoTexto(stats.recursos_vivo.veredicto, stats.recursos_vivo)}
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

        {/* Veredicto de cuello de botella (disco/CPU) del run completo. */}
        {finished && recursosTotales && recursosTotales.veredicto && (
          <div className="pm-veredicto">
            <p className="pm-veredicto-texto">
              {veredictoTexto(recursosTotales.veredicto)}
            </p>
            {veredictoDetalle(recursosTotales) && (
              <p className="pm-veredicto-detalle">
                {veredictoDetalle(recursosTotales)}
              </p>
            )}
          </div>
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
          {/* El cierre NUNCA se bloquea. Antes estaba `disabled` hasta que
              llegara `done`/`error`, así que si el backend dejaba de emitir
              (transporte roto, hilo muerto) el operador se quedaba encerrado en
              un modal mudo y tenía que matar la aplicación. Mientras corre, el
              botón avisa de que el proceso sigue por su cuenta. */}
          <button type="button" className="btn-run pm-close" onClick={onClose}>
            {finished ? 'Cerrar' : 'Cerrar (el proceso sigue en segundo plano)'}
          </button>
        </div>
      </div>
    </div>
  )
}
