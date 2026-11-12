import { useCallback, useEffect, useState } from 'react'
import { api } from './bridge'

// Fotos fuera de rango del estadillo: muestra EN QUÉ PARTE del estadillo se han
// quedado sin colocar (fila anterior y siguiente) y pregunta dónde van.
// Las horas son las del dron (EXIF), tal cual: no se convierten de zona.

const MOTIVOS = {
  'fuera-de-ventana': 'Fuera de las franjas de vuelo del estadillo',
  'dia-sin-estadillo': 'Día sin ningún vuelo en el estadillo',
  'sin-fecha-exif': 'La foto no tiene fecha EXIF',
  'estadillo-sin-fecha-legible': 'El estadillo no tiene fecha legible',
}

// "2026-05-04T10:12:03" -> "10:12". Se corta el texto: nada de `Date`, que
// aplicaría la zona del equipo a una hora que ya es la del dron.
export function hhmm(iso) {
  const m = /[T ](\d{2}:\d{2})/.exec(String(iso || ''))
  return m ? m[1] : null
}

function distancia(s) {
  if (s == null) return ''
  if (s < 90) return `${Math.round(s)} s`
  return `${Math.round(s / 60)} min`
}

export function textoVecino(v) {
  const franja = `${hhmm(v.hora_inicio) ?? '?'}–${hhmm(v.hora_fin) ?? '?'}`
  const dist = v.distancia_s != null ? `, a ${distancia(v.distancia_s)}` : ''
  return `fila ${v.fila} (PB${v.pb}/V${v.vuelo}, ${franja}${dist})`
}

function tiposTexto(porTipo) {
  return Object.entries(porTipo || {})
    .map(([t, n]) => `${n} ${t}`)
    .join(' · ')
}

function Grupo({ g, filas, ocupado, claves, onVerFotos, onAsignar }) {
  const [fila, setFila] = useState('')
  const [abierto, setAbierto] = useState(false)
  const ant = g.vecino_anterior
  const sig = g.vecino_siguiente
  const franja = g.hora_inicio
    ? `${hhmm(g.hora_inicio) ?? '?'}–${hhmm(g.hora_fin) ?? '?'}`
    : 'sin hora'
  const filasGrupo = filas.filter((f) => f.ejecucion === g.ejecucion)

  function alternarFotos() {
    const nuevo = !abierto
    setAbierto(nuevo)
    if (nuevo && !claves) onVerFotos()
  }

  return (
    <li className="sc-grupo" data-testid={`sc-grupo-${g.grupo_id}`}>
      <div className="sc-cab">
        <strong>{franja}</strong>
        <span className="pm-stats">
          {g.n_fotos} {g.n_fotos === 1 ? 'foto' : 'fotos'} ({tiposTexto(g.fotos_por_tipo)}) · hora del dron
        </span>
      </div>
      <div className="sc-motivo">{MOTIVOS[g.motivo] || g.motivo}</div>

      {g.motivo === 'fuera-de-ventana' && (
        <div className="sc-pos">
          {ant || sig ? (
            <>
              Quedó entre {ant ? textoVecino(ant) : 'el inicio del estadillo'} y{' '}
              {sig ? textoVecino(sig) : 'el final del estadillo'}.
            </>
          ) : (
            'No hay filas vecinas en el estadillo para esta franja.'
          )}
        </div>
      )}

      <button type="button" className="pm-detail-toggle" onClick={alternarFotos} aria-expanded={abierto}>
        <span aria-hidden="true">{abierto ? '▾' : '▸'}</span> ver fotos
      </button>
      {abierto && (
        <pre className="pm-log sc-fotos">{claves ? claves.join('\n') : 'Cargando…'}</pre>
      )}

      <div className="sc-acciones">
        <button type="button" className="btn-ghost" disabled={ocupado || !ant}
          onClick={() => onAsignar(g, ant.fila)}>
          Colocar en fila anterior{ant ? ` (${ant.fila})` : ''}
        </button>
        <button type="button" className="btn-ghost" disabled={ocupado || !sig}
          onClick={() => onAsignar(g, sig.fila)}>
          Colocar en fila siguiente{sig ? ` (${sig.fila})` : ''}
        </button>
        <select className="sc-select" aria-label={`Fila del estadillo para ${g.grupo_id}`}
          value={fila} disabled={ocupado} onChange={(e) => setFila(e.target.value)}>
          <option value="">Otra fila del estadillo…</option>
          {filasGrupo.map((f) => (
            <option key={f.fila} value={f.fila}>
              {`Fila ${f.fila} · PB${f.pb}/V${f.vuelo} · ${f.fecha ?? ''} ${hhmm(f.hora_inicio) ?? '?'}–${hhmm(f.hora_fin) ?? '?'}`}
            </option>
          ))}
        </select>
        <button type="button" className="btn-ghost" disabled={ocupado || fila === ''}
          onClick={() => onAsignar(g, Number(fila))}>
          Colocar
        </button>
        <button type="button" className="btn-ghost" disabled={ocupado}
          onClick={() => onAsignar(g, null)}>
          Dejar en SIN_ORDENAR
        </button>
      </div>
    </li>
  )
}

