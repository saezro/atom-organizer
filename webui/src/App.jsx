import { useEffect, useState } from 'react'
import { api, onCloud, onProgress, whenBridgeReady } from './bridge'
import { SECTIONS, SPLIT_ADVANCED } from './schema'
import TaskBlock, { Field, initialState, buildParams } from './TaskBlock'
import ProgressModal from './ProgressModal'
import PreflightModal from './PreflightModal'
import UpdateModal from './UpdateModal'
import './App.css'

// Campos avanzados aplanados (todas las secciones) para el estado del panel.
const ADV_FIELDS = SPLIT_ADVANCED.flatMap((s) => s.fields)

const NAV = [
  { id: 'organizar', label: 'Organizar' },
  { id: 'bucket', label: 'SUBIR AL BUCKET' },
  { id: 'aerotools', label: 'AEROTOOLS' },
  { id: 'otros', label: 'OTROS EQUIPOS' },
  { id: 'config', label: 'CONFIGURACIÓN' },
]

function formatBytes(n) {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`
}

// « a las 17:42 » para la última comprobación de sesión. Devuelve cadena vacía
// si no hay fecha, para poder concatenarla sin condicionales en el JSX.
function horaCorta(epochSegundos) {
  if (!epochSegundos) return ''
  const d = new Date(epochSegundos * 1000)
  if (Number.isNaN(d.getTime())) return ''
  return ` a las ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

// Marca la fase `index` (1-based) como activa y las previas como hechas. Si el
// backend no mandó `plan` (tasks sin fases predefinidas), añade la fase que
// llega de forma dinámica.
function advancePhases(prev, data) {
  const { index, name, prev: closed } = data
  let next = [...prev]
  while (next.length < index) {
    next.push({ name: next.length === index - 1 ? name : '…', status: 'pending' })
  }
  return next.map((p, i) => {
    // Cerrar la fase que acaba de terminar con su duración y nº de errores.
    if (closed && i === closed.index - 1) {
      return {
        ...p,
        status: closed.errors > 0 ? 'error' : 'done',
        duration: closed.duration,
        errors: closed.errors,
      }
    }
    if (i < index - 1) return { ...p, status: p.status === 'error' ? 'error' : 'done' }
    if (i === index - 1) return { ...p, status: 'active', name: p.name || name }
    return p
  })
}

function App() {
  const [ready, setReady] = useState(false)
  const [section, setSection] = useState('organizar')
  const [running, setRunning] = useState(false)

  // Estado del modal de progreso (derivado de los eventos atom:progress).
  const [modalOpen, setModalOpen] = useState(false)
  const [plant, setPlant] = useState('')
  const [phases, setPhases] = useState([]) // [{name, status}]
  const [progress, setProgress] = useState(0) // % de la fase activa
  // Estadísticas en vivo de la fase (evento `stats`): imágenes analizadas de M,
  // reparto RGB / térmica y rotaciones 270/90/sin rotar del run.
  const [stats, setStats] = useState(null)
  const [detail, setDetail] = useState([]) // log crudo (colapsable)
  const [finished, setFinished] = useState(null) // null | {ok, msg}

  // Modal PREVIO (info del estadillo). null | {loading, info, task, params, advanced}
  const [preflight, setPreflight] = useState(null)

  // Versión REAL, la que dice version.py (vía updater.current_version()). Antes era
  // un literal en el header y quedó congelado en "v3.9" tras el reseteo de versionado.
  const [version, setVersion] = useState('')

  useEffect(() => {
    whenBridgeReady()
      .then(() => api.appVersion())
      .then((r) => setVersion(r?.version || ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    whenBridgeReady().then(() => setReady(true))
    return onProgress((d) => {
      switch (d.kind) {
        case 'plant':
          setPlant(d.text || '')
          break
        case 'plan':
          setPhases((d.data || []).map((name) => ({ name, status: 'pending' })))
          break
        case 'phase':
          setProgress(0)
          setPhases((prev) => advancePhases(prev, d.data))
          break
        case 'progress':
          setProgress(Math.max(0, Math.min(100, d.value)))
          break
        case 'stats':
          // Python ya reinicia los contadores por fase; aquí solo se pinta.
          setStats(d.data || null)
          break
        case 'log':
          if (d.text) setDetail((l) => [...l, d.text])
          break
        case 'summary':
          if (d.text && d.text.trim() && !/^_+$/.test(d.text)) {
            setDetail((l) => [...l, d.text])
          }
          break
        case 'done': {
          setRunning(false)
          const info = d.data || null
          const closed = info && info.last
          setPhases((prev) =>
            prev.map((p, i) => {
              if (closed && i === closed.index - 1) {
                return {
                  ...p,
                  status: closed.errors > 0 ? 'error' : 'done',
                  duration: closed.duration,
                  errors: closed.errors,
                }
              }
              // Fases que nunca corrieron o seguían activas: cerrar como
              // hechas, preservando las que ya quedaron marcadas con error.
              return p.status === 'error' ? p : { ...p, status: 'done' }
            })
          )
          setFinished(
            info
              ? {
                  ok: true,
                  // Ámbar tanto para errores no fatales como para avisos (SIN_ORDENAR).
                  warn: info.status === 'errors' || info.status === 'warning',
                  kind: info.status,
                  errors: info.errors,
                  warnings: info.warnings,
                  elapsed: info.elapsed,
                }
              : { ok: true }
          )
          break
        }
        case 'error':
          setRunning(false)
          setFinished({ ok: false, msg: d.text })
          break
        default:
          break
      }
    })
  }, [])

  // Entrada de "Ejecutar": si hay estadillo, primero el modal previo con la
  // info de vuelo; el pipeline no arranca hasta que el operador pulsa Comenzar.
  async function run(task, params, advanced) {
    if (task === 'split_images' && params.estadillo) {
      setPreflight({ loading: true, info: null, task, params, advanced })
      try {
        const info = await api.readEstadilloInfo(params.estadillo)
        setPreflight((p) => (p ? { ...p, loading: false, info } : p))
      } catch (e) {
        setPreflight((p) => (p ? { ...p, loading: false, info: { error: String(e) } } : p))
      }
      return
    }
    startRun(task, params, advanced)
  }

  function startRun(task, params, advanced) {
    // Reset del estado del modal para la nueva corrida.
    setPlant('')
    setPhases([])
    setProgress(0)
    setStats(null)
    setDetail([])
    setFinished(null)
    setModalOpen(true)
    setRunning(true)
    ;(async () => {
      try {
        const res = await api.runTask(task, params, advanced)
        if (res && res.started === false) {
          setRunning(false)
          setFinished({ ok: false, msg: res.reason || 'No se pudo iniciar.' })
        }
      } catch (e) {
        setRunning(false)
        setFinished({ ok: false, msg: String(e) })
      }
    })()
  }

  function confirmPreflight() {
    if (!preflight) return
    const { task, params, advanced } = preflight
    setPreflight(null)
    startRun(task, params, advanced)
  }

  return (
    <div className="app">
      <header className="brand">
        <h1>
          <span className="atom">ATOM</span> <span className="org">ORGANIZER</span>
        </h1>
        {version && <span className="ver">v{version}</span>}
      </header>

      <nav className="seg">
        {NAV.map((n) => (
          <button
            key={n.id}
            className={'seg-btn' + (section === n.id ? ' active' : '')}
            onClick={() => setSection(n.id)}
          >
            {n.label}
          </button>
        ))}
      </nav>

      <main>
        {section === 'organizar' ? (
          <OrganizarScreen ready={ready} running={running} onRun={run} />
        ) : section === 'bucket' ? (
          <BucketScreen ready={ready} />
        ) : section === 'config' ? (
          <ConfigScreen ready={ready} />
        ) : (
          <div className="section-blocks">
            {SECTIONS[section].blocks.map((b) => (
              <TaskBlock key={b.task} block={b} running={running} onRun={run} />
            ))}
          </div>
        )}
      </main>

      {preflight && (
        <PreflightModal
          info={preflight.info}
          loading={preflight.loading}
          onStart={confirmPreflight}
          onCancel={() => setPreflight(null)}
        />
      )}

      {modalOpen && (
        <ProgressModal
          plant={plant}
          phases={phases}
          progress={progress}
          stats={stats}
          detail={detail}
          finished={finished}
          onClose={() => setModalOpen(false)}
        />
      )}

      {/* Se pinta solo si Python avisa de que hay versión nueva (`atom:update`). */}
      <UpdateModal />
    </div>
  )
}

function OrganizarScreen({ ready, running, onRun }) {
  const [origen, setOrigen] = useState('')
  const [destino, setDestino] = useState('')
  const [destinoFull, setDestinoFull] = useState(null) // {count} si la salida no está vacía
  const [estadillo, setEstadillo] = useState('')
  const [rename, setRename] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [adv, setAdv] = useState(() => initialState(ADV_FIELDS))
  const [detected, setDetected] = useState(null)

  const setAdvField = (name, value) => setAdv((s) => ({ ...s, [name]: value }))

  async function pick(setter, kind) {
    const path = kind === 'file' ? await api.pickFile() : await api.pickFolder()
    if (path) setter(path)
  }

  // Al elegir la carpeta de salida: comprobar que está vacía. Una corrida sobre
  // residuos de otra previa genera duplicados `_1/_2` y errores de recorte, así
  // que el backend la rechaza; avisamos ya aquí y bloqueamos el botón.
  async function pickDestino() {
    const path = await api.pickFolder()
    if (!path) return
    setDestino(path)
    try {
      const r = await api.folderIsEmpty(path)
      setDestinoFull(r?.empty ? null : { count: r?.count ?? 0 })
    } catch {
      setDestinoFull(null)
    }
  }

  // Al elegir la carpeta origen: escanear los nombres y autorrellenar el sufijo
  // de separación (el operador ya no tiene que escribirlo; sigue editable).
  async function pickOrigen() {
    const path = await api.pickFolder()
    if (!path) return
    setOrigen(path)
    try {
      const d = await api.detectSuffixes(path)
      setDetected(d)
      if (d?.ok) {
        setAdv((s) => ({ ...s, end_thermo_files: d.thermal || '', end_rgb_files: d.rgb || '' }))
      }
    } catch {
      setDetected(null)
    }
  }

  async function pickAdv(field) {
    const path = field.type === 'file' ? await api.pickFile() : await api.pickFolder()
    if (path) setAdvField(field.name, path)
  }

  const canRun = ready && !running && origen && destino && !destinoFull

  function handleRun() {
    const advanced = buildParams(ADV_FIELDS, adv)
    onRun('split_images', { origen, destino, estadillo, rename }, advanced)
  }

  return (
    <div className="card">
      <h2 className="card-title">Organizar completo</h2>
      <FileField label="Carpeta origen" value={origen} onPick={pickOrigen} onType={setOrigen} />
      <FileField label="Carpeta final" value={destino} onPick={pickDestino} onType={setDestino} />
      {destinoFull && (
        <span className="field-hint hint-warn">
          La carpeta de salida no está vacía ({destinoFull.count} elemento{destinoFull.count === 1 ? '' : 's'}).
          Debe estar vacía: vacíala o elige otra, o la organización se rechazará (una corrida sobre
          residuos genera duplicados y errores de recorte).
        </span>
      )}
      <FileField
        label="Estadillo (opcional)"
        value={estadillo}
        onPick={() => pick(setEstadillo, 'file')}
        onType={setEstadillo}
        placeholder="Si se indica, organiza por planta"
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

// Subir al bucket «datos para organizar»: elegir la INSPECCIÓN a la que
// pertenecen los datos y la carpeta del vuelo, y subirla entera. El destino
// dentro del bucket lo nombra la inspección elegida — antes salía del nombre de
// la carpeta local, y eso hacía chocar dos «Nueva carpeta» de vuelos distintos.
const NUEVA = '__nueva__'

function BucketScreen({ ready }) {
  const [status, setStatus] = useState(null) // {configured, logged_in, email, bucket, help}
  const [carpeta, setCarpeta] = useState('')
  const [catalogo, setCatalogo] = useState(null) // {ok, inspecciones[], origen, error}
  const [eleccion, setEleccion] = useState('') // prefijo elegido | NUEVA | ''
  const [nueva, setNueva] = useState('') // nombre tecleado si eleccion === NUEVA
  const [plan, setPlan] = useState(null) // {ok, prefix, files, bytes, existing, error}
  const [busy, setBusy] = useState(false) // login o preparación en curso
  const [uploading, setUploading] = useState(false)
  const [force, setForce] = useState(false)
  const [lines, setLines] = useState([])
  const [result, setResult] = useState(null) // {ok, ...} | {error}
  // Estado REAL de la sesión, el que sale de preguntarle a Google. Aparte de
  // `status.logged_in`, que solo dice que hay un token guardado: puede estar
  // revocado o caducado y hasta que no se usa nadie lo sabe. Se separan porque
  // enseñar «sesión iniciada» sobre un token muerto es justo la confusión que
  // esta pantalla tiene que quitar.
  //   null = aún sin comprobar · {ok, text, validada_en} = ya comprobada
  const [sesion, setSesion] = useState(null)
  const [comprobando, setComprobando] = useState(false)

  // Lo que se manda al backend como destino. Con «nueva» va el texto crudo: la
  // normalización (acentos, espacios) es cosa del backend, que es quien manda.
  const prefijo = eleccion === NUEVA ? nueva.trim() : eleccion

  async function refresh() {
    try {
      setStatus(await api.cloudStatus())
    } catch (e) {
      setStatus({ ok: false, configured: false, help: String(e) })
    }
  }

  // Pregunta a Google si el token guardado sigue sirviendo. La respuesta llega
  // por el evento `atom:cloud` (kind 'session'), no por el return.
  async function comprobarSesion() {
    setComprobando(true)
    try {
      const r = await api.cloudVerify()
      if (r && r.started === false) {
        setComprobando(false)
        // Sin sesión guardada no hay nada que comprobar; no es un error.
        if (r.logged_in === false) setSesion(null)
      }
    } catch (e) {
      setComprobando(false)
      setSesion({ ok: false, text: String(e) })
    }
  }

  async function cargarInspecciones() {
    try {
      setCatalogo(await api.cloudInspecciones())
    } catch (e) {
      setCatalogo({ ok: false, inspecciones: [], error: String(e) })
    }
  }

  useEffect(() => {
    if (ready) {
      refresh()
      cargarInspecciones()
      // Al abrir la pantalla se confirma la sesión de una vez, en vez de que el
      // operador se entere de que caducó cuando ya lleva media subida.
      comprobarSesion()
    }
  }, [ready])

  useEffect(
    () =>
      onCloud((d) => {
        switch (d.kind) {
          case 'login':
            setBusy(false)
            if (d.ok) {
              refresh()
              // El canje del código acaba de funcionar: la sesión está viva sin
              // necesidad de volver a preguntar.
              setSesion({ ok: true, text: 'Sesión válida.', validada_en: Date.now() / 1000 })
              // El catálogo vive en el bucket: hasta ahora no había con qué
              // pedirlo, así que se baja en cuanto hay sesión.
              cargarInspecciones()
            } else setResult({ error: d.text || 'No se pudo iniciar sesión.' })
            break
          case 'session':
            setComprobando(false)
            setSesion({ ok: !!d.ok, text: d.text, validada_en: d.validada_en })
            // Siempre se relee el estado, no solo al fallar: una sesión revocada
            // deja de estar «iniciada» también en el backend (el refresh fallido
            // borra el token), y al revés, la comprobación puede terminar justo
            // después de que el usuario cerrara sesión. En ambos casos la UI
            // seguiría enseñando algo que ya no es verdad.
            refresh()
            break
          case 'start':
            setLines([`Subiendo ${d.files} ficheros (${formatBytes(d.bytes)}) a ${d.prefix}/`])
            break
          case 'log':
            if (d.text) setLines((l) => [...l, d.text])
            break
          case 'done':
            setUploading(false)
            setResult(d)
            break
          case 'error':
            setUploading(false)
            setResult({ error: d.text })
            break
          default:
            break
        }
      }),
    []
  )

  async function login() {
    setResult(null)
    setBusy(true)
    const r = await api.cloudLogin()
    if (r && r.started === false) {
      setBusy(false)
      setResult({ error: r.reason })
    }
  }

  async function logout() {
    await api.cloudLogout()
    setPlan(null)
    setSesion(null)
    refresh()
  }

  async function pickCarpeta() {
    const path = await api.pickFolder()
    if (!path) return
    setCarpeta(path)
    setResult(null)
    setForce(false)
    await preparar(path, prefijo)
  }

  // Cambiar de inspección cambia el destino, así que el plan anterior (y sobre
  // todo su aviso de «ya hay datos ahí») deja de valer: se recalcula.
  function elegir(valor) {
    setEleccion(valor)
    setPlan(null)
    setForce(false)
    setResult(null)
  }

  async function preparar(path, pref) {
    if (!pref) return
    setBusy(true)
    try {
      setPlan(await api.cloudPrepare(path, pref))
    } catch (e) {
      setPlan({ ok: false, error: String(e) })
    } finally {
      setBusy(false)
    }
  }

  async function subir() {
    setResult(null)
    setLines([])
    setUploading(true)
    const r = await api.cloudUpload(carpeta, force, prefijo)
    if (r && r.started === false) {
      setUploading(false)
      setResult({ error: r.reason })
    }
  }

  const logged = !!status?.logged_in
  const ocupado = busy || uploading
  const inspecciones = catalogo?.inspecciones || []
  const puedeSubir = ready && logged && !!prefijo && plan?.ok && !ocupado

  if (status && status.configured === false) {
    return (
      <div className="card">
        <h2 className="card-title">Subir al bucket</h2>
        <span className="field-hint hint-warn" style={{ whiteSpace: 'pre-line' }}>
          {status.help}
        </span>
      </div>
    )
  }

  return (
    <div className="card">
      <h2 className="card-title">Subir al bucket</h2>

      <div className="field">
        <span className="field-label">Cuenta de Google</span>
        {logged ? (
          <>
            <div className="field-row">
              <input className="glass-input" type="text" value={status.email || 'sesión iniciada'} readOnly />
              {/* Una sesión caducada no se arregla cerrándola: se vuelve a
                  entrar. El botón principal cambia según el estado real. */}
              {sesion && !sesion.ok ? (
                <button type="button" className="btn-ghost" onClick={login} disabled={!ready || busy}>
                  {busy ? 'Esperando…' : 'Volver a iniciar sesión'}
                </button>
              ) : null}
              <button type="button" className="btn-ghost" onClick={logout} disabled={ocupado}>
                Cerrar sesión
              </button>
            </div>
            <span className={`field-hint ${sesion ? (sesion.ok ? 'hint-ok' : 'hint-warn') : ''}`}>
              {comprobando || !sesion
                ? 'Comprobando que la sesión sigue activa…'
                : sesion.ok
                  ? `Sesión activa y comprobada${horaCorta(sesion.validada_en)}.`
                  : sesion.text || 'La sesión ya no es válida. Vuelve a iniciar sesión.'}
              {!comprobando ? (
                <>
                  {' '}
                  <button type="button" className="link-inline" onClick={comprobarSesion}>
                    Comprobar de nuevo
                  </button>
                </>
              ) : null}
            </span>
          </>
        ) : (
          <>
            <div className="field-row">
              <input
                className="glass-input"
                type="text"
                value="Sin iniciar sesión"
                readOnly
              />
              <button type="button" className="btn-ghost" onClick={login} disabled={!ready || busy}>
                {busy ? 'Esperando…' : 'Iniciar sesión'}
              </button>
            </div>
            {/* Por qué no hay sesión, cuando el motivo no es «nunca entraste»:
                un perfil copiado de otro equipo deja el almacén ilegible y sin
                esto el operador solo vería un «sin iniciar sesión» inexplicable. */}
            {status?.aviso ? (
              <span className="field-hint hint-warn">{status.aviso}</span>
            ) : null}
          </>
        )}
        <span className="field-hint">
          Se abre el navegador para identificarte con tu cuenta de Aerotools. Los datos van
          al bucket «{status?.bucket || 'datos_para_organizar'}»; quién puede subir lo decide
          el permiso de la cuenta, no la aplicación.
        </span>
      </div>

      <div className="field">
        <span className="field-label">Inspección</span>
        <div className="field-row">
          <select
            className="glass-input"
            value={eleccion}
            onChange={(e) => elegir(e.target.value)}
            disabled={ocupado}
          >
            <option value="">— Elige la inspección —</option>
            {inspecciones.map((i) => (
              <option key={i.prefijo} value={i.prefijo}>
                {i.etiqueta}
              </option>
            ))}
            <option value={NUEVA}>+ Inspección nueva…</option>
          </select>
          <button
            type="button"
            className="btn-ghost"
            onClick={cargarInspecciones}
            disabled={ocupado}
          >
            Actualizar lista
          </button>
        </div>
        {eleccion === NUEVA && (
          <input
            className="glass-input"
            type="text"
            value={nueva}
            onChange={(e) => {
              setNueva(e.target.value)
              setPlan(null)
            }}
            placeholder="Empresa--Planta--Año--Tipo"
          />
        )}
        <span className="field-hint">
          {catalogo?.error
            ? catalogo.error
            : catalogo?.origen === 'cache'
              ? `${inspecciones.length} inspecciones de la última descarga (no se pudo consultar ahora).`
              : catalogo?.origen === 'bucket'
                // Respaldo: la Suite no respondió y esta lista se genera a mano,
                // así que puede no traer las inspecciones creadas hoy. Decirlo
                // evita que el operador busque una que existe y no aparece.
                ? `${inspecciones.length} inspecciones de la lista de respaldo (puede estar desactualizada).`
                : `${inspecciones.length} inspecciones. Los datos se guardarán en «${prefijo || '…'}/».`}
        </span>
      </div>

      <FileField
        label="Carpeta a subir"
        value={carpeta}
        onPick={pickCarpeta}
        onType={setCarpeta}
        placeholder="La carpeta completa del vuelo"
      />
      {carpeta && prefijo && !plan && (
        <button
          type="button"
          className="btn-ghost"
          onClick={() => preparar(carpeta, prefijo)}
          disabled={ocupado}
        >
          Comprobar carpeta
        </button>
      )}
      {carpeta && !prefijo && (
        <span className="field-hint hint-warn">
          Elige la inspección a la que pertenece este vuelo antes de subirlo.
        </span>
      )}

      {plan && plan.ok && (
        <span className="field-hint hint-ok">
          {plan.files} ficheros · {formatBytes(plan.bytes)} → {plan.prefix}/
        </span>
      )}
      {plan && !plan.ok && <span className="field-hint hint-warn">{plan.error}</span>}

      {plan?.ok && plan.existing > 0 && (
        <>
          <span className="field-hint hint-warn">
            En «{plan.prefix}/» ya hay datos subidos. Si es esta misma carpeta, puedes
            continuar donde se quedó. Si son datos de otro vuelo, comprueba que la inspección
            elegida es la correcta: las imágenes de dron se llaman igual en todos los vuelos y
            subir encima pisaría lo anterior.
          </span>
          <label className="check">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            <span>Continuar la subida en ese destino</span>
          </label>
        </>
      )}

      {lines.length > 0 && (
        <ul className="config-list">
          {lines.slice(-6).map((l, i) => (
            <li key={i} className="config-item">
              <span className="config-model">{l}</span>
            </li>
          ))}
        </ul>
      )}

      {result && result.error && <span className="field-hint hint-warn">{result.error}</span>}
      {result && !result.error && (
        <span className={`field-hint ${result.ok ? 'hint-ok' : 'hint-warn'}`}>
          {result.cancelled
            ? `Subida cancelada. ${result.uploaded} ficheros subidos; al volver a lanzarla continúa donde se quedó.`
            : result.ok
              ? `Subida completa: ${result.uploaded} ficheros (${formatBytes(result.bytes)}) a ${result.mbps} Mbps.` +
                (result.skipped ? ` ${result.skipped} ya estaban.` : '')
              : `Terminó con ${result.failed_total} fallo(s): ${(result.failed || [])
                  .map((f) => `${f.objeto} (${f.error})`)
                  .join('; ')}. Vuelve a lanzarla: sólo reintenta lo que falta.`}
        </span>
      )}

      {uploading ? (
        <button className="btn-run" onClick={() => api.cloudCancel()}>
          Cancelar subida
        </button>
      ) : (
        <button className="btn-run" disabled={!puedeSubir} onClick={subir}>
          {busy ? 'Comprobando…' : 'Subir al bucket'}
        </button>
      )}
    </div>
  )
}

function ConfigScreen({ ready }) {
  const [ruta, setRuta] = useState('')
  const [models, setModels] = useState([]) // [{model, pct}]
  const [mName, setMName] = useState('')
  const [mPct, setMPct] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [saved, setSaved] = useState(null) // null | {ok, msg}

  // Carga inicial de la config persistente.
  useEffect(() => {
    if (!ready) return
    api
      .readConfig()
      .then((c) => {
        setRuta(c?.ruta_thermoviewer || '')
        const pbm = c?.percentage_by_models || {}
        setModels(Object.entries(pbm).map(([model, pct]) => ({ model, pct: String(pct) })))
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [ready])

  async function pickRuta() {
    const path = await api.pickFile()
    if (path) setRuta(path)
  }

  // Añade o actualiza (case-insensitive, por modelo en MAYÚSCULAS) como el Qt.
  function addOrUpdate() {
    const name = mName.trim().toUpperCase()
    const pct = parseInt(mPct, 10)
    if (!name || Number.isNaN(pct)) return
    setModels((prev) => {
      const i = prev.findIndex((m) => m.model === name)
      if (i >= 0) {
        const next = [...prev]
        next[i] = { model: name, pct: String(pct) }
        return next
      }
      return [...prev, { model: name, pct: String(pct) }]
    })
    setMName('')
    setMPct('')
    setSaved(null)
  }

  function editItem(m) {
    setMName(m.model)
    setMPct(String(m.pct))
  }

  function removeItem(model) {
    setModels((prev) => prev.filter((m) => m.model !== model))
    setSaved(null)
  }

  async function save() {
    const percentage_by_models = {}
    for (const m of models) {
      const pct = parseInt(m.pct, 10)
      if (m.model && !Number.isNaN(pct)) percentage_by_models[m.model] = pct
    }
    try {
      const res = await api.writeConfig({ ruta_thermoviewer: ruta, percentage_by_models })
      setSaved(res?.ok ? { ok: true, msg: 'Configuración guardada.' } : { ok: false, msg: res?.error || 'No se pudo guardar.' })
    } catch (e) {
      setSaved({ ok: false, msg: String(e) })
    }
  }

  return (
    <div className="card">
      <h2 className="card-title">Configuración</h2>

      <FileField
        label="Ruta de ThermoViewer.exe"
        value={ruta}
        onPick={pickRuta}
        onType={setRuta}
        placeholder="Solo Windows · necesario para la extracción térmica (TMC)"
      />
      <span className="field-hint">
        Ejecutable de ThermoViewer instalado en el equipo. Se usa en AEROTOOLS → «Térmica ·
        extracción». Si no está en su ruta por defecto, indícalo aquí.
      </span>

      <div className="field">
        <span className="field-label">% de recorte RGB por modelo de dron</span>
        <div className="suffix-row">
          <div className="suffix-cell">
            <input
              className="glass-input"
              type="text"
              value={mName}
              placeholder="Modelo (p.ej. M4T)"
              onChange={(e) => setMName(e.target.value)}
            />
          </div>
          <div className="suffix-cell">
            <input
              className="glass-input"
              type="number"
              min="0"
              max="100"
              value={mPct}
              placeholder="%"
              onChange={(e) => setMPct(e.target.value)}
            />
          </div>
          <button type="button" className="btn-ghost" onClick={addOrUpdate}>
            Añadir / actualizar
          </button>
        </div>
        <span className="field-hint">
          Porcentaje de recorte automático del RGB para cada modelo. Si aparece un dron nuevo
          no listado, añádelo aquí o el recorte automático fallará para ese modelo.
        </span>

        {models.length > 0 ? (
          <ul className="config-list">
            {models.map((m) => (
              <li key={m.model} className="config-item">
                <button type="button" className="config-item-main" onClick={() => editItem(m)}>
                  <span className="config-model">{m.model}</span>
                  <span className="config-pct">{m.pct}%</span>
                </button>
                <button
                  type="button"
                  className="config-del"
                  title="Eliminar"
                  onClick={() => removeItem(m.model)}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <span className="field-hint hint-warn">
            {loaded ? 'No hay modelos configurados todavía.' : 'Cargando…'}
          </span>
        )}
      </div>

      {saved && (
        <span className={`field-hint ${saved.ok ? 'hint-ok' : 'hint-warn'}`}>{saved.msg}</span>
      )}

      <button className="btn-run" disabled={!ready} onClick={save}>
        Guardar configuración
      </button>
    </div>
  )
}

function FileField({ label, value, onPick, onType, placeholder }) {
  // Si se pasa `onType`, el campo es editable → se puede escribir o PEGAR la
  // ruta a mano (fallback cuando el diálogo nativo no abre, p.ej. en Windows).
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <div className="field-row">
        <input
          className="glass-input"
          type="text"
          value={value}
          placeholder={placeholder || 'Elige, o escribe/pega la ruta aquí…'}
          onChange={onType ? (e) => onType(e.target.value) : undefined}
          readOnly={!onType}
          spellCheck={false}
        />
        <button type="button" className="btn-ghost" onClick={onPick}>
          Elegir…
        </button>
      </div>
    </label>
  )
}

export default App
