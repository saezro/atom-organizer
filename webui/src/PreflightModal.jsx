// Modal PREVIO al procesado: muestra la info básica que se saca del estadillo
// (piloto(s), dron(es), nº de vuelos y franjas horarias) para que el operador
// confirme antes de arrancar. Datos de `api.readEstadilloInfo` (atom_core/estadillo) y, para el
// calendario, de `api.calendarioPrevio` (vuelos con dron/piloto + fotos fuera de franja en gris).
import { memo, useMemo } from 'react'
import CalendarioHoras from './CalendarioHoras.jsx'
import { modeloDesdeCalendario, tramosLibres } from './calendarioHoras.js'

const NOOP = () => {}

function PreflightModal({ info, calendario, calCargando = false, calParcial = false, loading, onStart, onCancel }) {
  const err = info && info.error
  const vuelos = (info && info.vuelos) || []
  // Calendario de vuelos (dron + piloto) y fotos fuera de toda franja (tramos grises).
  const { modelo, error: errCal } = useMemo(() => {
    if (!calendario) return { modelo: null, error: null }
    try {
      return { modelo: modeloDesdeCalendario(calendario), error: null }
    } catch (e) {
      return { modelo: null, error: e?.message ?? String(e) }
    }
  }, [calendario])
  const tramos = useMemo(() => (modelo ? tramosLibres(modelo.libres) : []), [modelo])
  const titulo = (info && (info.trabajo || info.empresa)) || 'Estadillo'
  // Un estadillo puede cubrir varios días (campaña con vuelos repartidos). Con
  // más de una fecha, un rango horario suelto no dice de qué día es, así que se
  // muestran las fechas completas y la franja se cualifica con su día.
  const fechas = (info && info.fechas) || (info && info.fecha ? [info.fecha] : [])
  const multiDia = fechas.length > 1
  const cruces = vuelos.filter((v) => v.cruza_medianoche)
  const errores = (info && info.errores) || []

  return (
    <div className="pm-overlay ri-overlay-full" role="dialog" aria-modal="true">
      <div className="pm-card ri-card">
        <h2 className="pm-title">
          Calendario de vuelos: <span className="pm-plant">{titulo}</span>
        </h2>

        {loading && <p className="pf-loading">Leyendo estadillo…</p>}

        {!loading && err && (
          <p className="pm-status err">✗ No se pudo leer el estadillo: {err}</p>
        )}

        {!loading && !err && info && (
          <>
            <dl className="pf-facts">
              <PFItem label="Empresa" value={info.empresa !== titulo ? info.empresa : null} />
              <PFItem label={multiDia ? 'Fechas' : 'Fecha'} value={fechas.join(', ')} />
              <PFItem label="Piloto(s)" value={(info.pilotos || []).join(', ')} />
              <PFItem label="Dron(es)" value={(info.drones || []).join(', ')} />
              <PFItem label="Nº de vuelos" value={String(info.num_vuelos ?? '')} />
              <PFItem label={multiDia ? 'De' : 'Franja horaria'} value={franja(info, multiDia)} />
            </dl>

            {errores.length > 0 && (
              <p className="pm-status err">
                ⚠ No se {errores.length === 1 ? 'pudo leer' : 'pudieron leer'}{' '}
                {errores.map((e) => `${e.archivo} (${e.error})`).join('; ')}. Se sigue con el
                resto.
              </p>
            )}

            {cruces.length > 0 && (
              <p className="pm-status err">
                ⚠ {cruces.length === 1 ? 'Un vuelo cruza' : `${cruces.length} vuelos cruzan`} la
                medianoche ({cruces.map((v) => `PB${v.pb} V${v.vuelo}`).join(', ')}). El estadillo
                da inicio y final en el mismo día, así que esas imágenes acabarán en SIN_ORDENAR.
                Corrige el estadillo antes de procesar.
              </p>
            )}

            {calCargando && !modelo && (
              <p className="pf-loading" role="status" data-testid="pf-cal-cargando">
                <svg className="espera-spinner" viewBox="0 0 24 24" width="18" height="18" fill="none"
                     stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 3a9 9 0 1 1-9 9" />
                </svg>
                Leyendo las horas de las fotos del origen para pintar el calendario… Puedes comenzar sin esperar.
              </p>
            )}
            {calCargando && modelo && (
              <p className="pf-loading" role="status" data-testid="pf-cal-leyendo">
                <svg className="espera-spinner" viewBox="0 0 24 24" width="18" height="18" fill="none"
                     stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 3a9 9 0 1 1-9 9" />
                </svg>
                Cargando las fotos por hora… Giro, avisos y fotos sin colocar son provisionales hasta terminar. Puedes comenzar sin esperar.
              </p>
            )}
            {errCal && <p className="pm-status err">✗ No se pudo preparar el calendario: {errCal}</p>}
            <AvisosDato avisos={modelo?.avisos ?? []} provisional={calParcial} />
            {modelo && (
              <CalendarioHoras vuelos={modelo.vuelos} tramos={tramos} seleccion={null} onSeleccion={NOOP}
                               cargando={calParcial} />
            )}
          </>
        )}

        <div className="pm-actions pf-actions">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancelar
          </button>
          <button type="button" className="btn-run" disabled={loading} onClick={onStart}>
            Comenzar
          </button>
        </div>
      </div>
    </div>
  )
}

// Con un solo día basta la hora ("08:12 → 19:40"); con varios hay que decir de
// qué día es cada extremo, o el rango parece un vuelo continuo que no existió.
function franja(info, multiDia) {
  const ini = info.hora_inicio
  const fin = info.hora_final
  if (!ini && !fin) return ''
  if (!multiDia) return `${ini || '?'} → ${fin || '?'}`
  const a = [info.fecha_inicio, ini].filter(Boolean).join(' ')
  const b = [info.fecha_final, fin].filter(Boolean).join(' ')
  return `${a || '?'} → ${b || '?'}`
}

function PFItem({ label, value }) {
  if (!value) return null
  return (
    <div className="pf-item">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

// Avisos del calendario en UNA línea: uno solo se muestra tal cual; varios, plegados con contador.
function AvisosDato({ avisos, provisional = false }) {
  if (!avisos.length) return null
  const prov = provisional ? ' (provisional, leyendo fotos…)' : ''
  if (avisos.length === 1) {
    return <p className="pm-status warn pf-aviso" role="alert" data-testid="pf-aviso-dato">AVISO{prov}: {avisos[0]}</p>
  }
  return (
    <details className="pm-status warn pf-aviso pf-avisos" role="alert">
      <summary>⚠ {avisos.length} avisos{prov} · {avisos[0]}</summary>
      <ul>{avisos.map((a, i) => <li key={i} data-testid="pf-aviso-dato">{a}</li>)}</ul>
    </details>
  )
}

export default memo(PreflightModal)
