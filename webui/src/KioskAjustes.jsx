import { useEffect, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import { api } from './bridge.js'

// Version kiosco de `ConfigScreen` (App.jsx): misma config persistente
// (ruta de ThermoViewer + % de recorte RGB por modelo de dron), pero en el
// patron de pantalla aislada del kiosco (header con "Atras" + titulo, botones
// grandes tocables) en vez de un formulario de escritorio con raton.
export default function KioskAjustes({ tactil, onVolver }) {
  const [cargando, setCargando] = useState(true)
  const [ruta, setRuta] = useState('')
  const [models, setModels] = useState([]) // [{model, pct}]
  const [mName, setMName] = useState('')
  const [mPct, setMPct] = useState('')
  const [guardando, setGuardando] = useState(false)
  const [ok, setOk] = useState(false)
  const [error, setError] = useState('')

  // Carga inicial de la config persistente, igual que ConfigScreen.
  useEffect(() => {
    let vivo = true
    api
      .readConfig()
      .then((c) => {
        if (!vivo) return
        setRuta(c?.ruta_thermoviewer || '')
        const pbm = c?.percentage_by_models || {}
        setModels(Object.entries(pbm).map(([model, pct]) => ({ model, pct: String(pct) })))
      })
      .catch(() => {})
      .finally(() => { if (vivo) setCargando(false) })
    return () => { vivo = false }
  }, [])

  async function elegirRuta() {
    const path = await api.pickFile().catch(() => null)
    if (path) setRuta(path)
  }

  // Mismo formato que el Qt: modelo en MAYUSCULAS, actualiza si ya existe.
  function anadirModelo() {
    const name = mName.trim().toUpperCase()
    const pct = parseInt(mPct, 10)
    if (!name || Number.isNaN(pct)) return
    setModels((prev) => {
      const i = prev.findIndex((m) => m.model === name)
      if (i >= 0) {
        const next = [...prev]
        next[i] = { model: name, pct: String(pct) }
        return next
      }
      return [...prev, { model: name, pct: String(pct) }]
    })
    setMName('')
    setMPct('')
    setOk(false)
  }

  function quitarModelo(model) {
    setModels((prev) => prev.filter((m) => m.model !== model))
    setOk(false)
  }

  // Mismo shape que espera `write_config`, calcado de ConfigScreen.save().
  async function guardar() {
    const percentage_by_models = {}
    for (const m of models) {
      const pct = parseInt(m.pct, 10)
      if (m.model && !Number.isNaN(pct)) percentage_by_models[m.model] = pct
    }
    setGuardando(true)
    setOk(false)
    setError('')
    try {
      const res = await api.writeConfig({ ruta_thermoviewer: ruta, percentage_by_models })
      if (res?.ok) {
        setOk(true)
      } else {
        setError(res?.error || 'No se pudo guardar.')
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setGuardando(false)
    }
  }

  return (
    <div className="kiosk kiosk-ajustes">
      <div className="kiosk-header kiosk-header-paso">
        <BotonToque className="kiosk-atras" tactil={tactil} onActivar={onVolver}>
          ← Atrás
        </BotonToque>
        <span className="kiosk-titulo">Ajustes</span>
      </div>

      {cargando ? (
        <span className="kiosk-ajustes-cargando">Cargando…</span>
      ) : (
        <>
          <div className="kiosk-ajustes-seccion">
            <span className="kiosk-ajustes-label">ThermoViewer</span>
            <span className="kiosk-ajustes-ruta">{ruta || 'Sin definir'}</span>
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-thermo"
              onActivar={elegirRuta}
            >
              Elegir…
            </BotonToque>
          </div>

          <div className="kiosk-ajustes-seccion">
            <span className="kiosk-ajustes-label">% de recorte por dron</span>
            {models.length > 0 ? (
              <ul className="kiosk-ajustes-lista">
                {models.map((m) => (
                  <li key={m.model} className="kiosk-ajustes-item">
                    <span className="kiosk-ajustes-modelo">{m.model}</span>
                    <span className="kiosk-ajustes-pct">{m.pct}%</span>
                    <BotonToque
                      className="btn-ghost kiosk-btn"
                      tactil={tactil}
                      data-testid={`kiosk-ajustes-quitar-${m.model}`}
                      onActivar={() => quitarModelo(m.model)}
                    >
                      Quitar
                    </BotonToque>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="kiosk-ajustes-vacio">No hay modelos configurados todavía.</span>
            )}

            <div className="kiosk-ajustes-nuevo">
              <input
                className="glass-input"
                type="text"
                value={mName}
                placeholder="Modelo (p.ej. M4T)"
                onChange={(e) => setMName(e.target.value)}
              />
              <input
                className="glass-input"
                type="number"
                min="0"
                max="100"
                value={mPct}
                placeholder="%"
                onChange={(e) => setMPct(e.target.value)}
              />
              <BotonToque
                className="btn-ghost kiosk-btn"
                tactil={tactil}
                data-testid="kiosk-ajustes-anadir"
                onActivar={anadirModelo}
              >
                Añadir
              </BotonToque>
            </div>
          </div>

          <BotonToque
            className="btn kiosk-btn"
            tactil={tactil}
            data-testid="kiosk-ajustes-guardar"
            onActivar={guardar}
          >
            {guardando ? 'Guardando…' : 'Guardar'}
          </BotonToque>

          {ok && <span className="kiosk-ajustes-ok" data-testid="kiosk-ajustes-ok">Guardado</span>}
          {error && <span className="kiosk-ajustes-error" data-testid="kiosk-ajustes-error">{error}</span>}
        </>
      )}
    </div>
  )
}
