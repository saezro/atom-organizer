import { useEffect, useRef, useState } from 'react'
import { api } from '../bridge'
import InspeccionSelector from '../InspeccionSelector'

const NUEVA = '__nueva__'

export default function PasoInspeccion({ ready, prefijo, carpeta, estadilloRutas, onChange, disabled, reloadToken }) {
  const [catalogo, setCatalogo] = useState(null) // {ok, inspecciones[], origen, error}
  const [eleccion, setEleccion] = useState(prefijo || '') // prefijo elegido | NUEVA | ''
  const [nueva, setNueva] = useState('') // nombre tecleado si eleccion === NUEVA

  // Sugerencia automática por carpeta + estadillo (ver `inspeccion_sugerir`).
  // `eleccionManual` se marca en cualquier interacción del usuario: desde ahí
  // la autodetección no vuelve a tocar la elección. `sugerenciaRef` guarda la
  // clave `carpeta|estadillo` ya resuelta para no repetir la consulta.
  const [sugerencia, setSugerencia] = useState(null) // {estado, candidatos, motivo?, mensaje?, auto?}
  const eleccionManual = useRef(Boolean(prefijo))
  const sugerenciaRef = useRef(null)
  const eleccionRef = useRef(eleccion)
  eleccionRef.current = eleccion
  const eleccionAutoRef = useRef(false)

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
    // `reloadToken` es el aviso de `TrabajoScreen` tras un login desde cero
    // en `PanelSubida` (el catálogo vive aquí, ese panel no puede recargarlo
    // directamente): cambia de valor y basta para disparar este mismo
    // efecto, sin tocar `eleccion`/`prefijo` ya elegidos.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, reloadToken])

  const claveEstadillo = (estadilloRutas || []).join('|')
  // Si cambia la CARPETA, una elección que vino de la autodetección ya no es
  // fiable: se limpia y se vuelve a consultar. Una elección manual no se toca
  // nunca. Si solo cambia el estadillo se re-pide la sugerencia (efecto de
  // abajo) pero NO se borra la elección: limpiarla cambiaba `prefijo`, eso
  // reseteaba PasoEstadillo, que cambiaba el estadillo... y vuelta al bucle.
  const carpetaAnterior = useRef(carpeta)
  useEffect(() => {
    if (carpetaAnterior.current === carpeta) return
    carpetaAnterior.current = carpeta
    if (sugerencia?.auto && !eleccionManual.current && eleccionRef.current) {
      setEleccion('')
      setSugerencia(null)
      sugerenciaRef.current = null
      eleccionAutoRef.current = false
      // El efecto de sugerencia corre en este mismo commit, antes del render
      // que actualiza `eleccionRef`: sin esto vería la elección vieja y no
      // autodetectaría la carpeta nueva.
      eleccionRef.current = ''
      onChange('', null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [carpeta])
  useEffect(() => {
    if (!carpeta || !catalogo?.ok || eleccionManual.current) return
    // Con elección ya hecha solo se re-consulta si fue automática.
    if (eleccionRef.current && !eleccionAutoRef.current) return
    const clave = `${carpeta}|${claveEstadillo}`
    if (sugerenciaRef.current === clave) return
    sugerenciaRef.current = clave
    if (!eleccionRef.current) setSugerencia(null)
    let cancelado = false
    ;(async () => {
      try {
        const r = await api.inspeccionSugerir(carpeta, estadilloRutas || [])
        // No pisar una decisión manual tomada mientras la consulta estaba en vuelo.
        if (cancelado || eleccionManual.current) return
        if (eleccionRef.current && !eleccionAutoRef.current) return
        if (r?.estado === 'unica' && r.prefijo) {
          // Mismo prefijo: no tocar nada (cambiarlo reseteaba PasoEstadillo).
          if (r.prefijo !== eleccionRef.current) elegirAuto(r.prefijo)
          setSugerencia({ ...r, auto: true })
        } else if (!eleccionRef.current) {
          setSugerencia(r || null)
        }
        // Ya no es única pero hay elección automática: se deja (limpiarla
        // reabriría el ciclo) y se mantiene el aviso de preseleccionada.
      } catch (e) {
        if (!cancelado && !eleccionRef.current) setSugerencia({ estado: 'error', mensaje: String(e?.message || e) })
      }
    })()
    return () => {
      cancelado = true
      // Cancelada a medias (cambio de carpeta/estadillo): la clave no queda
      // como resuelta, para que volver a esta combinación la consulte de nuevo.
      if (sugerenciaRef.current === clave) sugerenciaRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [carpeta, claveEstadillo, catalogo])

  // Elección hecha por la autodetección: no cuenta como manual.
  function elegirAuto(valor) {
    eleccionAutoRef.current = true
    eleccionRef.current = valor
    setEleccion(valor)
    onChange(valor, inspecciones.find((i) => i.prefijo === valor) || null)
  }

  // Cambiar de inspección cambia el destino, así que el plan anterior (y
  // demás estado dependiente de la inspección) lo invalida quien escuche
  // `onChange` — este componente solo se ocupa de la elección en sí.
  function elegir(valor) {
    eleccionManual.current = true
    eleccionAutoRef.current = false
    setSugerencia((s) => (s?.auto ? { ...s, auto: false } : s))
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
      {sugerencia && !eleccion && sugerencia.estado === 'varias' && (
        <span className="field-hint">
          Varias inspecciones encajan con la carpeta, elige una:{' '}
          {sugerencia.candidatos.map((c) => inspecciones.find((i) => i.prefijo === c)?.etiqueta || c).join(' · ')}
        </span>
      )}
      {sugerencia && !eleccion && sugerencia.estado === 'ninguna' && (
        <span className="field-hint">No se ha podido deducir la inspección de la carpeta, elígela a mano.</span>
      )}
      {sugerencia && !eleccion && sugerencia.estado === 'conflicto' && (
        <span className="field-hint">{sugerencia.motivo || 'La carpeta y el estadillo no coinciden.'} Elige la inspección a mano.</span>
      )}
      {sugerencia?.auto && eleccion && eleccion !== NUEVA && (
        <span className="field-hint">Preseleccionada por la carpeta, puedes cambiarla.</span>
      )}
      {catalogo?.error && (
        <span className="field-hint hint-error" role="alert">{catalogo.error}</span>
      )}
      <span className="field-hint">
        {catalogo?.error
          ? null
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
