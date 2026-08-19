// Puente con el backend Python (pywebview) o con el servidor HTTP+SSE del
// modo `--server` (Raspberry Pi). Todo el acceso a `window.pywebview` pasa
// por aquí para aislar la app de React del shell.

// El Organizer corre en dos shells: pywebview/Qt (Windows) y un navegador
// contra el modo `--server` (Raspberry Pi, donde se sirve la UI por HTTP para
// no arrastrar un segundo Chromium ni depender de dialogos nativos).
// La deteccion es por presencia: si `window.pywebview` no aparece, es servidor.
// No se puede decidir al importar el modulo porque en Qt la inyeccion es
// asincrona; por eso `whenBridgeReady` da un plazo antes de rendirse.
const ESPERA_PYWEBVIEW_MS = 1500
let modoServidor = null

// Bajo pywebview la pagina se carga por `file://` (`resolve_target` devuelve la
// ruta del index, app_webview.py). El modo `--server` siempre sirve por HTTP.
// El protocolo es, por tanto, una senal POSITIVA y sincrona del shell, a
// diferencia de `window.pywebview`, que en Qt aparece tarde: si esto es
// `file://`, es pywebview aunque el bridge aun no este inyectado.
function esFile() {
  return window.location.protocol === 'file:'
}

// Heuristica sincrona para quien necesite el modo antes de que
// `whenBridgeReady` concluya. Con `file://` la respuesta ya es definitiva (no
// hay servidor posible); fuera de `file://` se responde por la presencia de
// `window.pywebview` AHORA MISMO.
function modoServidorActual() {
  if (modoServidor !== null) return modoServidor
  if (esFile()) return false
  return !window.pywebview
}

export function isServerMode() {
  return modoServidorActual()
}

// El puente queda "ready" cuando pywebview inyecta `window.pywebview.api`.
// En WebView2 (Windows) el evento one-shot `pywebviewready` puede dispararse
// ANTES de que React registre el listener (carrera de arranque): el evento se
// pierde y ninguna llamada al backend resuelve nunca. Por eso no nos fiamos
// solo del evento: polleamos `window.pywebview.api` con un intervalo y
// resolvemos en cuanto exista, por cualquiera de las tres vías. Inocuo en
// Linux/Qt (ahí gana el check inicial o el evento y el intervalo se limpia).
// La promesa se memoiza: el modo, una vez determinado, es definitivo, y sin
// esto CADA llamada al bridge arrancaba su propio plazo de
// ESPERA_PYWEBVIEW_MS. En pywebview no se notaba (el check inicial
// cortocircuita en cuanto Qt inyecta), pero en modo servidor `window.pywebview`
// no aparece JAMAS, asi que no habia cortocircuito posible y toda llamada
// pagaba 1500 ms integros antes del fetch. Abrir el selector encadenaba dos
// (`pickFolder` + `defaultDir`) = ~3 s de espera pura con el backend
// respondiendo en 4 ms.
let promesaBridge = null

export function whenBridgeReady() {
  if (modoServidor !== null) return Promise.resolve()
  if (promesaBridge) return promesaBridge
  promesaBridge = new Promise((resolve) => {
    if (window.pywebview?.api) { modoServidor = false; return resolve() }
    let done = false
    let timer = null
    const finish = (servidor) => {
      if (done) return
      done = true
      modoServidor = servidor
      if (timer !== null) clearInterval(timer)
      clearTimeout(plazo)
      window.removeEventListener('pywebviewready', alListo)
      if (servidor) conectarEventos()
      resolve()
    }
    const alListo = () => finish(false)
    window.addEventListener('pywebviewready', alListo, { once: true })
    timer = setInterval(() => { if (window.pywebview?.api) finish(false) }, 100)
    // Si en este plazo no aparecio, no va a aparecer: es un navegador normal.
    // PERO solo si NO estamos en `file://`: ahi es pywebview con la inyeccion
    // lenta (arranque en frio de PyInstaller, antivirus). Rendirse por reloj
    // seria irreversible —`finish` limpia intervalo y listener— y dejaria
    // Windows hablando por `fetch` con un servidor que no existe, ademas de un
    // EventSource contra `file:///events`. En ese caso seguimos esperando
    // indefinidamente, que es como se comportaba antes del modo servidor.
    const plazo = setTimeout(() => { if (!esFile()) finish(true) }, ESPERA_PYWEBVIEW_MS)
  })
  return promesaBridge
}

