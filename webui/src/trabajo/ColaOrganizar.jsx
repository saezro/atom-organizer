import { useCallback, useEffect, useState } from 'react'
import { api, onProgress } from '../bridge'

// Cola de «Organizar» (`atom_core/cola_organizar.py`): los trabajos se procesan uno
// tras otro en esta misma app. Aquí se ven, se quitan, se reintentan y se cancela el
// que está en curso (sin vaciar la cola). La lista se refresca con los eventos
// `atom:progress` kind `cola` (cambio de estado) y `done`/`error` (fin de un run).

const ETIQUETA = {
  pendiente: 'Pendiente',
  en_curso: 'En curso',
  hecho: 'Hecho',
  error: 'Error',
  cancelado: 'Cancelado',
  interrumpido: 'Interrumpido',
}

const ETIQUETA_SUBIDA = {
  encolada: 'Subida del resultado en cola',
  omitida: 'No se sube el resultado',
  error: 'Fallo al encolar la subida',
}

// Línea de subida de un trabajo: solo si lleva `subir_al_terminar` (los viejos sin el campo no suben).
function textoSubida(j) {
  if (j.subir_al_terminar !== true) return null
  if (j.subida?.estado) {
    const t = ETIQUETA_SUBIDA[j.subida.estado] || j.subida.estado
    return { texto: j.subida.detalle && j.subida.estado !== 'encolada' ? `${t}: ${j.subida.detalle}` : t, aviso: j.subida.estado !== 'encolada' }
  }
  if (['cancelado', 'error', 'interrumpido'].includes(j.estado)) return { texto: 'No se subirá (no terminó bien)', aviso: false }
  return { texto: 'Se subirá a la nube al terminar', aviso: false }
}

const nombre = (ruta) => String(ruta || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || String(ruta || '')

export default function ColaOrganizar() {
  const [jobs, setJobs] = useState([])
  const [pausada, setPausada] = useState(false)
  const [falloCarga, setFalloCarga] = useState('')
  const [falloAccion, setFalloAccion] = useState('')
  const [avisos, setAvisos] = useState([])

  const cargar = useCallback(async () => {
    try {
      const r = await api.colaListar()
      if (r && r.ok === false) { setFalloCarga(r.error || 'No se pudo leer la cola.'); return }
      setFalloCarga('')
      setJobs(Array.isArray(r?.jobs) ? r.jobs : [])
      setPausada(!!r?.pausada)
      if (Array.isArray(r?.avisos) && r.avisos.length) {
        setAvisos((prev) => [...prev, ...r.avisos.filter((a) => !prev.includes(a))])
      }
    } catch (e) {
      setFalloCarga(String(e?.message || e || 'No se pudo leer la cola.'))
    }
  }, [])

  useEffect(() => {
    cargar()
    return onProgress((d) => {
      if (d.kind === 'cola' || d.kind === 'done' || d.kind === 'error') cargar()
    })
  }, [cargar])

  async function accion(fn, id) {
    try {
      const r = await fn(id)
      setFalloAccion(r && r.ok === false ? (r.error || 'No se pudo completar la acción.') : '')
    } catch (e) {
      setFalloAccion(String(e?.message || e || 'No se pudo completar la acción.'))
    }
    cargar()
  }

  const fallo = falloAccion || falloCarga
  if (jobs.length === 0 && !fallo && avisos.length === 0) return null

  const hayPendientes = jobs.some((j) => j.estado === 'pendiente')

  return (
    <div className="field cola-organizar" data-testid="cola-organizar">
      <span className="field-label">Cola de trabajos</span>
      {pausada && hayPendientes && (
        <div className="field-row">
          <span className="field-hint hint-warn" role="status">
            La cola está en pausa: hay trabajos pendientes de la sesión anterior.
          </span>
          <button type="button" className="btn-ghost" onClick={() => accion(() => api.colaReanudar())}>
            Reanudar cola
          </button>
        </div>
      )}
      {avisos.map((a) => (
        <span key={a} className="field-hint hint-warn" role="alert">{a}</span>
      ))}
      {fallo && <span className="field-hint hint-error" role="alert">{fallo}</span>}
      <ul className="cola-lista">
        {jobs.map((j) => {
          const p = j.params || {}
          const aviso = j.estado === 'hecho' && j.error
          const subida = textoSubida(j)
          return (
            <li key={j.id} className={`cola-item cola-${j.estado}`} data-testid={`cola-job-${j.id}`}>
              <div className="cola-info">
                <span className="cola-titulo">
                  {nombre(p.origen || p.input_folder)} → {nombre(p.destino || p.output_folder)}
                </span>
                <span className={`cola-estado cola-estado-${j.estado}`}>{ETIQUETA[j.estado] || j.estado}</span>
                {j.resumen && <span className="field-hint">{j.resumen}</span>}
                {subida && (
                  <span className={`field-hint ${subida.aviso ? 'hint-warn' : ''}`} data-testid={`cola-subida-${j.id}`}>{subida.texto}</span>
                )}
                {j.error && (
                  <span className={`field-hint ${aviso ? 'hint-warn' : 'hint-error'}`}>{j.error}</span>
                )}
              </div>
              <div className="cola-acciones">
                {j.estado === 'en_curso' && (
                  <button type="button" className="btn-ghost" onClick={() => accion(() => api.runCancelar())}>
                    Cancelar este
                  </button>
                )}
                {['error', 'cancelado', 'interrumpido'].includes(j.estado) && (
                  <button type="button" className="btn-ghost" onClick={() => accion(api.colaReintentar, j.id)}>
                    Reintentar
                  </button>
                )}
                {j.estado !== 'en_curso' && (
                  <button type="button" className="btn-ghost" onClick={() => accion(api.colaQuitar, j.id)}>
                    Quitar
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
