import { ordenarPB } from './subidaMedicion'

// Semáforo de salud (cabecera del panel) y tabla de progreso por PB en vivo.
const nf = (n, d = 1) => Number(n).toLocaleString('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d })
const NOMBRE = { verde: 'verde', ambar: 'ámbar', rojo: 'rojo' }

export function SemaforoSubida({ salud }) {
  if (!salud) return null
  return (
    <div className={`sub-semaforo sub-semaforo-${salud.nivel}`} role="status" aria-live="polite" data-testid="semaforo-subida" data-nivel={salud.nivel}>
      <i className="sub-semaforo-luz" aria-hidden="true" />
      <strong>{salud.titulo}</strong>
      <span className="sr-only"> (semáforo {NOMBRE[salud.nivel]})</span>
      <span className="sub-semaforo-motivo">{salud.motivo}</span>
    </div>
  )
}

export function TablaPorPB({ porPb }) {
  const filas = ordenarPB(porPb)
  if (!filas.length) return null
  return (
    <table className="sub-tabla sub-por-pb" aria-label="Progreso por PB" data-testid="tabla-por-pb">
      <thead>
        <tr><th scope="col">PB</th><th scope="col">Progreso</th><th scope="col">Ficheros</th><th scope="col">Datos</th><th scope="col">Fallidas</th></tr>
      </thead>
      <tbody>
        {filas.map((g) => {
          const pct = g.bytes_total > 0 ? Math.min(100, (g.bytes_done / g.bytes_total) * 100)
            : g.ficheros_total > 0 ? Math.min(100, (g.ficheros_hechos / g.ficheros_total) * 100) : 0
          return (
            <tr key={g.pb} data-terminado={g.terminado ? '1' : '0'}>
              <th scope="row">{g.pb}{g.terminado && <span className="sub-pb-ok" title="Terminado" aria-label="terminado"> ✓</span>}</th>
              <td><span className="sub-pb-barra" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span></td>
              <td>{g.ficheros_hechos.toLocaleString('es-ES')} / {g.ficheros_total.toLocaleString('es-ES')}</td>
              <td>{nf(g.bytes_done / 1e9, 2)} / {nf(g.bytes_total / 1e9, 2)} GB</td>
              <td className={g.fallidas ? 'sub-aviso' : ''}>{g.fallidas || 0}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
