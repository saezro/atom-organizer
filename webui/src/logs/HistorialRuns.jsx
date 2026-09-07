// Historial de procesos: runs anteriores de `atom_core.organize.run_task`,
// agrupados por planta/inspección, con acceso a su log completo. Vive dentro
// de Ajustes (ver `ConfigScreen` en App.jsx) porque es diagnóstico, no un
// flujo de trabajo — no se toca `TrabajoScreen`/`ProgressModal` para nada.
import { useEffect, useState } from 'react'
import { api } from '../bridge'
import { conPlazo } from '../plazo'

const PLAZO_MS = 15000
const SIN_PLANTA = 'Sin identificar'

function formatoFecha(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('es-ES', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function formatoDuracion(segundos) {
  if (segundos == null) return '—'
  const s = Number(segundos)
  if (Number.isNaN(s)) return '—'
  if (s < 60) return `${s.toFixed(1)} s`
  const min = Math.floor(s / 60)
  const rest = Math.round(s % 60)
  return `${min} min ${rest} s`
}

const MARCA_ESTADO = { ok: '✓', error: '✗', incompleto: '⚠' }
const TEXTO_ESTADO = { ok: 'Completado', error: 'Con errores', incompleto: 'Incompleto' }

// Agrupa por planta preservando el orden de llegada (ya viene ordenado por
// fecha desc desde el backend); "Sin identificar" siempre al final.
function agruparPorPlanta(runs) {
  const grupos = new Map()
  for (const run of runs) {
    const clave = run.planta || SIN_PLANTA
    if (!grupos.has(clave)) grupos.set(clave, [])
    grupos.get(clave).push(run)
  }
  const entradas = [...grupos.entries()]
  entradas.sort((a, b) => {
    if (a[0] === SIN_PLANTA) return 1
    if (b[0] === SIN_PLANTA) return -1
    return 0
  })
  return entradas
}

export default function HistorialRuns({ onVolver }) {
  const [runs, setRuns] = useState(null) // null = cargando
  const [error, setError] = useState('')
  const [seleccionado, setSeleccionado] = useState(null) // nombre del log abierto
  const [detalle, setDetalle] = useState(null) // {texto, truncado, bytes} | null
  const [detalleError, setDetalleError] = useState('')
  const [cargandoDetalle, setCargandoDetalle] = useState(false)
  const [carpeta, setCarpeta] = useState(null) // {ruta} | {error}

  useEffect(() => {
    let vivo = true
    setError('')
    conPlazo(api.logsListar(), PLAZO_MS)
      .then((res) => {
        if (!vivo) return
        if (res?.ok) {
          setRuns(res.runs || [])
        } else {
          setRuns([])
          setError(res?.error || 'No se pudo leer el historial.')
        }
      })
      .catch((e) => {
        if (!vivo) return
        setRuns([])
        setError(String(e))
      })
    return () => {
      vivo = false
    }
  }, [])

  async function abrir(run) {
    setSeleccionado(run.nombre)
    setDetalle(null)
    setDetalleError('')
    setCargandoDetalle(true)
    try {
      const res = await conPlazo(api.logsLeer(run.nombre), PLAZO_MS)
      if (res?.ok) {
        setDetalle(res)
      } else {
        setDetalleError(res?.error || 'No se pudo leer el log.')
      }
    } catch (e) {
      setDetalleError(String(e))
    } finally {
      setCargandoDetalle(false)
    }
  }

  function volverALista() {
    setSeleccionado(null)
    setDetalle(null)
    setDetalleError('')
  }

  async function abrirCarpeta() {
    try {
      const res = await conPlazo(api.logsCarpeta(), PLAZO_MS)
      setCarpeta(res?.ok ? { ruta: res.ruta } : { error: res?.error || 'No se pudo obtener la carpeta.' })
    } catch (e) {
      setCarpeta({ error: String(e) })
    }
  }

  if (seleccionado) {
    return (
      <div className="hist-detalle">
        <div className="hist-detalle-cab">
          <button type="button" className="btn-ghost" onClick={volverALista}>
            ← Volver al historial
          </button>
          <span className="hist-detalle-nombre">{seleccionado}</span>
        </div>
        {cargandoDetalle ? (
          <span className="field-hint">Cargando…</span>
        ) : detalleError ? (
          <span className="field-hint hint-warn">{detalleError}</span>
        ) : (
          <>
            {detalle?.truncado && (
              <span className="field-hint hint-warn">
                Log muy grande: se muestra solo la última parte ({detalle.bytes} bytes en total).
              </span>
            )}
            <pre className="pm-log hist-log">{detalle?.texto || ''}</pre>
          </>
        )}
      </div>
    )
  }

  const grupos = runs ? agruparPorPlanta(runs) : []

  return (
    <div className="hist-lista">
      <div className="hist-cab">
        {onVolver && (
          <button type="button" className="btn-ghost" onClick={onVolver}>
            ← Volver
          </button>
        )}
        <button type="button" className="btn-ghost" onClick={abrirCarpeta}>
          Abrir carpeta de logs
        </button>
      </div>
      {carpeta?.ruta && <span className="field-hint hint-ok">Carpeta: {carpeta.ruta}</span>}
      {carpeta?.error && <span className="field-hint hint-warn">{carpeta.error}</span>}

      {runs === null ? (
        <span className="field-hint">Cargando…</span>
      ) : error ? (
        <span className="field-hint hint-warn">{error}</span>
      ) : runs.length === 0 ? (
        <span className="field-hint">Todavía no hay procesos registrados.</span>
      ) : (
        grupos.map(([planta, items]) => (
          <div key={planta} className="hist-grupo">
            <h3 className="hist-grupo-titulo">{planta}</h3>
            <ul className="hist-runs">
              {items.map((run) => (
                <li key={run.nombre}>
                  <button
                    type="button"
                    className="hist-run"
                    onClick={() => abrir(run)}
                    title={run.nombre}
                  >
                    <span className={`hist-marca hist-marca-${run.estado}`}>
                      {MARCA_ESTADO[run.estado] || '?'}
                    </span>
                    <span className="hist-run-info">
                      <span className="hist-run-fecha">{formatoFecha(run.fecha)}</span>
                      <span className="hist-run-task">{run.task}</span>
                    </span>
                    <span className="hist-run-meta">
                      <span>{TEXTO_ESTADO[run.estado] || run.estado}</span>
                      <span>{run.errores > 0 ? `${run.errores} error(es)` : ''}</span>
                      <span>{formatoDuracion(run.duracion)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  )
}
