import { useEffect, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import BotonMantener from './BotonMantener.jsx'
import BotonAtras from './BotonAtras.jsx'
import { api } from './bridge.js'
import KioskRed from './KioskRed.jsx'
import MenuApps from './MenuApps.jsx'
import Paginador from './Paginador.jsx'

const PROPS_SVG = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: '2',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  width: '1.1em',
  height: '1.1em',
  'aria-hidden': 'true',
}

// Icono heredado de la app «Red» del launcher (apps/registry.js): dejó de
// ser una app suelta y pasó a ser una sección de Ajustes, así que el icono
// se mueve con ella en vez de duplicarse.
function IconoRed() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4.5 10a11 11 0 0115 0" />
      <path d="M7.5 13.2a7 7 0 019 0" />
      <path d="M10.5 16.4a3 3 0 013 0" />
      <circle cx="12" cy="19" r="0.75" fill="currentColor" stroke="none" />
    </svg>
  )
}

function IconoGeneral() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4 6h10" />
      <circle cx="17" cy="6" r="2.5" />
      <path d="M20 12H10" />
      <circle cx="7" cy="12" r="2.5" />
      <path d="M4 18h10" />
      <circle cx="17" cy="18" r="2.5" />
    </svg>
  )
}

// Cuántos modelos caben por página en la sección «General» sin empujar el
// botón Guardar fuera de los 320px de la Pi (mismo problema que resuelve la
// paginación de `KioskTareas`/`MenuApps`: en un panel resistivo no hay
// scroll de página, así que la lista se pagina en vez de crecer sin límite).
const MODELOS_POR_PAGINA = 2

// Catálogo de secciones de Ajustes: mismo patrón que `apps/registry.js`
// (id + nombre + icono), solo que aquí «abrir» es un estado local en vez de
// una `accion` de KioskScreen.
const SECCIONES = [
  { id: 'red', nombre: 'Red (WiFi)', Icono: IconoRed },
  { id: 'general', nombre: 'General', Icono: IconoGeneral },
]

// Pantalla «Ajustes» del kiosco: es un ÍNDICE de secciones (mismo patrón que
// `KioskScreen` con `accion`: estado local `seccion`, null = índice). La
// sección «Red (WiFi)» reutiliza `KioskRed` tal cual, embebida con su propio
// «← Atrás» que vuelve al índice en vez de al launcher. «General» es lo que
// esta pantalla ya hacía antes de tener secciones (ruta de ThermoViewer + %
// de recorte por modelo de dron), calcado de `ConfigScreen` (App.jsx).
export default function KioskAjustes({ tactil, onVolver }) {
  const [seccion, setSeccion] = useState(null)

  if (seccion === 'red') {
    return <KioskRed tactil={tactil} onVolver={() => setSeccion(null)} />
  }

  if (seccion === 'general') {
    return <SeccionGeneral tactil={tactil} onVolver={() => setSeccion(null)} />
  }

  return (
    <div className="kiosk kiosk-ajustes kiosk-ajustes-indice">
      <div className="kiosk-header kiosk-header-paso">
        <BotonAtras tactil={tactil} onActivar={onVolver} />
        <span className="kiosk-titulo">Ajustes</span>
      </div>
      <MenuApps
        apps={SECCIONES}
        tactil={tactil}
        onAbrir={setSeccion}
        porPagina={9}
        compacta
        testidPrefijo="kiosk-ajustes-seccion-"
      />
    </div>
  )
}