let fuenteEventos = null

function conectarEventos() {
  if (fuenteEventos) return
  fuenteEventos = new EventSource('/events')
  for (const canal of ['atom:progress', 'atom:update', 'atom:cloud']) {
    fuenteEventos.addEventListener(canal, (e) => {
      window.dispatchEvent(new CustomEvent(canal, { detail: JSON.parse(e.data) }))
    })
  }
}

// El SSE se conecta SOLO desde `whenBridgeReady` al confirmar modo servidor.
// No se conecta al cargar el modulo aunque `window.pywebview` aun no exista:
// en Windows la inyeccion de Qt es asincrona y la pagina se carga por `file://`,
// asi que un EventSource especulativo apuntaria a `file:///events`, fallaria y
// se reconectaria en bucle para siempre. Adelantarlo tampoco aporta nada: los
// eventos solo los emite Python en respuesta a una accion, y toda accion pasa
// por `call`, que espera a `whenBridgeReady`.

async function call(method, ...args) {
  await whenBridgeReady()
  if (modoServidor) {
    const r = await fetch(`/api/${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args }),
    })
    const cuerpo = await r.json()
    if (!r.ok) throw new Error(cuerpo.error || `Error llamando a «${method}»`)
    return cuerpo.result
  }
  const fn = window.pywebview.api[method]
  if (!fn) throw new Error(`El bridge no expone «${method}»`)
  return fn(...args)
}

// En modo servidor (Raspberry Pi) no hay diálogo nativo de ficheros: la webui
// registra aquí su propio explorador (`FolderPicker`) y `pickFolder`/`pickFile`
// lo usan en su lugar, sin que los call sites tengan que enterarse. Bajo
// pywebview (Windows) `pickerUI` se queda a null y la ruta nativa sigue igual.
let pickerUI = null

export function registerPicker(fn) {
  pickerUI = fn
}

// El modo se consulta con `whenBridgeReady` YA resuelto, no con la heuristica
// sincrona: elegir explorador es una decision funcional, y bajo `file://` la
// heuristica puede decir «servidor» en falso mientras Qt inyecta.
async function pick(mode, method) {
  await whenBridgeReady()
  if (modoServidor && pickerUI) return pickerUI(mode)
  return call(method)
}

export const api = {
  ping: (who) => call('ping', who),
  pickFolder: () => pick('folder', 'pick_folder'),
  pickFile: () => pick('file', 'pick_file'),
  // Listado de un directorio para el explorador in-app (`FolderPicker`), la
  // alternativa al diálogo nativo en modo servidor. Sin argumento lista el
  // home del usuario. Devuelve {ok, path, parent, dirs[], files[]} |
  // {ok:false, error}; `parent` es null en la raíz.
  listDir: (path) => call('list_dir', path ?? null),
  // Carpeta con la que debe arrancar el explorador (`FolderPicker`). En
  // Windows/pywebview siempre el home; en la Pi, el disco USB de
  // inspecciones si hay uno montado. En Linux devuelve el mismo shape que
  // listDir ({ok, path, parent, dirs[], files[]}), o sea con el listado ya
  // dentro: una sola llamada HTTP en vez de dos encadenadas. Solo se usa en
  // modo servidor; en escritorio el picker ni la llama.
  defaultDir: () => call('default_dir'),
  runOrganize: (params, advanced) => call('run_organize', params, advanced ?? null),
  runTask: (task, params, advanced) => call('run_task', task, params, advanced ?? null),
  // Lectura sincrónica del estadillo para el modal previo (pilotos, dron,
  // nº de vuelos, franjas horarias). Devuelve el dict de info o {error}.
  readEstadilloInfo: (path) => call('read_estadillo_info', path),
  // Autodetección del sufijo de separación desde los nombres de la carpeta
  // origen (DJI: térmicas `_T`). Devuelve {ok, thermal, rgb, tokens, total}.
  detectSuffixes: (origen) => call('detect_suffixes', origen),
  // ¿La carpeta de salida está vacía? Feedback previo al arrancar (el backend
  // igualmente rechaza no-vacía). Devuelve {exists, empty, count}.
  folderIsEmpty: (path) => call('folder_is_empty', path),
  // Configuración persistente: ruta de ThermoViewer.exe + % de recorte RGB por
  // modelo de dron. read devuelve {ruta_thermoviewer, percentage_by_models};
  // write persiste y devuelve {ok, path} | {ok:false, error}.
  readConfig: () => call('read_config'),
  writeConfig: (data) => call('write_config', data),
  // Actualizaciones (GitHub Releases). appVersion devuelve {version, platform};
  // checkUpdate {ok, update_available, current, latest, notes, asset_url,
  // asset_size, can_install}; downloadUpdate arranca la descarga (el progreso
  // llega por el evento `atom:update`); installUpdate lanza el instalador
  // silencioso, que cierra y reabre la app.
  appVersion: () => call('app_version'),
  checkUpdate: () => call('check_update'),
  downloadUpdate: (url, size) => call('download_update', url, size ?? 0),
  installUpdate: (path) => call('install_update', path ?? null),
  // Subida al bucket «datos para organizar». cloudStatus devuelve
  // {configured, logged_in, email, bucket, help?}; cloudLogin abre el navegador
  // y responde por el evento `atom:cloud`; cloudPrepare {ok, prefix, files,
  // bytes, existing}; cloudUpload arranca la subida (progreso por `atom:cloud`).
  // cloudInspecciones devuelve el catálogo {ok, inspecciones[], origen, error}.
  // `origen` dice de dónde salió y hay que enseñarlo: 'api' es ATOM Suite (al
  // día), 'bucket' es el `_inspecciones.json` de respaldo (se genera a mano y
  // puede estar viejo) y 'cache' es la última descarga local. El prefijo
  // destino ya NO se deriva del nombre de la carpeta: lo manda la UI con la
  // inspección elegida.
  // cloudStatus dice lo que se sabe SIN red (hay token guardado y de quién);
  // cloudVerify pregunta a Google si ese token sigue sirviendo y contesta por
  // el evento `atom:cloud` (kind 'session'). Son dos cosas distintas y la UI
  // las enseña por separado: «sesión recordada» no es «sesión válida».
  // `cloud_status` puede traer ademas `pairing: true`: senal de que este
  // equipo (Raspberry Pi) solo puede identificarse por vinculo QR con el
  // movil, nunca abriendo un navegador propio a Google. cloudPairStart arranca
  // un vinculo nuevo ({ok, pair_id, url, expires_in} | {ok:false, error});
  // cloudPairPoll consulta su estado ({estado: 'pendiente'|'expirado'|'listo',
  // email?}) — con 'listo' la sesion YA esta activa por dentro, la UI solo
  // tiene que refrescar `cloudStatus`.
  cloudStatus: () => call('cloud_status'),
  cloudVerify: () => call('cloud_verify'),
  cloudLogin: () => call('cloud_login'),
  cloudLogout: () => call('cloud_logout'),
  cloudPairStart: () => call('cloud_pair_start'),
  cloudPairPoll: (pairId) => call('cloud_pair_poll', pairId),
  cloudInspecciones: () => call('cloud_inspecciones'),
  cloudPrepare: (folder, prefix) => call('cloud_prepare', folder, prefix ?? null),
  cloudUpload: (folder, force, prefix, inspeccionId) =>
    call('cloud_upload', folder, force ?? false, prefix ?? null, inspeccionId ?? null),
  cloudCancel: () => call('cloud_cancel'),
  // Ubicación canónica del estadillo en el bucket (independiente de organizar
  // y de la subida de jornada). estadilloValidar es síncrono y no sube nada:
  // devuelve {ok, error, vuelos_detectados, filas_con_problemas} para enseñar
  // qué se ha entendido ANTES de permitir subir. estadilloSubir arranca la
  // subida en background (progreso por `atom:cloud`, `scope: 'estadillo'`) y
  // el primer argumento es el PREFIJO de la inspección elegida (no una carpeta
  // local): internamente se pasa a `prefijo_desde_carpeta`, igual que
  // `cloudUpload`/`cloudPrepare`.
  estadilloValidar: (rutas) => call('estadillo_validar', rutas),
  estadilloSubir: (prefijo, rutas) => call('estadillo_subir', prefijo, rutas),
  // Detecta si la inspección ya tiene un estadillo subido en el bucket.
  // Fail-open: {existe, error} — con `error` relleno se trata como `existe:
  // false` sin bloquear al operador (ver App.jsx, useEffect sobre `prefijo`).
  estadilloExistente: (prefijo) => call('estadillo_existente', prefijo),
  // Escanea una carpeta (2 niveles) y devuelve los estadillos que encuentra YA
  // resumidos: {rutas, n_estadillos, info, error}, con `info` = el mismo objeto
  // de `read_estadillo_info` (fechas, pilotos, drones, num_vuelos...). No
  // encontrar ninguno no es un error: `n_estadillos: 0`, `info: null`.
  estadillosDetectar: (carpeta) => call('estadillos_detectar', carpeta),
}

// Python empuja progreso del pipeline con:
//   window.dispatchEvent(new CustomEvent('atom:progress', {detail: {...}}))
// detail = { kind, text?, value?, data? }
//   kind 'log'|'summary' -> text
//   kind 'progress'      -> value (% de la fase activa)
//   kind 'plant'         -> text (nombre de planta, título del modal)
//   kind 'plan'          -> data (list[str] de fases activas)
//   kind 'phase'         -> data ({index, total, name})
//   kind 'stats'         -> data ({phase_index, phase_name, done, total, rgb,
//                           termica, rot270, rot90, rot_none}) — imágenes
//                           analizadas de la fase y rotaciones del run
//   kind 'done'|'error'  -> (error trae text)
export function onProgress(handler) {
  const wrapped = (e) => handler(e.detail)
  window.addEventListener('atom:progress', wrapped)
  return () => window.removeEventListener('atom:progress', wrapped)
}

// Eventos del updater (Python → JS), canal aparte del progreso del pipeline:
//   kind 'available'  -> data (resultado de check_update)
//   kind 'progress'   -> value (%), done, total (bytes)
//   kind 'downloaded' -> path del instalador en %TEMP%
//   kind 'error'      -> text
export function onUpdate(handler) {
  const wrapped = (e) => handler(e.detail)
  window.addEventListener('atom:update', wrapped)
  return () => window.removeEventListener('atom:update', wrapped)
}

// Eventos de la subida al bucket (Python → JS), canal propio:
//   kind 'login'   -> ok, email | text (error)
//   kind 'session' -> ok (la sesión guardada sigue valiendo), email,
//                     validada_en (epoch), text (motivo si ok=false)
//   kind 'start' -> files, bytes, prefix
//   kind 'log'   -> text (línea ya formateada por cloud_upload)
//   kind 'done'  -> ok, uploaded, skipped, bytes, elapsed, mbps, failed[]
//   kind 'error' -> text
export function onCloud(handler) {
  const wrapped = (e) => handler(e.detail)
  window.addEventListener('atom:cloud', wrapped)
  return () => window.removeEventListener('atom:cloud', wrapped)
}
