// Pantalla "Tareas" del kiosco: da acceso a las 11 tareas sueltas que hoy
// solo existen en el escritorio (`schema.js`, secciones AEROTOOLS y OTROS),
// para poder lanzarlas desde el panel táctil de la Pi (480x320 px).
//
// Recorre `SECTIONS` en vez de listar tareas a mano: si mañana se añade un
// block al schema, aparece solo aquí sin tocar este fichero. Por el mismo
// motivo se reusan `initialState`/`buildParams` de `TaskBlock.jsx` en vez de
// reimplementar la construcción de params — es la MISMA lógica que ya valida
// el escritorio, y duplicarla habría sido una segunda fuente de bugs.
//
// El índice de tareas usa la MISMA `MenuApps` que el índice de secciones de
// Ajustes (rejilla 3x3, cards idénticas): sin separadores de sección, porque
// una rejilla no admite un "hueco" a mitad de fila y con separadores algunas
// páginas dejaban la sección OTROS casi vacía. Las 11 tareas se distinguen
// por icono (AEROTOOLS / OTROS) en vez de por epígrafe.
//
// Dos vistas locales (lista / formulario) porque en 480x320 no cabe una
// lista de 11 tareas + su formulario en la misma pantalla, y el panel es
// resistivo: nada de <select> nativo (intocable con el dedo).
import { useState } from 'react'
import { api } from './bridge.js'
import BotonToque from './pulsacion.jsx'
import BotonAtras from './BotonAtras.jsx'
import MenuApps from './MenuApps.jsx'
import { SECTIONS } from './schema.js'
import { initialState, buildParams } from './TaskBlock.jsx'

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

// Icono para las tareas de la sección AEROTOOLS (pipeline térmica/RGB propio).
function IconoAerotools() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4 17l6-10 4 6 3-4 3 8" />
      <circle cx="10" cy="7" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  )
}

// Icono para las tareas de OTROS EQUIPOS (utilidades genéricas de imagen).
function IconoOtros() {
  return (
    <svg {...PROPS_SVG}>
      <rect x="4" y="4" width="7" height="7" rx="1" />
      <rect x="13" y="4" width="7" height="7" rx="1" />
      <rect x="4" y="13" width="7" height="7" rx="1" />
      <rect x="13" y="13" width="7" height="7" rx="1" />
    </svg>
  )
}

// Aplana SECTIONS en una única lista de apps para `MenuApps`: cada tarea
// lleva el icono de su sección de origen, pero sin separador ni epígrafe (la
// rejilla 3x3 no tiene sitio para un hueco a mitad de fila).
function appsDeSecciones() {
  const apps = []
  for (const clave of Object.keys(SECTIONS)) {
    const seccion = SECTIONS[clave]
    const Icono = clave === 'aerotools' ? IconoAerotools : IconoOtros
    for (const block of seccion.blocks) {
      apps.push({ id: block.task, nombre: block.title, Icono, block })
    }
  }
  return apps
}

export default function KioskTareas({ tactil, busy, onEjecutar, onVolver }) {
  const [tareaElegida, setTareaElegida] = useState(null)

  if (tareaElegida) {
    return (
      <FormularioTarea
        tactil={tactil}
        busy={busy}
        block={tareaElegida}
        onEjecutar={onEjecutar}
        onVolver={() => setTareaElegida(null)}
      />
    )
  }

  const apps = appsDeSecciones()

  function abrir(id) {
    const app = apps.find((a) => a.id === id)
    if (app) setTareaElegida(app.block)
  }

  return (
    <div className="kiosk kiosk-tareas">
      <div className="kiosk-header kiosk-header-paso">
        <BotonAtras tactil={tactil} onActivar={onVolver} />
        <span className="kiosk-titulo">Tareas</span>
      </div>
      <MenuApps
        apps={apps}
        tactil={tactil}
        disabled={busy}
        onAbrir={abrir}
        porPagina={9}
        compacta
        testidPrefijo="kiosk-tarea-"
      />
    </div>
  )
}

