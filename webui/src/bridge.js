// Puente con el backend Python (pywebview). Todo el acceso a `window.pywebview`
// pasa por aquí para aislar la app de React del shell.

export function whenBridgeReady() {
  return new Promise((resolve) => {
    if (window.pywebview?.api) return resolve()
    window.addEventListener('pywebviewready', () => resolve(), { once: true })
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
}

// Python empuja progreso del pipeline con:
//   window.dispatchEvent(new CustomEvent('atom:progress', {detail: {...}}))
// detail = { kind, text?, value?, data? }
//   kind 'log'|'summary' -> text
//   kind 'progress'      -> value (% de la fase activa)
//   kind 'plant'         -> text (nombre de planta, título del modal)
//   kind 'plan'          -> data (list[str] de fases activas)
//   kind 'phase'         -> data ({index, total, name})
//   kind 'done'|'error'  -> (error trae text)
export function onProgress(handler) {
  const wrapped = (e) => handler(e.detail)
  window.addEventListener('atom:progress', wrapped)
  return () => window.removeEventListener('atom:progress', wrapped)
}
