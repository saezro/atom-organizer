// Puente con el backend Python (pywebview). Todo el acceso a `window.pywebview`
// pasa por aquí para aislar la app de React del shell.

// El puente queda "ready" cuando pywebview inyecta `window.pywebview.api`.
// En WebView2 (Windows) el evento one-shot `pywebviewready` puede dispararse
// ANTES de que React registre el listener (carrera de arranque): el evento se
// pierde y ninguna llamada al backend resuelve nunca. Por eso no nos fiamos
// solo del evento: polleamos `window.pywebview.api` con un intervalo y
// resolvemos en cuanto exista, por cualquiera de las tres vías. Inocuo en
// Linux/Qt (ahí gana el check inicial o el evento y el intervalo se limpia).
export function whenBridgeReady() {
  return new Promise((resolve) => {
    if (window.pywebview?.api) return resolve()
    let done = false
    let timer = null
    const finish = () => {
      if (done) return
      done = true
      if (timer !== null) clearInterval(timer)
      window.removeEventListener('pywebviewready', finish)
      resolve()
    }
    window.addEventListener('pywebviewready', finish, { once: true })
    timer = setInterval(() => {
      if (window.pywebview?.api) finish()
    }, 100)
  })
}

async function call(method, ...args) {
  await whenBridgeReady()
  const fn = window.pywebview.api[method]
  if (!fn) throw new Error(`El bridge no expone «${method}»`)
  return fn(...args)
}

export const api = {
  ping: (who) => call('ping', who),
  pickFolder: () => call('pick_folder'),
  pickFile: () => call('pick_file'),
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
  // cloudInspecciones devuelve el catálogo {ok, inspecciones[], origen, error}:
  // sale de `_inspecciones.json` en el bucket, no de la BD de Aerotools. El
  // prefijo destino ya NO se deriva del nombre de la carpeta: lo manda la UI
  // con la inspección elegida.
  cloudStatus: () => call('cloud_status'),
  cloudLogin: () => call('cloud_login'),
  cloudLogout: () => call('cloud_logout'),
  cloudInspecciones: () => call('cloud_inspecciones'),
  cloudPrepare: (folder, prefix) => call('cloud_prepare', folder, prefix ?? null),
  cloudUpload: (folder, force, prefix) =>
    call('cloud_upload', folder, force ?? false, prefix ?? null),
  cloudCancel: () => call('cloud_cancel'),
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
//   kind 'login' -> ok, email | text (error)
//   kind 'start' -> files, bytes, prefix
//   kind 'log'   -> text (línea ya formateada por cloud_upload)
//   kind 'done'  -> ok, uploaded, skipped, bytes, elapsed, mbps, failed[]
//   kind 'error' -> text
export function onCloud(handler) {
  const wrapped = (e) => handler(e.detail)
  window.addEventListener('atom:cloud', wrapped)
  return () => window.removeEventListener('atom:cloud', wrapped)
}
