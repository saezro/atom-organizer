import { useEffect, useState } from 'react'
import { api, onAnalisis } from '../bridge'
import { SPLIT_ADVANCED } from '../schema'
import { Field, initialState, buildParams } from '../TaskBlock'
import PasoCarpeta from './PasoCarpeta'

// Campos avanzados aplanados (todas las secciones) para el estado del panel.
const ADV_FIELDS = SPLIT_ADVANCED.flatMap((s) => s.fields)

export default function PanelOrganizar({ origen, estadillos, ready, running, onRun }) {
  const [destino, setDestino] = useState('')
  const [destinoFull, setDestinoFull] = useState(null) // {count} si la salida no está vacía
  const [rename, setRename] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [adv, setAdv] = useState(() => initialState(ADV_FIELDS))
  const [detected, setDetected] = useState(null)
  const [escaneados, setEscaneados] = useState(0)

  const setAdvField = (name, value) => setAdv((s) => ({ ...s, [name]: value }))

  async function pickAdv(field) {
    const path = field.type === 'file' ? await api.pickFile() : await api.pickFolder()
    if (path) setAdvField(field.name, path)
  }

  // Al llegar la carpeta origen: escanear los nombres en hilo y autorrellenar
  // el sufijo de separación (el operador ya no tiene que escribirlo; sigue
  // editable). `analisis_reset()` va SIEMPRE antes de `detect_suffixes_start`:
  // el backend no resetea `_cancel_analisis` dentro de detect_suffixes_start,
  // solo lo limpia analisis_reset(); sin este orden, tras una cancelación
  // previa el siguiente análisis aborta al instante en silencio.
  useEffect(() => {
    if (!origen) return
    setEscaneados(0)
    let vivo = true
    const off = onAnalisis((d) => {
      if (d.scope !== 'suffixes') return
      if (d.kind === 'scan') setEscaneados(d.done)
      if (d.kind === 'cancelled') { setEscaneados(0); setDetected(null) }
      if (d.kind === 'error') { setEscaneados(0); setDetected({ ok: false, error: d.text }) }
      if (d.kind === 'done') {
        setEscaneados(0)
        setDetected(d.data)
        if (d.data?.ok) {
          setAdv((s) => ({ ...s, end_thermo_files: d.data.thermal || '', end_rgb_files: d.data.rgb || '' }))
        }
      }
    })
    ;(async () => {
      await api.analisisReset()
      if (vivo) api.detectSuffixesStart(origen)
    })()
    return () => { vivo = false; off() }
  }, [origen])

  const canRun = ready && !running && origen && destino && !destinoFull

  function handleRun() {
    const advanced = buildParams(ADV_FIELDS, adv)
    onRun('split_images', { origen, destino, estadillo: estadillos, rename }, advanced)
  }

  return (
    <div className="card">
      <h2 className="card-title">Organizar completo</h2>
      <PasoCarpeta
        label="Carpeta final"
        value={destino}
        onChange={(p) => {
          setDestino(p)
          api.folderIsEmpty(p)
            .then((r) => setDestinoFull(r?.empty ? null : { count: r?.count ?? 0 }))
            .catch(() => setDestinoFull(null))
        }}
        avisoNoVacia
      />
      <div className="field">
        <span className="field-label">Sufijos de separación (según el dron)</span>
        <div className="suffix-row">
          <div className="suffix-cell">
            <input
              className="glass-input"
              type="text"
              value={adv.end_thermo_files ?? ''}
              placeholder="_T (térmicas)"
              onChange={(e) => setAdvField('end_thermo_files', e.target.value)}
            />
            <span className="suffix-tag">Térmico</span>
          </div>
          <div className="suffix-cell">
            <input
              className="glass-input"
              type="text"
              value={adv.end_rgb_files ?? ''}
              placeholder="_W / _V (RGB)"
              onChange={(e) => setAdvField('end_rgb_files', e.target.value)}
            />
            <span className="suffix-tag">RGB</span>
          </div>
        </div>
        {escaneados > 0 && (
          <span className="field-hint">
            Analizando la carpeta… {escaneados} imágenes
            {' '}<button type="button" className="btn-ghost" onClick={() => api.analisisCancel()}>Cancelar</button>
          </span>
        )}
        {detected && (
          <span className={`field-hint ${detected.ok ? 'hint-ok' : 'hint-warn'}`}>
            {detected.ok
              ? `Autodetectado de la carpeta: ${detected.thermal ? `térmicas «${detected.thermal}»` : ''}${detected.thermal ? ', ' : ''}${detected.rgb ? `RGB «${detected.rgb}»` : (detected.thermal ? 'resto → RGB' : '')} (${detected.total} imágenes). Ajústalo si tu dron nombra distinto.`
              : `No pude deducir el sufijo de los nombres${detected.error ? ` (${detected.error})` : ''}. Ponlo a mano según el dron.`}
          </span>
        )}
        <span className="field-hint">
          Se rellena solo al elegir la carpeta origen. Las imágenes que acaben en el
          sufijo térmico van a TÉRMICA y el resto a RGB (o al revés con el sufijo RGB).
          Basta con uno.
        </span>
      </div>

      <label className="check">
        <input type="checkbox" checked={rename} onChange={(e) => setRename(e.target.checked)} />
        <span>Renombrar imágenes</span>
      </label>

      <button
        type="button"
        className={'adv-toggle' + (showAdvanced ? ' open' : '')}
        aria-expanded={showAdvanced}
        onClick={() => setShowAdvanced((v) => !v)}
      >
        <span className="adv-caret" aria-hidden="true">{showAdvanced ? '▾' : '▸'}</span>
        Modo avanzado
      </button>

      {showAdvanced && (
        <div className="adv-panel">
          {SPLIT_ADVANCED.map((sec) => (
            <section key={sec.title} className="adv-section">
              <h3 className="adv-section-title">{sec.title}</h3>
              <div className="block-grid">
                {sec.fields.map((f) => (
                  <Field key={f.name} f={f} value={adv[f.name]} onSet={setAdvField} onPick={pickAdv} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      <button className="btn-run" disabled={!canRun} onClick={handleRun}>
        {running ? 'Procesando…' : 'Ejecutar'}
      </button>
    </div>
  )
}