export default function SinColocarModal({ destino, onClose, onRelanzar, running = false }) {
  const [datos, setDatos] = useState(null) // {grupos, filas}
  const [error, setError] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [aviso, setAviso] = useState(null) // {texto}
  const [claves, setClaves] = useState(null) // {grupo_id: [clave]}

  const cargar = useCallback(async () => {
    try {
      const r = await api.sinColocarListar(destino, false)
      if (!r || r.ok === false) {
        setError((r && r.error) || 'No se pudo leer la lista de fotos sin colocar.')
        return
      }
      setError('')
      setDatos({ grupos: r.grupos || [], filas: r.filas || [] })
    } catch (e) {
      setError(e?.message ? e.message : String(e))
    }
  }, [destino])

  useEffect(() => { cargar() }, [cargar])

  async function verFotos() {
    try {
      const r = await api.sinColocarListar(destino, true)
      if (!r || r.ok === false) {
        setError((r && r.error) || 'No se pudieron leer los nombres de las fotos.')
        return
      }
      setClaves(Object.fromEntries((r.grupos || []).map((g) => [g.grupo_id, g.claves || []])))
    } catch (e) {
      setError(e?.message ? e.message : String(e))
    }
  }

  async function asignar(g, fila) {
    setOcupado(true)
    setError('')
    try {
      const r = await api.sinColocarAsignar(g.grupo_id, fila, destino)
      if (!r || r.ok === false) {
        setError((r && r.error) || 'No se pudo asignar el grupo.')
        return
      }
      const res = r.resultado || {}
      if (fila != null && (res.pendientes_apply > 0 || res.recolocadas > 0)) {
        setAviso({
          texto: res.recolocadas > 0
            ? `${res.recolocadas} ${res.recolocadas === 1 ? 'foto ya estaba aplicada y quedó' : 'fotos ya estaban aplicadas y quedaron'} pendiente de recolocar.`
            : `${res.pendientes_apply} ${res.pendientes_apply === 1 ? 'foto pendiente' : 'fotos pendientes'} de aplicar.`,
        })
      }
      if (res.avisos && res.avisos.length) setError(res.avisos.join(' · '))
      setClaves(null)
      await cargar()
    } catch (e) {
      setError(e?.message ? e.message : String(e))
    } finally {
      setOcupado(false)
    }
  }

  return (
    <div className="pm-overlay sc-overlay" role="dialog" aria-modal="true" aria-label="Fotos sin colocar">
      <div className="pm-card sc-card">
        <h2 className="pm-title">Fotos sin colocar</h2>
        <p className="pm-stats">
          Estas imágenes quedaron fuera de las franjas del estadillo. Las horas son las del
          dron (EXIF), sin convertir. Indica dónde va cada grupo.
        </p>

        {error && <p className="pm-status err" role="alert">✗ {error}</p>}

        {aviso && (
          <p className="pm-status warn" role="status">
            ⚠ {aviso.texto} Hay que relanzar «aplicar» para que se coloquen.{' '}
            {onRelanzar && (
              <button type="button" className="btn-run sc-relanzar" disabled={running}
                onClick={onRelanzar}>
                Relanzar aplicar
              </button>
            )}
          </p>
        )}

        {datos && datos.grupos.length === 0 && !error && (
          <p className="pm-stats">No quedan fotos sin colocar.</p>
        )}

        {datos && datos.grupos.length > 0 && (
          <ul className="sc-lista">
            {datos.grupos.map((g) => (
              <Grupo key={g.grupo_id} g={g} filas={datos.filas} ocupado={ocupado}
                claves={claves ? claves[g.grupo_id] : null}
                onVerFotos={verFotos} onAsignar={asignar} />
            ))}
          </ul>
        )}

        <div className="pm-actions">
          <button type="button" className="btn-run pm-close" onClick={onClose}>Cerrar</button>
        </div>
      </div>
    </div>
  )
}
