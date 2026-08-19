import { useState } from 'react'

// Orden de fases: primero las que hay que subir YA (Vuelo recién hecho,
// Preparación en marcha), luego el resto del ciclo de vida, y al final
// Terminado/Cancelada — que además se ocultan por defecto (ver `mostrarTerminadas`
// más abajo). Una fase que no está en esta lista (o viene vacía) se coloca justo
// antes de Terminado/Cancelada: ni se esconde ni compite con las prioritarias.
const ORDEN_FASES = [
  'Vuelo',
  'Preparacion',
  'Confirmada',
  'Por_confirmar',
  'Analisis',
  'Informes',
  'Revision',
]
const FASES_OCULTABLES = ['Terminado', 'Cancelada']

function posicionFase(fase) {
  const i = ORDEN_FASES.indexOf(fase)
  if (i !== -1) return i
  // Fase desconocida o vacía: después de las conocidas, antes de Terminado/Cancelada.
  if (fase === 'Terminado') return ORDEN_FASES.length + 1
  if (fase === 'Cancelada') return ORDEN_FASES.length + 2
  return ORDEN_FASES.length
}

const normaliza = (s) =>
  (s || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')

const MAX_RESULTADOS = 50

// Selector de inspección para «Subir al bucket»: buscador + chips de fase +
// toggle de terminadas + lista agrupada por año (desc) y ordenada por fase
// dentro de cada año. Deliberadamente sin portal ni posicionamiento absoluto:
// es una lista inline, igual que antes.
export default function InspeccionSelector({
  inspecciones,
  onElegir,
  onNueva,
  ocupado,
  onActualizar,
  // Opt-in para dejar SOLO la lista: en la Pi (480x320) el buscador + chips +
  // checkbox se comían toda la pantalla y la lista quedaba fuera de vista
  // (reporte Cas probando en la Pi real). Con `soloLista` no se pinta nada de
  // eso (ni el botón "Actualizar lista": esa vía queda en la cabecera del
  // kiosco, ver KioskScreen). El filtrado sigue exactamente igual por debajo
  // (sin texto, sin fases activas, sin terminadas) porque el estado no
  // cambia, solo se deja de pintar sus controles. Por defecto en `false` para
  // no tocar el comportamiento de escritorio (BucketScreen).
  soloLista = false,
}) {
  const [texto, setTexto] = useState('')
  const [fasesActivas, setFasesActivas] = useState([])
  const [mostrarTerminadas, setMostrarTerminadas] = useState(false)

  const palabras = normaliza(texto).split(/\s+/).filter(Boolean)

  const casaTexto = (i) => {
    if (!palabras.length) return true
    const heno = normaliza(
      `${i.empresa || ''} ${i.planta || ''} ${i.anio || ''} ${i.tipo || ''} ${i.fase || ''} ${i.prefijo || ''} ${i.etiqueta || ''}`
    )
    return palabras.every((p) => heno.includes(p))
  }

  // Chips de fase disponibles, con su recuento, calculados sobre TODAS las
  // inspecciones que casan con el texto (no solo las visibles tras el toggle):
  // el operario tiene que poder filtrar por «Terminado» aunque el toggle esté
  // apagado, y ver cuántas hay de cada fase con independencia de si se enseñan.
  const porTexto = inspecciones.filter(casaTexto)
  const conteoFases = {}
  for (const i of porTexto) {
    const f = i.fase || ''
    conteoFases[f] = (conteoFases[f] || 0) + 1
  }
  const fasesPresentes = Object.keys(conteoFases).sort(
    (a, b) => posicionFase(a) - posicionFase(b)
  )

  function toggleFase(fase) {
    setFasesActivas((prev) =>
      prev.includes(fase) ? prev.filter((f) => f !== fase) : [...prev, fase]
    )
  }

  // Solo filtran las fases que el texto actual deja sobre la mesa. Sin esto, un
  // chip marcado que deja de aparecer al reescribir la búsqueda («acme» con
  // Vuelo marcado → «acme 2024», donde no hay ninguna en Vuelo) seguiría
  // filtrando desde la sombra: la lista saldría vacía, sin chip visible que
  // desmarcar y sin explicación. Se conserva en el estado, así que si la fase
  // reaparece el chip vuelve activo.
  const fasesEfectivas = fasesActivas.filter((f) => f in conteoFases)
  const casaFase = (i) => !fasesEfectivas.length || fasesEfectivas.includes(i.fase || '')
  const esOcultable = (i) => FASES_OCULTABLES.includes(i.fase)

  const candidatas = porTexto.filter(casaFase)
  const visibles = mostrarTerminadas ? candidatas : candidatas.filter((i) => !esOcultable(i))
  const ocultasPorTerminadas = candidatas.length - visibles.length

  // Orden final: fase (según ORDEN_FASES) y, a igualdad, etiqueta alfabética.
  // El corte a 50 va DESPUÉS de ordenar por fase (para que las 50 primeras sean
  // las de mayor prioridad) y ANTES de agrupar por año.
  const ordenadas = [...visibles].sort((a, b) => {
    const pf = posicionFase(a.fase) - posicionFase(b.fase)
    if (pf !== 0) return pf
    return (a.etiqueta || '').localeCompare(b.etiqueta || '')
  })
  const total = ordenadas.length
  const recortadas = ordenadas.slice(0, MAX_RESULTADOS)

  // Agrupado por año, descendente. `anio` puede venir number o string; se
  // normaliza a string para usarla como clave y como etiqueta de cabecera.
  const porAnio = new Map()
  for (const i of recortadas) {
    const anio = String(i.anio || '—')
    if (!porAnio.has(anio)) porAnio.set(anio, [])
    porAnio.get(anio).push(i)
  }
  // Años numéricos primero y descendentes; cualquier año corrupto o ausente
  // («—», ''…) va al final en bloque. Comparar las cadenas crudas colaría un
  // «N/A» por delante de 2026.
  const esNumerico = (s) => s !== '' && !Number.isNaN(Number(s))
  const grupos = [...porAnio.entries()].sort((a, b) => {
    const na = esNumerico(a[0])
    const nb = esNumerico(b[0])
    if (na !== nb) return na ? -1 : 1
    if (!na) return a[0].localeCompare(b[0])
    return Number(b[0]) - Number(a[0])
  })

  return (
    <>
      {!soloLista && (
        <div className="field-row">
          <input
            className="glass-input"
            type="text"
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            placeholder="Escribe para buscar: empresa, planta, año…"
            disabled={ocupado}
            autoComplete="off"
          />
          <button type="button" className="btn-ghost" onClick={onActualizar} disabled={ocupado}>
            Actualizar lista
          </button>
        </div>
      )}

      {!soloLista && fasesPresentes.length > 0 && (
        <div className="insp-chips">
          {fasesPresentes.map((fase) => (
            <button
              key={fase || '(sin fase)'}
              type="button"
              className={`insp-chip${fasesActivas.includes(fase) ? ' insp-chip-activo' : ''}`}
              aria-pressed={fasesActivas.includes(fase)}
              onClick={() => toggleFase(fase)}
              disabled={ocupado}
            >
              {fase || 'Sin fase'} ({conteoFases[fase]})
            </button>
          ))}
        </div>
      )}

      {!soloLista && (
        <label className="check insp-toggle-terminadas">
          <input
            type="checkbox"
            checked={mostrarTerminadas}
            onChange={(e) => setMostrarTerminadas(e.target.checked)}
            disabled={ocupado}
          />
          Mostrar terminadas y canceladas
        </label>
      )}

      <ul className="insp-list">
        {grupos.map(([anio, items]) => (
          <li key={anio} className="insp-grupo-anio">
            <span className="insp-anio">{anio}</span>
            <ul className="insp-list-anio">
              {items.map((i) => (
                <li key={i.prefijo}>
                  <button
                    type="button"
                    className="insp-item"
                    onClick={() => onElegir(i.prefijo)}
                    disabled={ocupado}
                  >
                    {i.etiqueta}
                  </button>
                </li>
              ))}
            </ul>
          </li>
        ))}
        {total === 0 && (
          <li className="insp-vacio">
            {soloLista
              ? 'No hay inspecciones disponibles. Pulsa el botón de actualizar.'
              : !texto
                ? 'Ninguna inspección coincide con los filtros. Prueba a cambiarlos o crea una nueva.'
                : `Ninguna inspección coincide con «${texto}». Comprueba el nombre o crea una nueva.`}
          </li>
        )}
        {/* El kiosco (`soloLista`) no crea inspecciones: pasa un `onNueva`
            vacío, así que el botón sería una diana muerta. */}
        {!soloLista && (
          <li>
            <button type="button" className="insp-item insp-nueva" onClick={onNueva} disabled={ocupado}>
              + Inspección nueva…
            </button>
          </li>
        )}
      </ul>

      {!soloLista && total > MAX_RESULTADOS && (
        <span className="field-hint">
          {total} coinciden; se muestran las {MAX_RESULTADOS} primeras. Escribe más para acotar.
        </span>
      )}
      {!soloLista && ocultasPorTerminadas > 0 && (
        <span className="field-hint hint-warn">
          {ocultasPorTerminadas} terminada{ocultasPorTerminadas === 1 ? '' : 's'} oculta
          {ocultasPorTerminadas === 1 ? '' : 's'} — activa «Mostrar terminadas y canceladas» para verla
          {ocultasPorTerminadas === 1 ? '' : 's'}.
        </span>
      )}
    </>
  )
}
