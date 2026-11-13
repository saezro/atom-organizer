import { useEffect, useMemo, useState } from 'react'
import CalendarioHoras from './CalendarioHoras.jsx'
import FotosLibres from './FotosLibres.jsx'
import { textoConfianza, etiquetaVuelo, tramosLibres, vueloQueContiene } from './calendarioHoras.js'

const distanciaTexto = (s) => (s == null ? '' : s < 120 ? `a ${s} s` : `a ${Math.round(s / 60)} min`)
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

const pendientes0 = (rs) => rs.filter((r) => !r.aplicada)

function ChipRecomendacion({ r }) {
  const destino = r.pb == null ? 'sin destino' : `→ PB${r.pb}/V${r.vuelo}`
  const partes = [destino, distanciaTexto(r.distancia_s), textoConfianza(r.confianza)].filter(Boolean)
  return (
    <li className={`ri-rec ri-conf-${r.confianza}`} data-testid={`ri-rec-${r.id}`}>
      <span className="ri-chip">{partes.join(' · ')}</span>
      <span className="ri-rec-n">{r.n_fotos} fotos</span>
      <span className="ri-rec-exp">{r.explicacion}</span>
      {r.aplicada ? <span className="ri-chip ri-chip-auto">aplicado automáticamente</span> : null}
    </li>
  )
}

export default function RevisionIndiceModal({ modelo, api, ahora = () => Date.now() }) {
  const [seleccion, setSeleccion] = useState(null)
  const [fila, setFila] = useState('')
  const [error, setError] = useState(null)
  const [ocupado, setOcupado] = useState(false)
  const [t, setT] = useState(() => ahora())
  useEffect(() => {
    const id = setInterval(() => setT(ahora()), 1000)
    return () => clearInterval(id)
  }, [ahora])

  const { vuelos, libres, recomendaciones, espera } = modelo
  const tramos = useMemo(() => tramosLibres(libres), [libres])
  const enVuelo = tramos.filter((t) => vueloQueContiene(t, vuelos))
  const tramo = tramos.find((t) => t.id === seleccion) ?? null
  const recTramo = tramo ? pendientes0(recomendaciones).find((r) => r.grupo_id === tramo.grupo_id) : null
  const pendientes = recomendaciones.filter((r) => !r.aplicada)
  const altas = pendientes.filter((r) => r.confianza === 'alta')
  const aplicadas = recomendaciones.filter((r) => r.aplicada)
  const sinAplicar = recomendaciones.filter((r) => !r.aplicada)

  const ejecutar = async (llamada) => {
    setOcupado(true)
    setError(null)
    try {
      api.latido()
      const res = await llamada()
      if (!res?.ok) setError(res?.error || 'error desconocido')
      return res
    } finally {
      setOcupado(false)
    }
  }
  const alSeleccionar = (id) => { api.latido(); setSeleccion(id) }

  const esperando = espera?.estado === 'esperando' && espera.deadline_epoch != null
  const restante = esperando ? Math.max(0, Math.round((espera.deadline_epoch - t) / 1000)) : null
  const desatendida = espera?.estado === 'desatendida'
  // Sin dato de la espera no se inventa un número (antes: 30 por defecto).
  const minutos = espera?.espera_min ?? null
  const sinPendientes = !libres.length && !recomendaciones.length

  return (
    <div className="pm-overlay ri-overlay" role="dialog" aria-modal="true" aria-label="Revisión del índice">
      <div className="pm-card ri-card">
        <h2 className="pm-title">Revisión del índice: fotos sin colocar</h2>
        {esperando ? (
          <p className="pm-status warn ri-espera" role="status" data-testid="ri-cuenta-atras">
            Se continuará solo en {mmss(restante)} si no tocas nada.
          </p>
        ) : null}
        {desatendida ? (
          <p className="pm-status warn ri-aviso" role="alert" data-testid="ri-desatendida">
            Sin respuesta{minutos != null ? ` en ${Number(minutos.toFixed(2))} min` : ' durante la espera'}: aplicada {aplicadas.length} {aplicadas.length === 1 ? 'recomendación' : 'recomendaciones'} de confianza alta; {sinAplicar.length} quedan en SIN_ORDENAR.
          </p>
        ) : null}
        {enVuelo.length ? (
          <p className="pm-status err" role="alert" data-testid="ri-aviso-en-vuelo">
            AVISO: {enVuelo.length} {enVuelo.length === 1 ? 'tramo de fotos sin colocar cae' : 'tramos de fotos sin colocar caen'} dentro de la franja de un vuelo del estadillo; revisa las horas de cámara.
          </p>
        ) : null}
        {(modelo.avisos ?? []).map((a, i) => (
          <p key={i} className="pm-status warn ri-aviso" role="alert" data-testid="ri-aviso-dato">AVISO: {a}</p>
        ))}
        {error ? <p className="pm-status err" role="alert">✗ {error}</p> : null}

        {sinPendientes ? (
          <p className="ri-vacio">No quedan fotos sin colocar ni recomendaciones.</p>
        ) : (
          <>
            {recomendaciones.length ? <ul className="ri-recs">{recomendaciones.map((r) => <ChipRecomendacion key={r.id} r={r} />)}</ul> : null}
            <CalendarioHoras vuelos={vuelos} tramos={tramos} seleccion={seleccion} onSeleccion={alSeleccionar} />
            <FotosLibres tramos={tramos} seleccion={seleccion} onSeleccion={alSeleccionar} />
            <div className="ri-meter">
              <select className="sc-select" aria-label="Vuelo destino" value={fila} onChange={(e) => { api.latido(); setFila(e.target.value) }}>
                <option value="">Elige vuelo…</option>
                {vuelos.map((v) => <option key={v.fila} value={v.fila}>{`Fila ${v.fila} · ${etiquetaVuelo(v)}${v.generales ? ' · fotos generales' : ''}`}</option>)}
                <option value="sin_ordenar">Dejar en SIN_ORDENAR (sin vuelo)</option>
              </select>
              <button type="button" className="btn-ghost" disabled={ocupado || !tramo || fila === ''}
                      onClick={() => ejecutar(() => api.asignar(tramo.claves, fila === 'sin_ordenar' ? null : Number(fila)))}>
                Meter tramo en vuelo
              </button>
              {recTramo ? (
                <button type="button" className="btn-ghost" disabled={ocupado} onClick={() => ejecutar(() => api.aceptar([recTramo.id]))}>
                  Aceptar recomendación del tramo
                </button>
              ) : null}
            </div>
          </>
        )}
        <div className="pm-actions ri-acciones">
          <button type="button" className="btn-ghost" disabled={ocupado || !altas.length}
                  onClick={() => ejecutar(() => api.aceptar(altas.map((r) => r.id)))}>
            Aceptar las de alta confianza
          </button>
          <button type="button" className="btn-run" disabled={ocupado} onClick={() => ejecutar(() => api.continuar('tal_cual'))}>Continuar</button>
          <button type="button" className="btn-ghost ri-cancelar" disabled={ocupado} onClick={() => ejecutar(() => api.cancelar())}>Cancelar run</button>
        </div>
      </div>
    </div>
  )
}
