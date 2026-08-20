import { useEffect, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import BotonMantener from './BotonMantener.jsx'
import BotonAtras from './BotonAtras.jsx'
import TecladoPantalla from './TecladoPantalla.jsx'
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

// Recorte: un marco con la esquina tijereteada, para la entrada «% de recorte
// por dron» del índice de General.
function IconoRecorte() {
  return (
    <svg {...PROPS_SVG} width="1.6em" height="1.6em">
      <path d="M7 3v14h14" />
      <path d="M3 7h14v14" />
    </svg>
  )
}

function IconoCarpeta() {
  return (
    <svg {...PROPS_SVG} width="1.6em" height="1.6em">
      <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
    </svg>
  )
}

function IconoChevron() {
  return (
    <svg {...PROPS_SVG} width="1.3em" height="1.3em">
      <path d="M9 5l7 7-7 7" />
    </svg>
  )
}

// Papelera para «quitar modelo»: con texto, el boton se comia media fila y
// quedaba pegado a los controles de pagina (que estan justo al lado, no
// debajo: `.kiosk-pag-wrap` es flex ROW).
function IconoPapelera() {
  return (
    <svg {...PROPS_SVG} width="1.3em" height="1.3em">
      <path d="M4 7h16" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12" />
      <path d="M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
    </svg>
  )
}

function IconoMas() {
  return (
    <svg {...PROPS_SVG} width="1.6em" height="1.6em">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  )
}

function IconoMenos() {
  return (
    <svg {...PROPS_SVG} width="1.6em" height="1.6em">
      <path d="M5 12h14" />
    </svg>
  )
}

// La lista de modelos ya no comparte pantalla con el formulario ni con la
// ruta del ThermoViewer: tiene pantalla propia, así que caben más filas.
const MODELOS_POR_PAGINA = 4

// El % se ajusta a pasos de 5 con botones grandes en vez de teclearse: en el
// panel resistivo un <input type=number> no se puede rellenar (la Pi no tiene
// teclado físico) y el valor real siempre es redondo.
const PCT_PASO = 5
const PCT_DEFECTO = 20

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

// Sección «General»: versión kiosco de `ConfigScreen` (App.jsx): misma config
// persistente (ruta de ThermoViewer + % de recorte RGB por modelo de dron).
//
// Es a su vez un ÍNDICE con sub-pantallas (estado `vista`) en vez de un único
// formulario: metida entera en una pantalla, la lista de modelos sólo cabía de
// dos en dos y los dos <input> de «añadir» eran directamente inútiles en la Pi
// (sin teclado físico no hay forma de escribir en ellos). Ahora cada paso tiene
// la pantalla para él: lista paginada, teclado en pantalla para el nombre y
// botones -5/+5 para el porcentaje.
function SeccionGeneral({ tactil, onVolver }) {
  const [vista, setVista] = useState('indice')
  const [cargando, setCargando] = useState(true)
  const [ruta, setRuta] = useState('')
  const [models, setModels] = useState([]) // [{model, pct}]
  const [nuevoModelo, setNuevoModelo] = useState('')
  const [nuevoPct, setNuevoPct] = useState(PCT_DEFECTO)
  const [guardando, setGuardando] = useState(false)
  const [ok, setOk] = useState(false)
  const [error, setError] = useState('')
  const [paginaModelos, setPaginaModelos] = useState(0)
  // La lista de modelos es de solo lectura hasta pulsar «Editar»: las
  // papeleras quedaban al lado del paginador y en el panel resistivo se
  // borraba un modelo al cambiar de pagina. Añadir tambien vive aqui.
  const [editando, setEditando] = useState(false)

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
    const name = nuevoModelo.trim().toUpperCase()
    if (!name) return
    setModels((prev) => {
      const i = prev.findIndex((m) => m.model === name)
      const entrada = { model: name, pct: String(nuevoPct) }
      if (i >= 0) {
        const next = [...prev]
        next[i] = entrada
        return next
      }
      return [...prev, entrada]
    })
    setNuevoModelo('')
    setNuevoPct(PCT_DEFECTO)
    setOk(false)
    setEditando(true)
    setVista('modelos')
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

  // Guardar y el aviso de resultado son idénticos en todas las sub-pantallas:
  // la config es una sola, se persista desde donde se persista.
  const pieGuardar = (
    <>
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
  )

  if (cargando) {
    return (
      <div className="kiosk kiosk-ajustes">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={onVolver} />
          <span className="kiosk-titulo">General</span>
        </div>
        <span className="kiosk-ajustes-cargando">Cargando…</span>
      </div>
    )
  }

  // Paso 1 de «añadir»: nombre del modelo con el mismo teclado en pantalla que
  // la contraseña del WiFi. Separado del % porque juntos no caben: el teclado
  // se come casi toda la altura de los 320px.
  if (vista === 'nuevo-modelo') {
    return (
      <div className="kiosk kiosk-ajustes">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={() => setVista('modelos')} />
          <span className="kiosk-titulo">Modelo de dron</span>
        </div>
        <input
          className="kiosk-input kiosk-ajustes-campo"
          type="text"
          value={nuevoModelo.toUpperCase()}
          placeholder="p.ej. M4T"
          data-testid="kiosk-ajustes-nombre"
          readOnly
        />
        <TecladoPantalla tactil={tactil} valor={nuevoModelo} onValor={setNuevoModelo} />
        <div className="kiosk-acciones">
          <BotonToque
            className="btn kiosk-btn"
            tactil={tactil}
            disabled={nuevoModelo.trim().length === 0}
            data-testid="kiosk-ajustes-siguiente"
            onActivar={() => setVista('nuevo-pct')}
          >
            Siguiente
          </BotonToque>
        </div>
      </div>
    )
  }

  // Paso 2 de «añadir»: el porcentaje, a pasos de 5 y con targets enormes.
  if (vista === 'nuevo-pct') {
    const mover = (d) => setNuevoPct((p) => Math.min(100, Math.max(0, p + d)))
    return (
      <div className="kiosk kiosk-ajustes">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={() => setVista('nuevo-modelo')} />
          <span className="kiosk-titulo">{nuevoModelo.trim().toUpperCase()}</span>
        </div>
        <div className="kiosk-cuerpo kiosk-pct">
          <BotonToque
            className="kiosk-pct-btn"
            tactil={tactil}
            disabled={nuevoPct <= 0}
            data-testid="kiosk-ajustes-pct-menos"
            aria-label={`Bajar ${PCT_PASO} por ciento`}
            onActivar={() => mover(-PCT_PASO)}
          >
            <IconoMenos />
          </BotonToque>
          <span className="kiosk-pct-valor" data-testid="kiosk-ajustes-pct-valor">{nuevoPct}%</span>
          <BotonToque
            className="kiosk-pct-btn"
            tactil={tactil}
            disabled={nuevoPct >= 100}
            data-testid="kiosk-ajustes-pct-mas"
            aria-label={`Subir ${PCT_PASO} por ciento`}
            onActivar={() => mover(PCT_PASO)}
          >
            <IconoMas />
          </BotonToque>
        </div>
        <div className="kiosk-acciones">
          <BotonToque
            className="btn kiosk-btn"
            tactil={tactil}
            data-testid="kiosk-ajustes-anadir"
            onActivar={anadirModelo}
          >
            Añadir
          </BotonToque>
        </div>
      </div>
    )
  }

  // Página segura: si se quita un modelo y la página actual queda fuera de
  // rango, se recalcula aquí mismo en vez de con un efecto aparte.
  const totalPaginasModelos = Math.max(1, Math.ceil(models.length / MODELOS_POR_PAGINA))
  const paginaSegura = Math.min(paginaModelos, totalPaginasModelos - 1)
  const inicioModelos = paginaSegura * MODELOS_POR_PAGINA
  const modelosVisibles = models.slice(inicioModelos, inicioModelos + MODELOS_POR_PAGINA)

  if (vista === 'modelos') {
    return (
      <div className="kiosk kiosk-ajustes">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={() => { setEditando(false); setVista('indice') }} />
          <span className="kiosk-titulo">% de recorte</span>
        </div>

        {models.length > 0 ? (
          <div className="kiosk-pag-wrap">
            <ul className="kiosk-ajustes-lista">
              {modelosVisibles.map((m) => (
                <li key={m.model} className="kiosk-ajustes-item">
                  <span className="kiosk-ajustes-modelo">{m.model}</span>
                  <span className="kiosk-ajustes-pct">{m.pct}%</span>
                  {/* Destructivo: borra el % configurado del modelo sin
                      deshacer. Mantener pulsado (BotonMantener), no un
                      toque — el panel resistivo da toques fantasma y
                      ya se ha perdido un modelo asi por accidente. */}
                  {editando && <BotonMantener
                    className="kiosk-ajustes-quitar"
                    tactil={tactil}
                    data-testid={`kiosk-ajustes-quitar-${m.model}`}
                    onActivar={() => quitarModelo(m.model)}
                    aria-label={`Quitar ${m.model}`}
                    etiquetaConfirmar={<IconoPapelera />}
                  >
                    <IconoPapelera />
                  </BotonMantener>}
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
        ) : (
          <span className="kiosk-ajustes-vacio">No hay modelos configurados todavía.</span>
        )}

        <div className="kiosk-acciones">
          {editando || models.length === 0 ? (
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-nuevo"
              onActivar={() => setVista('nuevo-modelo')}
            >
              Añadir
            </BotonToque>
          ) : (
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-editar"
              onActivar={() => setEditando(true)}
            >
              Editar
            </BotonToque>
          )}
          {editando ? (
            <BotonToque
              className="btn kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-listo"
              onActivar={() => setEditando(false)}
            >
              Listo
            </BotonToque>
          ) : (
            <BotonToque
              className="btn kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-guardar"
              onActivar={guardar}
            >
              {guardando ? 'Guardando…' : 'Guardar'}
            </BotonToque>
          )}
        </div>
        {ok && <span className="kiosk-ajustes-ok" data-testid="kiosk-ajustes-ok">Guardado</span>}
        {error && <span className="kiosk-ajustes-error" data-testid="kiosk-ajustes-error">{error}</span>}
      </div>
    )
  }

  return (
    <div className="kiosk kiosk-ajustes">
      <div className="kiosk-header kiosk-header-paso">
        <BotonAtras tactil={tactil} onActivar={onVolver} />
        <span className="kiosk-titulo">General</span>
      </div>

      {/* Cuerpo con scroll propio (`.kiosk-cuerpo`, App.css): `.kiosk` es
          overflow:hidden, así que sin esto el contenido tapaba el botón
          Guardar en vez de dejarle sitio fijo abajo. */}
      <div className="kiosk-cuerpo">
        <div className="kiosk-ajustes-seccion">
          <span className="kiosk-ajustes-label">ThermoViewer</span>
          <span className="kiosk-ajustes-ruta">{ruta || 'Sin definir'}</span>
          <div className="kiosk-acciones">
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-ajustes-thermo"
              onActivar={elegirRuta}
            >
              <IconoCarpeta />
              Elegir…
            </BotonToque>
          </div>
        </div>

        <BotonToque
          className="kiosk-ajustes-entrada"
          tactil={tactil}
          data-testid="kiosk-ajustes-abrir-modelos"
          onActivar={() => setVista('modelos')}
        >
          <IconoRecorte />
          <span className="kiosk-ajustes-entrada-txt">
            <span>% de recorte por dron</span>
            <span className="kiosk-ajustes-label">
              {models.length === 1 ? '1 modelo' : `${models.length} modelos`}
            </span>
          </span>
          <IconoChevron />
        </BotonToque>
      </div>

      {pieGuardar}
    </div>
  )
}
