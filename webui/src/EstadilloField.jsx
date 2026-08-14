import { api } from './bridge'

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
export default function EstadilloField({ value, onChange, disabled }) {
  const files = value || []

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
            spellCheck={false}
            onChange={(e) => {
              const t = e.target.value
              onChange(t ? [t] : [])
            }}
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
  return (
    <div className="field">
      <span className="field-label">Estadillos (gana el primero de la lista)</span>
      <ul className="estad-list">
        {files.map((path, i) => (
          <li key={i} className="estad-item">
            <span className="estad-order" title="Prioridad de este estadillo">
              {i + 1}º
            </span>
            <input
              className="glass-input"
              type="text"
              value={path}
              disabled={disabled}
              spellCheck={false}
              onChange={(e) => escribir(i, e.target.value)}
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
            <button
              type="button"
              className="config-del"
              title="Quitar este estadillo"
              disabled={disabled}
              onClick={() => quitar(i)}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
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
