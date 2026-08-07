// Aviso de actualización. Espeja el flujo del atom-migrador: la app comprueba
// sola al arrancar (3 s), si hay versión nueva sale este modal, el usuario
// acepta, se descarga con progreso y al terminar se lanza el instalador
// silencioso — que cierra esta instancia y la reabre ya actualizada.
//
// Fases: 'available' → 'downloading' → 'ready' → 'installing' | 'error'.
import { useEffect, useState } from 'react'
import { api, onUpdate } from './bridge'

function mb(bytes) {
  return `${(Number(bytes || 0) / 1024 / 1024).toFixed(0)} MB`
}

export default function UpdateModal() {
  const [info, setInfo] = useState(null)      // resultado de check_update
  const [phase, setPhase] = useState('available')
  const [pct, setPct] = useState(0)
  const [done, setDone] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => onUpdate((d) => {
    if (d.kind === 'available') { setInfo(d.data); setPhase('available') }
    else if (d.kind === 'progress') { setPct(d.value || 0); setDone(d.done || 0) }
    else if (d.kind === 'downloaded') { setPhase('ready') }
    else if (d.kind === 'error') { setError(d.text || 'Error desconocido'); setPhase('error') }
  }), [])

  if (!info) return null

  const size = info.asset_size || 0

  const start = async () => {
    setError(''); setPct(0); setPhase('downloading')
    const res = await api.downloadUpdate(info.asset_url, size)
    if (res && res.started === false) { setError(res.reason || ''); setPhase('error') }
  }

  const install = async () => {
    setPhase('installing')
    const res = await api.installUpdate()
    if (!res || !res.ok) { setError((res && res.error) || 'No se pudo instalar'); setPhase('error') }
    // Si va bien no hay que hacer nada: el instalador cierra la app.
  }

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card up-card">
        <h2 className="pm-title">
          Nueva versión disponible: <span className="pm-plant">v{info.latest}</span>
        </h2>
        <p className="up-sub">Tienes la v{info.current}.</p>

        {info.notes && <pre className="up-notes">{info.notes}</pre>}

        {phase === 'downloading' && (
          <div className="up-progress">
            <div className="up-bar">
              <div className="up-bar-fill" style={{ '--up-pct': pct / 100 }} />
            </div>
            <span className="up-pct">{pct}% · {mb(done)} de {mb(size)}</span>
          </div>
        )}

        {/* Nada de prometer el reinicio como un hecho: la reapertura la hace la
            entrada [Run] del instalador y en la corrida de Daniel (2026-08-04) no
            ocurrió. Decir «volverá a abrirse solo» y que no pase deja al usuario
            creyendo que la actualización falló. Se avisa del plan B. */}
        {phase === 'ready' && (
          <p className="up-sub">
            Descarga completa. Al instalar, ATOM Organizer se cerrará y debería volver
            a abrirse solo. Si no lo hace, ábrelo desde el acceso directo de siempre.
          </p>
        )}
        {phase === 'installing' && (
          <p className="up-sub">
            Instalando… ATOM Organizer se cerrará. Si no vuelve a abrirse solo en unos
            segundos, ábrelo desde el acceso directo de siempre.
          </p>
        )}
        {phase === 'error' && <p className="pm-status err">✗ {error}</p>}

        {!info.can_install && (
          <p className="up-sub">
            La instalación automática sólo está disponible en Windows. Descarga la nueva
            versión desde la página de la release.
          </p>
        )}

        <div className="up-actions">
          <button className="btn-ghost" onClick={() => setInfo(null)}
                  disabled={phase === 'downloading' || phase === 'installing'}>
            Ahora no
          </button>
          {info.can_install && (phase === 'available' || phase === 'error') && (
            <button className="btn-run" onClick={start}>
              Descargar e instalar{size ? ` (${mb(size)})` : ''}
            </button>
          )}
          {info.can_install && phase === 'ready' && (
            <button className="btn-run" onClick={install}>Instalar y reiniciar</button>
          )}
        </div>
      </div>
    </div>
  )
}
