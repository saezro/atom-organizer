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
// Dos vistas locales (lista / formulario) porque en 480x320 no cabe una
// lista de 11 tareas + su formulario en la misma pantalla, y el panel es
// resistivo: nada de <select> nativo (intocable con el dedo) ni de scroll
// largo (el arrastre no es fiable), así que la lista se pagina igual que
// `MenuApps.jsx`.
import { useState } from 'react'
import { api } from './bridge.js'
import BotonToque from './pulsacion.jsx'
import BotonAtras from './BotonAtras.jsx'
import { SECTIONS } from './schema.js'
import { initialState, buildParams } from './TaskBlock.jsx'

const POR_PAGINA = 4

// Aplana SECTIONS en una lista de filas paginables: separadores de sección
// intercalados con las tareas. Así la paginación no tiene que saber nada de
// la forma del schema, solo recorrer un array plano.
function filasDeSecciones() {
  const filas = []
  for (const clave of Object.keys(SECTIONS)) {
    const seccion = SECTIONS[clave]
    filas.push({ tipo: 'separador', key: `sep-${clave}`, label: seccion.label })
    for (const block of seccion.blocks) {
      filas.push({ tipo: 'tarea', key: block.task, block })
    }
  }
  return filas
}

export default function KioskTareas({ tactil, busy, onEjecutar, onVolver }) {
  const [tareaElegida, setTareaElegida] = useState(null)
  const [pagina, setPagina] = useState(0)

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

  const filas = filasDeSecciones()
  const totalPaginas = Math.max(1, Math.ceil(filas.length / POR_PAGINA))
  const inicio = pagina * POR_PAGINA
  const visibles = filas.slice(inicio, inicio + POR_PAGINA)
  const conPaginacion = filas.length > POR_PAGINA

  return (
    <div className="kiosk kiosk-tareas">
      <div className="kiosk-header kiosk-header-paso">
        <BotonAtras tactil={tactil} onActivar={onVolver} />
        <span className="kiosk-titulo">Tareas</span>
      </div>
      <div className="kiosk-tareas-lista">
        {visibles.map((fila) =>
          fila.tipo === 'separador' ? (
            <span key={fila.key} className="kiosk-tareas-separador">
              {fila.label}
            </span>
          ) : (
            <BotonToque
              key={fila.key}
              className="btn kiosk-btn kiosk-tareas-item"
              tactil={tactil}
              data-testid={`kiosk-tarea-${fila.block.task}`}
              onActivar={() => setTareaElegida(fila.block)}
            >
              {fila.block.title}
            </BotonToque>
          )
        )}
      </div>
      {conPaginacion && (
        <div className="kiosk-tareas-nav">
          <BotonToque
            className="kiosk-tareas-nav-btn"
            tactil={tactil}
            disabled={pagina <= 0}
            onActivar={() => setPagina((p) => Math.max(0, p - 1))}
            data-testid="kiosk-tareas-arriba"
            aria-label="Página anterior de tareas"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 15l6-6 6 6" />
            </svg>
          </BotonToque>
          <span className="kiosk-tareas-pagina" data-testid="kiosk-tareas-pagina">
            {pagina + 1}/{totalPaginas}
          </span>
          <BotonToque
            className="kiosk-tareas-nav-btn"
            tactil={tactil}
            disabled={pagina >= totalPaginas - 1}
            onActivar={() => setPagina((p) => Math.min(totalPaginas - 1, p + 1))}
            data-testid="kiosk-tareas-abajo"
            aria-label="Página siguiente de tareas"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </BotonToque>
        </div>
      )}
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
