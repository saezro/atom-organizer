import { useEffect, useState } from 'react'
import { api, onAnalisis } from '../bridge'
import { SPLIT_ADVANCED } from '../schema'
import { Field, initialState, buildParams } from '../TaskBlock'
import PasoCarpeta from './PasoCarpeta'

// Campos avanzados aplanados (todas las secciones) para el estado del panel.
const ADV_FIELDS = SPLIT_ADVANCED.flatMap((s) => s.fields)

// `onDestino(carpeta)` publica la «Carpeta final» (la ofrece la pestaña «Subir resultado»);
// `onVerSubida()` lleva a esa pestaña. La subida del resultado ya no vive aquí.
export default function PanelOrganizar({ origen, estadillos = [], inspeccion, ready, running, onRun, onDestino, onVerSubida }) {
  const [destino, setDestino] = useState('')
  const [destinoFull, setDestinoFull] = useState(null) // {count} si la salida no está vacía NI es un destino organizado
  const [destinoOrganizado, setDestinoOrganizado] = useState(false) // destino con manifiesto válido: se añade otra tanda
  const [fallidasError, setFallidasError] = useState('') // manifiesto ilegible: se muestra, no se oculta el botón
  const [fallidas, setFallidas] = useState(0) // filas 'fallido' del manifiesto del destino (mismo origen): "Reorganizar fallidas"
  const [rename, setRename] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [adv, setAdv] = useState(() => initialState(ADV_FIELDS))
  const [detected, setDetected] = useState(null)
  const [escaneados, setEscaneados] = useState(0)
  const [camaraRgb, setCamaraRgb] = useState('Z') // 'Z' (por defecto) | 'W'; solo se envía si la tanda trae _Z

  // Recorte de la Z (vista simple): 'auto' (tabla) | 'ninguno' (copia íntegra) | 'pct'.
  const [recorteZ, setRecorteZ] = useState('auto')
  const [recorteZPct, setRecorteZPct] = useState('')

  useEffect(() => { if (onDestino && destino) onDestino(destino) }, [destino, onDestino])

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
    setDetected(null)
    let vivo = true
    const off = onAnalisis((d) => {
      if (d.scope !== 'suffixes') return
      if (d.kind === 'scan') setEscaneados(d.done)
      if (d.kind === 'cancelled') { setEscaneados(0); setDetected({ ok: false, error: 'análisis cancelado' }) }
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
      if (!vivo) return
      // Si el análisis no llega a arrancar (el flag `_analizando` sigue tomado
      // por un escaneo anterior) la caja de sufijos se quedaba en 0, muda y sin
      // explicación. Se dice, que es la diferencia entre "no detecta nada" y
      // "no ha llegado a mirar".
      const res = await api.detectSuffixesStart(origen)
      if (vivo && res && res.started === false) {
        setEscaneados(0)
        setDetected({ ok: false, error: res.reason || 'No se pudo analizar la carpeta.' })
      }
    })()
    return () => { vivo = false; off() }
  }, [origen])

  // Selector de cámara RGB: solo si la autodetección ha visto ficheros `_Z` en la tanda.
  const hayZoom = !!(detected?.ok && detected.tokens && detected.tokens._Z > 0)

  // Recorte Z: solo aplica con cámara Zoom elegida y tanda con _Z.
  const recorteZActivo = hayZoom && camaraRgb === 'Z'
  const pctNum = Number(String(recorteZPct).replace(',', '.'))
  const pctInvalido = recorteZActivo && recorteZ === 'pct'
    && (String(recorteZPct).trim() === '' || !Number.isFinite(pctNum) || pctNum < 0 || pctNum > 100)

  // El backend exige estadillo para organizar (`estadillo.py`): sin él, no se lanza.
  const canRun = ready && !running && origen && destino && !destinoFull && estadillos.length > 0 && !pctInvalido
  // Por qué «Ejecutar» está deshabilitado: mismas condiciones que `canRun`.
  const falta = []
  if (!ready) falta.push('conexión con ATOM')
  if (!origen) falta.push('carpeta del vuelo')
  if (!destino) falta.push('carpeta final')
  else if (destinoFull) falta.push('carpeta final vacía (la elegida tiene archivos)')
  if (estadillos.length === 0) falta.push('estadillo')
  if (pctInvalido) falta.push('porcentaje de recorte Z (0-100)')

  // Re-consulta las fallidas si cambia el origen con el destino ya elegido
  // (solo cuentan las del MISMO origen).
  useEffect(() => {
    if (!destino || !origen) { setFallidas(0); setFallidasError(''); return }
    let vivo = true
    api.folderIsEmpty(destino, origen)
      .then((r) => { if (vivo) { setFallidas(r?.organizado ? (r?.fallidas ?? 0) : 0); setFallidasError(r?.fallidas_error || '') } })
      .catch((e) => { if (vivo) { setFallidas(0); setFallidasError(String(e?.message || e || 'error')) } })
    return () => { vivo = false }
  }, [destino, origen])

  function handleRun(soloFallidas = false) {
    const advanced = buildParams(ADV_FIELDS, adv)
    // Quién manda: la vista simple SOBRESCRIBE cropping_mode_auto/crop_percentage de
    // Avanzado solo cuando cámara=Z y la tanda trae _Z; en cualquier otro caso
    // mandan los valores de Avanzado. «Automático» = comportamiento de siempre.
    if (recorteZActivo) {
      if (recorteZ === 'auto') { advanced.cropping_mode_auto = true; advanced.crop_percentage = '0' }
      else if (recorteZ === 'ninguno') { advanced.cropping_mode_auto = false; advanced.crop_percentage = '0' }
      else { advanced.cropping_mode_auto = false; advanced.crop_percentage = String(pctNum) }
    }
    // Nombre de la inspección ya elegida en el paso anterior, para que el
    // modal de progreso muestre eso (lo que el operador reconoce) en vez del
    // fichero de estadillo. `_derive_plant` lo prioriza en el backend; si no
    // llega (p.ej. "Nueva inspección"), cae solo al estadillo como siempre.
    const nombreInspeccion = inspeccion?.etiqueta || inspeccion?.prefijo || ''
    // Orientación de la planta ("Horizontal" nunca gira, ver
    // atom_core.indice._consenso_de_angulo_por_vuelo): vacía mientras el
    // catálogo de la Suite no mande el campo (lib/organizer-catalogo.js).
    onRun('split_images', {
      origen, destino, estadillo: estadillos, rename,
      ...(soloFallidas ? { solo_fallidas: true } : {}),
      inspeccion: nombreInspeccion, orientacion: inspeccion?.orientacion || '',
      inspeccion_id: inspeccion?.id ?? null,
      ...(hayZoom ? { camara_rgb: camaraRgb } : {}),
    }, advanced)
  }

  return (
    <div className="card">
      <PasoCarpeta
        label="Carpeta final"
        value={destino}
        onChange={(p) => {
          setDestino(p)
          api.folderIsEmpty(p, origen)
            .then((r) => {
              const organizado = !!r?.organizado
              setDestinoOrganizado(organizado)
              setDestinoFull(r?.empty || organizado ? null : { count: r?.count ?? 0 })
              setFallidas(organizado ? (r?.fallidas ?? 0) : 0)
              setFallidasError(r?.fallidas_error || '')
            })
            .catch((e) => { setDestinoFull(null); setDestinoOrganizado(false); setFallidas(0); setFallidasError(String(e?.message || e || 'error')) })
        }}
        avisoNoVacia
        destinoOrganizado={destinoOrganizado}
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
        {escaneados === 0 && !detected && origen && (
          <span className="field-hint">Calculando…</span>
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

      {hayZoom && (
        <div className="field" data-testid="selector-camara-rgb">
          <span className="field-label">Cámara RGB</span>
          <div className="insp-chips" role="radiogroup" aria-label="Cámara RGB a recortar">
            {[['Z', 'Zoom (_Z)'], ['W', 'Wide (_W)']].map(([v, txt]) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={camaraRgb === v}
                className={'insp-chip' + (camaraRgb === v ? ' insp-chip-activo' : '')}
                onClick={() => setCamaraRgb(v)}
              >
                {txt}
              </button>
            ))}
          </div>
          <span className="field-hint">
            Zoom: se crea copia _CROP de la Z, la Wide queda sin recortar. Wide: se recorta la
            Wide, la Z queda sin recortar.
          </span>
        </div>
      )}

      {recorteZActivo && (
        <div className="field" data-testid="selector-recorte-z">
          <span className="field-label">Recorte Z</span>
          <div className="insp-chips" role="radiogroup" aria-label="Recorte de la cámara Zoom">
            {[['auto', 'Automático (tabla)'], ['ninguno', 'Sin recorte'], ['pct', 'Con %']].map(([v, txt]) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={recorteZ === v}
                className={'insp-chip' + (recorteZ === v ? ' insp-chip-activo' : '')}
                onClick={() => setRecorteZ(v)}
              >
                {txt}
              </button>
            ))}
          </div>
          {recorteZ === 'pct' && (
            <input
              type="number" min="0" max="100" step="any"
              className="glass-input"
              aria-label="Porcentaje de recorte Z"
              placeholder="0-100"
              value={recorteZPct}
              onChange={(e) => setRecorteZPct(e.target.value)}
            />
          )}
          {pctInvalido && (
            <span className="field-hint" role="alert">El porcentaje debe estar entre 0 y 100.</span>
          )}
          <span className="field-hint">
            Automático: según la tabla de modelos. Sin recorte: la copia _CROP es íntegra.
            Con %: recorte manual fijo. Sustituye lo de Avanzado mientras la cámara sea Zoom.
          </span>
        </div>
      )}

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

      <button className="btn-run" disabled={!canRun} onClick={() => handleRun(false)}>
        {running ? 'Procesando…' : 'Ejecutar'}
      </button>
      {!canRun && !running && falta.length > 0 && (
        <span className="field-hint" data-testid="falta-ejecutar">Falta: {falta.join(' / ')}</span>
      )}
      {fallidasError && (
        <span className="field-hint hint-warn">
          No se pudo comprobar si hay imágenes fallidas que reorganizar: {fallidasError}
        </span>
      )}
      {fallidas > 0 && (
        <>
          <span className="field-hint hint-warn">
            Este destino tiene {fallidas} imágenes fallidas del mismo origen. Reorganizarlas
            no toca las ya hechas.
          </span>
          <button type="button" className="btn-ghost" disabled={!canRun}
            onClick={() => handleRun(true)}>
            Reorganizar {fallidas} fallidas
          </button>
        </>
      )}
      {destino && onVerSubida && (
        <div className="field-row">
          <button type="button" className="btn-ghost" onClick={onVerSubida}>Ir a «Subir resultado»</button>
          <span className="field-hint">Al terminar de organizar, el resultado se sube desde esa pestaña.</span>
        </div>
      )}
    </div>
  )
}