// Formulario de una tarea elegida. Estado local por campo, igual que
// `TaskBlock`, pero pintado con `BotonToque` en vez de <select>/<button>
// nativos: en un panel resistivo el desplegable nativo no es manejable con
// el dedo, así que el `select` del schema se convierte en "tocar para rotar
// a la siguiente opción" (mismo truco que un ciclo de valores).
function FormularioTarea({ tactil, busy, block, onEjecutar, onVolver }) {
  const [state, setState] = useState(() => initialState(block.fields))
  const set = (name, value) => setState((s) => ({ ...s, [name]: value }))

  async function elegirRuta(field) {
    const path = field.type === 'file' ? await api.pickFile() : await api.pickFolder()
    if (path) set(field.name, path)
  }

  return (
    <div className="kiosk kiosk-tareas">
      <div className="kiosk-header kiosk-header-paso">
        <BotonAtras tactil={tactil} onActivar={onVolver} />
        <span className="kiosk-titulo">{block.title}</span>
      </div>
      <div className="kiosk-tareas-form">
        {block.fields.map((f) => (
          <CampoTarea
            key={f.name}
            f={f}
            value={state[f.name]}
            tactil={tactil}
            onSet={set}
            onElegirRuta={elegirRuta}
          />
        ))}
      </div>
      <BotonToque
        className="btn kiosk-btn kiosk-tareas-ejecutar"
        tactil={tactil}
        disabled={busy}
        data-testid="kiosk-tarea-ejecutar"
        onActivar={() => onEjecutar(block.task, buildParams(block.fields, state))}
      >
        {busy ? 'Procesando…' : 'Ejecutar'}
      </BotonToque>
    </div>
  )
}

function CampoTarea({ f, value, tactil, onSet, onElegirRuta }) {
  if (f.type === 'bool') {
    return (
      <div className="kiosk-tareas-campo">
        <span className="field-label">{f.label}</span>
        <BotonToque
          className="btn-ghost kiosk-btn"
          tactil={tactil}
          data-testid={`kiosk-campo-${f.name}`}
          onActivar={() => onSet(f.name, !value)}
        >
          {value ? 'Sí' : 'No'}
        </BotonToque>
      </div>
    )
  }
  if (f.type === 'select') {
    const indice = value ?? 0
    const opcion = f.options[indice] ?? f.options[0]
    return (
      <div className="kiosk-tareas-campo">
        <span className="field-label">{f.label}</span>
        <BotonToque
          className="btn-ghost kiosk-btn"
          tactil={tactil}
          data-testid={`kiosk-campo-${f.name}`}
          onActivar={() => onSet(f.name, (indice + 1) % f.options.length)}
        >
          {opcion?.label ?? '—'}
        </BotonToque>
      </div>
    )
  }
  if (f.type === 'number' || f.type === 'text') {
    return (
      <label className="kiosk-tareas-campo">
        <span className="field-label">{f.label}</span>
        <input
          className="kiosk-input"
          type={f.type === 'number' ? 'number' : 'text'}
          value={value}
          onChange={(e) => onSet(f.name, e.target.value)}
        />
      </label>
    )
  }
  // folder / file: el picker del bridge decide diálogo nativo o explorador
  // in-app según el modo (pywebview / servidor), este componente no lo sabe.
  return (
    <div className="kiosk-tareas-campo">
      <span className="field-label">{f.label}</span>
      <span className="kiosk-tareas-ruta">{value || 'Sin elegir'}</span>
      <BotonToque
        className="btn-ghost kiosk-btn"
        tactil={tactil}
        data-testid={`kiosk-campo-${f.name}`}
        onActivar={() => onElegirRuta(f)}
      >
        Elegir…
      </BotonToque>
    </div>
  )
}
