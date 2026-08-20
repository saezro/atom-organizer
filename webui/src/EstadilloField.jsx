import { useState } from 'react'
import { api } from './bridge'
import BotonToque from './pulsacion.jsx'
import BotonMantener from './BotonMantener.jsx'

// Cuántos estadillos caben por página cuando se usa en el kiosco (`tactil`):
// el panel resistivo de la Pi no tiene scroll de página (`.kiosk-estadillo`
// vive dentro de `.kiosk`, que es `overflow:hidden`), así que igual que la
// lista de modelos de `KioskAjustes` la lista se pagina (▲/▼ + N/M, mismo
// patrón que `KioskTareas`) en vez de crecer, para que "Organizar" y la
// barra de progreso de debajo queden siempre alcanzables. En escritorio
// (`tactil` falsy) no cambia nada: se sigue pintando la lista entera.
const ESTADILLOS_POR_PAGINA = 2

// Campo "Estadillo": lista ORDENADA de ficheros CSV/XLSX (antes solo se podía
// elegir uno). Con 0 o 1 fichero se ve y se comporta exactamente igual que el
// campo simple de siempre; la lista con quitar/reordenar solo aparece a partir
// del segundo, para no complicar el caso normal.
//
// El orden importa: aguas abajo la regla es "gana el primero" cuando dos
// estadillos cubren la misma imagen, así que el operador puede reordenarlos.
//
// `value` es siempre un array de rutas (puede estar vacío). `onChange` recibe
// el array nuevo completo.
export default function EstadilloField({ value, onChange, disabled, tactil }) {
  const files = value || []
  const [pagina, setPagina] = useState(0)

  async function elegir(indexAReemplazar) {
    const path = await api.pickFile()
    if (!path) return
    if (indexAReemplazar == null) {
      onChange([...files, path])
    } else {
      onChange(files.map((f, i) => (i === indexAReemplazar ? path : f)))
    }
  }

  function escribir(i, texto) {
    onChange(files.map((f, idx) => (idx === i ? texto : f)))
  }

  function quitar(i) {
    onChange(files.filter((_, idx) => idx !== i))
  }

  function mover(i, delta) {
    const j = i + delta
    if (j < 0 || j >= files.length) return
    const next = [...files]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }

  // Caso simple (0 o 1 fichero): una sola fila, como el campo de toda la vida.
  if (files.length <= 1) {
    const path = files[0] ?? ''
    return (
      <div className="field">
        <span className="field-label">Estadillo (opcional)</span>
        <div className="field-row">
          <input
            className="glass-input"
            type="text"
            value={path}
            placeholder="Si se indica, organiza por planta"
            disabled={disabled}
            readOnly={tactil}
            spellCheck={false}
            // En kiosco (`tactil`) no se puede escribir a mano: la selección
            // va solo por "Elegir…" (lista de ficheros detectados). En
            // escritorio se sigue pudiendo teclear la ruta tal cual siempre.
            onChange={
              tactil
                ? undefined
                : (e) => {
                    const t = e.target.value
                    onChange(t ? [t] : [])
                  }
            }
          />
          <button
            type="button"
            className="btn-ghost"
            disabled={disabled}
            onClick={() => elegir(path ? 0 : null)}
          >
            Elegir…
          </button>
        </div>
        {path && (
          <button
            type="button"
            className="link-inline estad-add"
            disabled={disabled}
            onClick={() => elegir(null)}
          >
            + Añadir otro estadillo
          </button>
        )}
      </div>
    )
  }

  // Varios ficheros: lista con quitar y reordenar (arriba = mayor prioridad).
  // En kiosco (`tactil`) se pagina para no desbordar `.kiosk-estadillo`; en
  // escritorio se sigue pintando la lista completa, tal cual antes.
  const totalPaginasEstad = Math.max(1, Math.ceil(files.length / ESTADILLOS_POR_PAGINA))
  const paginaSeguraEstad = Math.min(pagina, totalPaginasEstad - 1)
  const inicioEstad = tactil ? paginaSeguraEstad * ESTADILLOS_POR_PAGINA : 0
  const finEstad = tactil ? inicioEstad + ESTADILLOS_POR_PAGINA : files.length
  const filasVisibles = files.map((path, i) => ({ path, i })).slice(inicioEstad, finEstad)
  const conPaginacionEstad = tactil && files.length > ESTADILLOS_POR_PAGINA

  return (
    <div className="field">
      <span className="field-label">Estadillos (gana el primero de la lista)</span>
      <ul className="estad-list">
        {filasVisibles.map(({ path, i }) => (
          <li key={i} className="estad-item">
            <span className="estad-order" title="Prioridad de este estadillo">
              {i + 1}º
            </span>
            <input
              className="glass-input"
              type="text"
              value={path}
              disabled={disabled}
              readOnly={tactil}
              spellCheck={false}
              // Mismo criterio que en el campo simple: en kiosco no se
              // teclea, solo "Elegir…"; en escritorio no cambia nada.
              onChange={tactil ? undefined : (e) => escribir(i, e.target.value)}
            />
            <button
              type="button"
              className="btn-ghost"
              disabled={disabled}
              onClick={() => elegir(i)}
            >
              Elegir…
            </button>
            <span className="estad-move">
              <button
                type="button"
                className="estad-arrow"
                disabled={disabled || i === 0}
                title="Subir prioridad"
                onClick={() => mover(i, -1)}
              >
                ▲
              </button>
              <button
                type="button"
                className="estad-arrow"
                disabled={disabled || i === files.length - 1}
                title="Bajar prioridad"
                onClick={() => mover(i, 1)}
              >
                ▼
              </button>
            </span>
            {/* Destructivo: quita el estadillo de la lista sin deshacer. En
                kiosco exige mantener pulsado (BotonMantener); en escritorio
                (tactil falsy) sigue siendo un click normal, como antes. */}
            <BotonMantener
              className="config-del"
              tactil={tactil}
              title="Quitar este estadillo"
              aria-label="Quitar este estadillo"
              data-testid={`estad-quitar-${i}`}
              disabled={disabled}
              onActivar={() => quitar(i)}
            >
              ✕
            </BotonMantener>
          </li>
        ))}
      </ul>
      {conPaginacionEstad && (
        <div className="estad-nav">
          <BotonToque
            className="estad-nav-btn"
            tactil={tactil}
            disabled={disabled || paginaSeguraEstad <= 0}
            onActivar={() => setPagina((p) => Math.max(0, p - 1))}
            data-testid="estad-pag-arriba"
            aria-label="Página anterior de estadillos"
          >
            ▲
          </BotonToque>
          <span className="estad-pagina" data-testid="estad-pagina">
            {paginaSeguraEstad + 1}/{totalPaginasEstad}
          </span>
          <BotonToque
            className="estad-nav-btn"
            tactil={tactil}
            disabled={disabled || paginaSeguraEstad >= totalPaginasEstad - 1}
            onActivar={() => setPagina((p) => Math.min(totalPaginasEstad - 1, p + 1))}
            data-testid="estad-pag-abajo"
            aria-label="Página siguiente de estadillos"
          >
            ▼
          </BotonToque>
        </div>
      )}
      <button
        type="button"
        className="link-inline estad-add"
        disabled={disabled}
        onClick={() => elegir(null)}
      >
        + Añadir otro estadillo
      </button>
      <span className="field-hint">
        Se organiza contra todos a la vez. Si dos estadillos cubren la misma imagen, gana el
        de arriba: reordénalos con las flechas si hace falta.
      </span>
    </div>
  )
}
