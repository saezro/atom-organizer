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

// Icono genérico: fallback si algún día se añade un `task` al schema sin
// entrada en `ICONOS_TAREA` (nunca debe quedar una card sin icono).
function IconoGenerica() {
  return (
    <svg {...PROPS_SVG}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
  )
}

// Térmica · extracción (do_extraction): saca temperaturas de las imágenes
// térmicas del vuelo — termómetro con escala.
function IconoExtraccion() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M12 14.5V5a2 2 0 10-4 0v9.5a4 4 0 104 0z" />
      <path d="M10 8h1M10 11h1" />
    </svg>
  )
}

// Procesamiento RGB (rgb_aerotools): compresión + renombrado + geotag de las
// fotos en color del vuelo — cámara.
function IconoRgb() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1V9a1 1 0 011-1z" />
      <circle cx="12" cy="13" r="3.5" />
    </svg>
  )
}

// Rotación (gen_thumbnails_aerotools / gen_thumbnails): endereza las
// imágenes según el yaw del vuelo — flecha circular.
function IconoRotacion() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4 12a8 8 0 0113.66-5.66L20 8" />
      <path d="M20 4v4h-4" />
    </svg>
  )
}

// Geoetiquetado manual (manual_geotagging): asigna coordenadas a mano a
// partir del log — pin de mapa.
function IconoGeoetiquetado() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M12 21s7-6.3 7-11.5A7 7 0 105 9.5C5 14.7 12 21 12 21z" />
      <circle cx="12" cy="9.5" r="2.2" />
    </svg>
  )
}

// Comprimir imágenes (compress_image): reduce el peso del archivo — flechas
// hacia dentro.
function IconoComprimir() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4 4l6 6M4 4v4M4 4h4" />
      <path d="M20 20l-6-6M20 20v-4M20 20h-4" />
    </svg>
  )
}

// Generar meta y location (gen_meta_location): rellena metadatos EXIF/GPS —
// etiqueta con marca de posición.
function IconoMeta() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M3 12l8-8h8v8l-8 8z" />
      <circle cx="15" cy="9" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  )
}

// Estructura de carpetas · organizar (gen_struct): coloca cada imagen en su
// carpeta según el estadillo — carpeta.
function IconoEstructura() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
    </svg>
  )
}

// Renombrado de imágenes (rename_images): cambia el nombre de archivo por
// lote — etiqueta "Aa".
function IconoRenombrar() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M4 7h16M4 12h10M4 17h7" />
      <path d="M18 15l3 3-3 3" />
    </svg>
  )
}

// Convertir DJI → TIF (convert_to_tif): decodifica la térmica cruda del dron
// a TIF radiométrico — imagen con flecha de intercambio.
function IconoConvertirTif() {
  return (
    <svg {...PROPS_SVG}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M8 15l3-3 2 2 3-4" />
      <path d="M15 5v3M13 6.5h4" />
    </svg>
  )
}

// Recorte RGB (rgb_cropping): recorta el marco de las fotos RGB — esquinas
// de recorte.
function IconoRecorte() {
  return (
    <svg {...PROPS_SVG}>
      <path d="M6 3v14a2 2 0 002 2h14" />
      <path d="M18 21V7a2 2 0 00-2-2H2" />
    </svg>
  )
}

// Girar imágenes TIF (tif_rotating): rota un ángulo fijo (90/-90/180) las
// TIF ya convertidas — flecha de giro cerrada.
function IconoGiroTif() {
  return (
    <svg {...PROPS_SVG}>
      <rect x="5" y="5" width="14" height="14" rx="2" />
      <path d="M9 9l6 6M15 9l-6 6" />
    </svg>
  )
}

// Mapa `block.task` → icono, uno distinto por tarea real del schema (nunca
// compartido por sección, a diferencia del icono único por grupo de antes).
const ICONOS_TAREA = {
  do_extraction: IconoExtraccion,
  rgb_aerotools: IconoRgb,
  gen_thumbnails_aerotools: IconoRotacion,
  manual_geotagging: IconoGeoetiquetado,
  compress_image: IconoComprimir,
  gen_meta_location: IconoMeta,
  gen_struct: IconoEstructura,
  gen_thumbnails: IconoRotacion,
  rename_images: IconoRenombrar,
  convert_to_tif: IconoConvertirTif,
  rgb_cropping: IconoRecorte,
  tif_rotating: IconoGiroTif,
}

// Aplana SECTIONS en una única lista de apps para `MenuApps`: cada tarea
// lleva SU icono propio (`ICONOS_TAREA`), no el de su sección de origen.
function appsDeSecciones() {
  const apps = []
  for (const clave of Object.keys(SECTIONS)) {
    const seccion = SECTIONS[clave]
    for (const block of seccion.blocks) {
      const Icono = ICONOS_TAREA[block.task] || IconoGenerica
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
      {/* `.kiosk-acciones` es flex ROW: sin ella el `flex:1` de `.kiosk-btn`
          cuelga directo de `.kiosk-tareas` (flex COLUMN) y crece en ALTO
          hasta comerse la pantalla (causa diagnosticada en App.css). */}
      <div className="kiosk-acciones">
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
