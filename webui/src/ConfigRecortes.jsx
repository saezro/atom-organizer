import { useState } from 'react'

// Tabla efectiva de % de recorte (modelo, cámara W/Z). Filas de la tabla versionada: solo
// lectura, pero sobrescribibles; el override se edita y se quita. Las claves guardadas en
// Config.ini son `MODELO` (W) / `MODELO_Z` (Z), igual que `atom_core.recortes.clave_de`.

export function claveDe(modelo, camara) {
  const m = String(modelo).trim().toUpperCase()
  return camara === 'Z' ? `${m}_Z` : m
}

// "48,5" / "48.5" -> 48.5; fuera de (0, 100] o no numérico -> null.
// "-" (o null/None del backend) = sin recorte -> devuelve '-'.
export function parsePct(txt) {
  const t = String(txt ?? '-').trim().replace(',', '.')
  if (t === '' ) return null
  if (t === '-' || t.toLowerCase() === 'none' || t.toLowerCase() === 'null') return '-'
  if (!/^\d+(\.\d+)?$/.test(t)) return null
  const n = Number(t)
  return n > 0 && n <= 100 ? n : null
}

export function parClave(clave) {
  const c = String(clave).trim().toUpperCase()
  return c.endsWith('_Z') ? { modelo: c.slice(0, -2), camara: 'Z' } : { modelo: c, camara: 'W' }
}

// Filas visibles: tabla versionada + overrides ({CLAVE: texto %}); el override gana.
export function filasRecortes(tabla, overrides) {
  const filas = new Map()
  for (const r of tabla || []) {
    filas.set(claveDe(r.modelo, r.camara), { ...r, clave: claveDe(r.modelo, r.camara), origen: 'tabla', pct: r.porcentaje == null ? '-' : String(r.porcentaje) })
  }
  for (const [clave, pct] of Object.entries(overrides || {})) {
    const previa = filas.get(clave)
    filas.set(clave, { ...parClave(clave), clave, origen: 'override', pct: pct == null ? '-' : String(pct), nota: previa?.nota || '' })
  }
  return [...filas.values()].sort((a, b) => a.modelo.localeCompare(b.modelo) || a.camara.localeCompare(b.camara))
}

export default function ConfigRecortes({ tabla, overrides, onChange, loaded }) {
  const [mModelo, setMModelo] = useState('')
  const [mCamara, setMCamara] = useState('W')
  const [mPct, setMPct] = useState('')
  const [error, setError] = useState('')

  const filas = filasRecortes(tabla, overrides)

  function setPct(clave, pct) {
    onChange({ ...overrides, [clave]: pct })
  }
  function quitar(clave) {
    const { [clave]: _quitada, ...resto } = overrides
    onChange(resto)
  }
  function anadir() {
    const modelo = mModelo.trim().toUpperCase()
    if (!modelo) { setError('Indica el modelo.'); return }
    if (parsePct(mPct) === null) { setError('El % debe ser un número entre 0 y 100 (admite decimales).'); return }
    setError('')
    setPct(claveDe(modelo, mCamara), mPct.trim())
    setMModelo('')
    setMPct('')
  }

  return (
    <div className="field" data-testid="config-recortes">
      <span className="field-label">Recorte automático</span>
      <span className="field-hint">
        % de recorte del RGB por modelo de dron y cámara (W = wide, Z = zoom). Las filas «tabla» vienen
        con la versión de la app; un override propio manda sobre ellas. Si aparece un dron nuevo
        que no está aquí, el recorte automático fallará para ese modelo: añade su override.
      </span>

      {filas.length > 0 ? (
        <table className="recortes-tabla">
          <thead>
            <tr><th>Modelo</th><th>Cámara</th><th>%</th><th>Origen</th><th>Nota</th><th /></tr>
          </thead>
          <tbody>
            {filas.map((r) => (
              <tr key={r.clave}>
                <td className="config-model">{r.modelo}</td>
                <td>{r.camara}</td>
                <td>
                  {r.origen === 'override' ? (
                    <input
                      className="glass-input recortes-pct"
                      type="text"
                      inputMode="decimal"
                      aria-label={`% ${r.modelo} ${r.camara}`}
                      value={r.pct}
                      onChange={(e) => setPct(r.clave, e.target.value)}
                    />
                  ) : (
                    <span className="config-pct">{r.pct === '-' ? 'sin recorte' : `${r.pct}%`}</span>
                  )}
                </td>
                <td><span className={`chip-origen chip-origen-${r.origen}`}>{r.origen}</span></td>
                <td className="recortes-nota">{r.nota}</td>
                <td>
                  {r.origen === 'override' ? (
                    <button type="button" className="config-del" title="Quitar override" aria-label={`Quitar override ${r.modelo} ${r.camara}`} onClick={() => quitar(r.clave)}>✕</button>
                  ) : (
                    <button type="button" className="btn-ghost" onClick={() => setPct(r.clave, r.pct)}>Sobrescribir</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <span className="field-hint hint-warn">{loaded ? 'No hay modelos configurados todavía.' : 'Cargando…'}</span>
      )}

      <div className="suffix-row">
        <div className="suffix-cell">
          <input className="glass-input" type="text" value={mModelo} placeholder="Modelo (p.ej. M4T)" onChange={(e) => setMModelo(e.target.value)} />
        </div>
        <div className="suffix-cell">
          <select className="glass-input" aria-label="Cámara del override" value={mCamara} onChange={(e) => setMCamara(e.target.value)}>
            <option value="W">W (wide)</option>
            <option value="Z">Z (zoom)</option>
          </select>
        </div>
        <div className="suffix-cell">
          <input className="glass-input" type="text" inputMode="decimal" value={mPct} placeholder="% (p.ej. 48,5)" onChange={(e) => setMPct(e.target.value)} />
        </div>
        <button type="button" className="btn-ghost" onClick={anadir}>Añadir override</button>
      </div>
      {error && <span className="field-hint hint-warn">{error}</span>}
    </div>
  )
}