// Sección «General»: version kiosco de `ConfigScreen` (App.jsx): misma
// config persistente (ruta de ThermoViewer + % de recorte RGB por modelo de
// dron), pero en el patron de pantalla aislada del kiosco (header con
// "Atras" + titulo, botones grandes tocables) en vez de un formulario de
// escritorio con raton.
function SeccionGeneral({ tactil, onVolver }) {
  const [cargando, setCargando] = useState(true)
  const [ruta, setRuta] = useState('')
  const [models, setModels] = useState([]) // [{model, pct}]
  const [mName, setMName] = useState('')
  const [mPct, setMPct] = useState('')
  const [guardando, setGuardando] = useState(false)
  const [ok, setOk] = useState(false)
  const [error, setError] = useState('')
  const [paginaModelos, setPaginaModelos] = useState(0)

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

  // Página segura: si se quita un modelo y la página actual queda fuera de
  // rango, se recalcula aquí mismo en vez de con un efecto aparte.
  const totalPaginasModelos = Math.max(1, Math.ceil(models.length / MODELOS_POR_PAGINA))
  const paginaSegura = Math.min(paginaModelos, totalPaginasModelos - 1)
  const inicioModelos = paginaSegura * MODELOS_POR_PAGINA
  const modelosVisibles = models.slice(inicioModelos, inicioModelos + MODELOS_POR_PAGINA)

  return (
    <div className="kiosk kiosk-ajustes">
      <div className="kiosk-header kiosk-header-paso">
        <BotonAtras tactil={tactil} onActivar={onVolver} />
        <span className="kiosk-titulo">General</span>
      </div>

      {cargando ? (
        <span className="kiosk-ajustes-cargando">Cargando…</span>
      ) : (
        <>
          {/* Cuerpo con scroll propio (`.kiosk-cuerpo`, App.css): `.kiosk` es
              overflow:hidden, así que sin esto el formulario+lista de modelos
              tapaban el botón Guardar en vez de dejarle sitio fijo abajo. */}
          <div className="kiosk-cuerpo">
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
                <>
                  {/* Altura fija (no `flex:1`, ya lo da `.kiosk-ajustes-seccion`
                      con `min-height:0`): dentro de un card de altura auto un
                      `flex:1` sin límite se comía el resto de la sección. */}
                  <div className="kiosk-pag-wrap kiosk-ajustes-pag-wrap">
                    <ul className="kiosk-ajustes-lista">
                      {modelosVisibles.map((m) => (
                        <li key={m.model} className="kiosk-ajustes-item">
                          <span className="kiosk-ajustes-modelo">{m.model}</span>
                          <span className="kiosk-ajustes-pct">{m.pct}%</span>
                          {/* Destructivo: borra el % configurado del modelo sin
                              deshacer. Mantener pulsado (BotonMantener), no un
                              toque — el panel resistivo da toques fantasma y
                              ya se ha perdido un modelo asi por accidente. */}
                          <BotonMantener
                            className="btn-ghost kiosk-btn"
                            tactil={tactil}
                            data-testid={`kiosk-ajustes-quitar-${m.model}`}
                            onActivar={() => quitarModelo(m.model)}
                            aria-label={`Quitar ${m.model}`}
                          >
                            Quitar
                          </BotonMantener>
                        </li>
                      ))}
                    </ul>
                    <Paginador
                      pagina={paginaSegura}
                      totalPaginas={totalPaginasModelos}
                      onPagina={setPaginaModelos}
                      tactil={tactil}
                      testidPrefijo="kiosk-ajustes-modelos-"
                      contexto="modelos"
                    />
                  </div>
                </>
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
          </div>

          {/* `.kiosk-acciones` es flex ROW (App.css): el `flex:1` de `.kiosk-btn`
              da ancho aquí, no alto como pasaba colgando directo de `.kiosk`
              (flex column), que es lo que hacía crecer el botón hasta comerse
              la pantalla. */}
          <div className="kiosk-acciones">
            <BotonToque
              className="btn kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-guardar"
              onActivar={guardar}
            >
              {guardando ? 'Guardando…' : 'Guardar'}
            </BotonToque>
          </div>

          {ok && <span className="kiosk-ajustes-ok" data-testid="kiosk-ajustes-ok">Guardado</span>}
          {error && <span className="kiosk-ajustes-error" data-testid="kiosk-ajustes-error">{error}</span>}
        </>
      )}
    </div>
  )
}
