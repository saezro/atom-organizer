import { useEffect, useState } from 'react'
import { api } from '../bridge'
import InspeccionSelector from '../InspeccionSelector'

const NUEVA = '__nueva__'

export default function PasoInspeccion({ ready, prefijo, onChange, disabled }) {
  const [catalogo, setCatalogo] = useState(null) // {ok, inspecciones[], origen, error}
  const [eleccion, setEleccion] = useState(prefijo || '') // prefijo elegido | NUEVA | ''
  const [nueva, setNueva] = useState('') // nombre tecleado si eleccion === NUEVA

  const inspecciones = catalogo?.inspecciones || []
  const elegida = inspecciones.find((i) => i.prefijo === eleccion) || null

  async function cargarInspecciones() {
    try {
      setCatalogo(await api.cloudInspecciones())
    } catch (e) {
      setCatalogo({ ok: false, inspecciones: [], error: String(e) })
    }
  }

  useEffect(() => {
    if (ready) {
      cargarInspecciones()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])

  // Cambiar de inspección cambia el destino, así que el plan anterior (y
  // demás estado dependiente de la inspección) lo invalida quien escuche
  // `onChange` — este componente solo se ocupa de la elección en sí.
  function elegir(valor) {
    setEleccion(valor)
    if (valor === NUEVA) {
      onChange('', null)
    } else {
      onChange(valor, inspecciones.find((i) => i.prefijo === valor) || null)
    }
  }

  return (
    <div className="field">
      <span className="field-label">Inspección</span>
      {/* Una inspección ya elegida se enseña como un hecho, no como un
          desplegable abierto: lo normal es acertar a la primera y seguir. El
          buscador solo aparece cuando hace falta buscar. */}
      {eleccion && eleccion !== NUEVA ? (
        <div className="field-row">
          <input className="glass-input" type="text" value={elegida?.etiqueta || eleccion} readOnly />
          <button type="button" className="btn-ghost" onClick={() => elegir('')} disabled={disabled}>
            Cambiar
          </button>
        </div>
      ) : (
        <InspeccionSelector
          inspecciones={inspecciones}
          onElegir={elegir}
          onNueva={() => elegir(NUEVA)}
          ocupado={disabled}
          onActualizar={cargarInspecciones}
        />
      )}
      {eleccion === NUEVA && (
        <input
          className="glass-input"
          type="text"
          value={nueva}
          onChange={(e) => {
            const v = e.target.value
            setNueva(v)
            onChange(v.trim(), null)
          }}
          placeholder="Empresa--Planta--Año--Tipo"
        />
      )}
      <span className="field-hint">
        {catalogo?.error
          ? catalogo.error
          : catalogo?.origen === 'cache'
            ? `${inspecciones.length} inspecciones de la última descarga (no se pudo consultar ahora).`
            : catalogo?.origen === 'bucket'
              // Respaldo: la Suite no respondió y esta lista se genera a mano,
              // así que puede no traer las inspecciones creadas hoy. Decirlo
              // evita que el operador busque una que existe y no aparece.
              ? `${inspecciones.length} inspecciones de la lista de respaldo (puede estar desactualizada).`
              : `${inspecciones.length} inspecciones. Los datos se guardarán en «${prefijo || '…'}/».`}
      </span>
    </div>
  )
}
