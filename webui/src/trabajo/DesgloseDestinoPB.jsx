// Prechequeo: por PB, qué hay en local, qué ya está en el bucket, qué falta y qué solo está en el bucket.
const nf = (n) => Number(n || 0).toLocaleString('es-ES')
const gb = (b) => `${Number(b / 1e9).toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} GB`

export function estadoPB(g) {
  if (!g.locales) return { clave: 'solo', texto: 'solo en bucket' }
  if (!g.pendientes && !g.distintos) return { clave: 'completo', texto: 'completo' }
  return { clave: 'falta', texto: 'falta' }
}

const COLOR = { completo: '#34d399', falta: '#fbbf24', solo: '#9ca3af' }

export default function DesgloseDestinoPB({ porPb }) {
  const filas = Array.isArray(porPb) ? porPb : []
  if (!filas.length) return null
  const tot = filas.reduce((a, g) => ({
    locales: a.locales + (g.locales || 0), en_bucket: a.en_bucket + (g.en_bucket || 0),
    pendientes: a.pendientes + (g.pendientes || 0), distintos: a.distintos + (g.distintos || 0),
    solo_bucket: a.solo_bucket + (g.solo_bucket || 0), bytes_pendientes: a.bytes_pendientes + (g.bytes_pendientes || 0),
  }), { locales: 0, en_bucket: 0, pendientes: 0, distintos: 0, solo_bucket: 0, bytes_pendientes: 0 })
  return (
    <table className="sub-tabla sub-por-pb" aria-label="Desglose del destino por PB" data-testid="desglose-destino-pb">
      <thead>
        <tr>
          <th scope="col">PB</th>
          <th scope="col" title="Ficheros en la carpeta local">Local</th>
          <th scope="col" title="Ya en bucket (misma ruta y tamaño)">Ya en bucket</th>
          <th scope="col" title="Faltan por subir">Faltan por subir</th>
          <th scope="col" title="Existen en el bucket con otro tamaño">Distintos</th>
          <th scope="col" title="Solo en bucket (otras subidas)">Solo en bucket (otras subidas)</th>
          <th scope="col">Estado</th>
        </tr>
      </thead>
      <tbody>
        {filas.map((g) => {
          const e = estadoPB(g)
          return (
            <tr key={g.pb} data-estado={e.clave}>
              <th scope="row">{g.pb}</th>
              <td>{nf(g.locales)}</td>
              <td>{nf(g.en_bucket)}</td>
              <td className={g.pendientes ? 'sub-aviso' : ''} title={g.bytes_pendientes ? gb(g.bytes_pendientes) : undefined}>{nf(g.pendientes)}</td>
              <td className={g.distintos ? 'sub-aviso' : ''}>{nf(g.distintos)}</td>
              <td>{nf(g.solo_bucket)}</td>
              <td style={{ color: COLOR[e.clave] }} data-testid={`estado-${g.pb}`}>{e.texto}</td>
            </tr>
          )
        })}
      </tbody>
      <tfoot>
        <tr data-testid="desglose-total">
          <th scope="row">Total</th>
          <td>{nf(tot.locales)}</td>
          <td>{nf(tot.en_bucket)}</td>
          <td>{nf(tot.pendientes)}</td>
          <td>{nf(tot.distintos)}</td>
          <td>{nf(tot.solo_bucket)}</td>
          <td>{tot.bytes_pendientes ? `${gb(tot.bytes_pendientes)} por subir` : ''}</td>
        </tr>
      </tfoot>
    </table>
  )
}

export { DesgloseDestinoPB }
