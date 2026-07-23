// Modal PREVIO al procesado: muestra la info básica que se saca del estadillo
// (piloto(s), dron(es), nº de vuelos y franjas horarias) para que el operador
// confirme antes de arrancar. Datos de `api.readEstadilloInfo` (atom_core/estadillo).

export default function PreflightModal({ info, loading, onStart, onCancel }) {
  const err = info && info.error
  const vuelos = (info && info.vuelos) || []
  const titulo = (info && (info.trabajo || info.empresa)) || 'Estadillo'

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
              <PFItem label="Fecha" value={info.fecha} />
              <PFItem label="Piloto(s)" value={(info.pilotos || []).join(', ')} />
              <PFItem label="Dron(es)" value={(info.drones || []).join(', ')} />
              <PFItem label="Nº de vuelos" value={String(info.num_vuelos ?? '')} />
              <PFItem
                label="Franja horaria"
                value={
                  info.hora_inicio || info.hora_final
                    ? `${info.hora_inicio || '?'} → ${info.hora_final || '?'}`
                    : ''
                }
              />
            </dl>

            {vuelos.length > 0 && (
              <div className="pf-flights">
                <table className="pf-table">
                  <thead>
                    <tr>
                      <th>PB</th>
                      <th>Vuelo</th>
                      <th>Inicio</th>
                      <th>Final</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vuelos.map((v, i) => (
                      <tr key={i}>
                        <td>{v.pb || '—'}</td>
                        <td>{v.vuelo || '—'}</td>
                        <td>{v.inicio || '—'}</td>
                        <td>{v.final || '—'}</td>
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

function PFItem({ label, value }) {
  if (!value) return null
  return (
    <div className="pf-item">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
