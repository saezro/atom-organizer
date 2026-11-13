import { useState } from 'react'
import Paginador from './Paginador.jsx'

// Lista paginada de TRAMOS de fotos sin colocar. El tramo es la unidad: se elige entero
// (`seleccion` = id de tramo o null); no hay selección de fotos sueltas.
export const POR_PAGINA = 6

const rango = (t) => (t.ini ? `${t.ini.slice(0, 10)} ${t.ini.slice(11, 16)}-${t.fin.slice(11, 16)}` : 'sin hora de cámara')

export default function FotosLibres({ tramos, seleccion, onSeleccion }) {
  const [pagina, setPagina] = useState(0)
  const total = Math.max(1, Math.ceil(tramos.length / POR_PAGINA))
  const p = Math.min(pagina, total - 1)
  const visibles = tramos.slice(p * POR_PAGINA, (p + 1) * POR_PAGINA)
  const nFotos = tramos.reduce((a, t) => a + t.n, 0)

  if (!tramos.length) return <p className="ri-vacio">No quedan fotos sin colocar.</p>
  return (
    <div className="ri-libres" data-testid="ri-libres">
      <div className="ri-libres-cab">
        <span>{nFotos} fotos sin colocar en {tramos.length} tramos</span>
      </div>
      <div className="ri-libres-cuerpo">
        <ul className="ri-libres-lista">
          {visibles.map((t) => (
            <li key={t.id} className={seleccion === t.id ? 'ri-libre sel' : 'ri-libre'}>
              <button type="button" className="ri-tramo-fila" aria-pressed={seleccion === t.id} data-testid={`ri-tramo-${t.id}`}
                      onClick={() => onSeleccion(seleccion === t.id ? null : t.id)}>
                <span className="ri-libre-nombre">Tramo {rango(t)}</span>
                <span className="ri-libre-tipo">{t.n} fotos</span>
              </button>
            </li>
          ))}
        </ul>
        <Paginador pagina={p} totalPaginas={total} onPagina={setPagina} tactil={false} testidPrefijo="ri-pag-" contexto="tramos sin colocar" />
      </div>
    </div>
  )
}
