// Modal PREVIO al procesado: muestra la info básica que se saca del estadillo
// (piloto(s), dron(es), nº de vuelos y franjas horarias) para que el operador
// confirme antes de arrancar. Datos de `api.readEstadilloInfo` (atom_core/estadillo).

export default function PreflightModal({ info, loading, onStart, onCancel }) {
  const err = info && info.error
  const vuelos = (info && info.vuelos) || []
  const titulo = (info && (info.trabajo || info.empresa)) || 'Estadillo'
  // Un estadillo puede cubrir varios días (campaña con vuelos repartidos). Con
  // más de una fecha, un rango horario suelto no dice de qué día es, así que se
  // muestran las fechas completas y la franja se cualifica con su día.
  const fechas = (info && info.fechas) || (info && info.fecha ? [info.fecha] : [])
  const multiDia = fechas.length > 1
  const cruces = vuelos.filter((v) => v.cruza_medianoche)

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card">
        <h2 className="pm-title">
          Resumen del vuelo: <span className="pm-plant">{titulo}</span>
        </h2>

        {loading && <p className="pf-loading">Leyendo estadillo…</p>}

        {!loading && err && (
          <p className="pm-status err">✗ No se pudo leer el estadillo: {err}</p>
        )}

        {!loading && !err && info && (
          <>
            <dl className="pf-facts">
              <PFItem label="Empresa" value={info.empresa} />
              <PFItem label={multiDia ? 'Fechas' : 'Fecha'} value={fechas.join(', ')} />
              <PFItem label="Piloto(s)" value={(info.pilotos || []).join(', ')} />
              <PFItem label="Dron(es)" value={(info.drones || []).join(', ')} />
              <PFItem label="Nº de vuelos" value={String(info.num_vuelos ?? '')} />
              <PFItem label={multiDia ? 'De' : 'Franja horaria'} value={franja(info, multiDia)} />
            </dl>

            {cruces.length > 0 && (
              <p className="pm-status err">
                ⚠ {cruces.length === 1 ? 'Un vuelo cruza' : `${cruces.length} vuelos cruzan`} la
                medianoche ({cruces.map((v) => `PB${v.pb} V${v.vuelo}`).join(', ')}). El estadillo
                da inicio y final en el mismo día, así que esas imágenes acabarán en SIN_ORDENAR.
                Corrige el estadillo antes de procesar.
              </p>
            )}

            {vuelos.length > 0 && (
              <div className="pf-flights">
                <table className="pf-table">
                  <thead>
                    <tr>
                      <th>PB</th>
                      <th>Vuelo</th>
                      <th>Fecha</th>
                      <th>Inicio</th>
                      <th>Final</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vuelos.map((v, i) => (
                      <tr key={i} className={v.cruza_medianoche ? 'pf-row-warn' : undefined}>
                        <td>{v.pb || '—'}</td>
                        <td>{v.vuelo || '—'}</td>
                        <td>{v.fecha || '—'}</td>
                        <td>{v.inicio || '—'}</td>
                        <td>
                          {v.final || '—'}
                          {v.cruza_medianoche && <span className="pf-warn" title="El final es anterior al inicio: el vuelo cruza medianoche"> ⚠</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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
