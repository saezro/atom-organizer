import { useCallback, useEffect, useState } from 'react'
import { api, isServerMode, onAnalisis, onCloud, onProgress, registerPicker, whenBridgeReady } from './bridge'
import ProgressModal from './ProgressModal'
import PreflightModal from './PreflightModal'
import UpdateModal from './UpdateModal'
import FolderPicker from './FolderPicker'
import SplashInicio from './SplashInicio'
import NavIcon from './NavIcon'
import KioskScreen from './KioskScreen'
import KioskGuard from './KioskGuard.jsx'
import AvisoSesion from './AvisoSesion.jsx'
import FileField from './FileField'
import cloudUploadConfirmando from './trabajo/cloudUploadConfirmando'
import TrabajoScreen from './trabajo/TrabajoScreen'
import HerramientasScreen from './HerramientasScreen'
import './App.css'

// De cinco pestañas a tres: «Organizar»/«SUBIR AL BUCKET» se funden en
// «Trabajo» (TrabajoScreen elige el destino) y «AEROTOOLS»/«OTROS EQUIPOS» en
// «Herramientas» (HerramientasScreen las apila). El icono de ajustes es un
// SVG inline (NavIcon ya trae el trazo 'config'), nunca un carácter/emoji.
export const NAV = [
  { id: 'trabajo', label: 'Trabajo', corto: 'Trabajo' },
  { id: 'herramientas', label: 'Herramientas', corto: 'Herramientas' },
  { id: 'config', label: 'Ajustes', corto: 'Ajustes' },
]

function basename(path) {
  return (path || '').split(/[\\/]/).pop()
}

// El backend (`GenStructFolderConfig.estad` / `atom_core.organize.run_task`)
// sigue esperando el estadillo como UN string: para colar varios por el mismo
// hueco, `atom_core.estadillo.empaquetar_rutas` los junta con el carácter de
// control Unit Separator (\x1f, no puede aparecer en un path real). Aquí se
// replica esa misma codificación en JS. Con 0 o 1 rutas el resultado es
// idéntico a lo que se mandaba antes (string vacío o el path suelto).
const ESTADILLO_PATH_SEP = '\x1f'
function empaquetarRutas(paths) {
  return (paths || []).map((p) => String(p).trim()).filter(Boolean).join(ESTADILLO_PATH_SEP)
}

// Combina la info de N estadillos (cada uno leído por separado con
// `api.readEstadilloInfo`, que solo sabe leer UN fichero) en un único resumen
// para el modal previo. Con un solo fichero se devuelve tal cual, sin tocar
// nada: el modal se ve exactamente igual que antes. Con varios, se etiqueta
// cada vuelo con su fichero de origen (columna «Estadillo» en la tabla) y no
// se calcula una franja horaria global, porque mezclar el formato de fecha de
// estadillos distintos podría dar un rango incorrecto; cada fila ya trae su
// propia hora.
function mergeEstadilloInfos(paths, infos) {
  if (infos.length === 1) return infos[0]

  const ok = []
  const errores = []
  infos.forEach((info, i) => {
    if (info && info.error) errores.push({ archivo: basename(paths[i]), error: info.error })
    else ok.push({ archivo: basename(paths[i]), info })
  })
  if (ok.length === 0) {
    return { error: errores.map((e) => `${e.archivo}: ${e.error}`).join(' · ') }
  }

  const empresas = []
  const trabajos = []
  const pilotos = []
  const drones = []
  const fechas = []
  const vuelos = []
  let num_vuelos = 0
  for (const { archivo, info } of ok) {
    if (info.empresa && !empresas.includes(info.empresa)) empresas.push(info.empresa)
    if (info.trabajo && !trabajos.includes(info.trabajo)) trabajos.push(info.trabajo)
    for (const p of info.pilotos || []) if (!pilotos.includes(p)) pilotos.push(p)
    for (const d of info.drones || []) if (!drones.includes(d)) drones.push(d)
    for (const f of info.fechas || []) if (!fechas.includes(f)) fechas.push(f)
    num_vuelos += info.num_vuelos || 0
    for (const v of info.vuelos || []) vuelos.push({ ...v, fuente: archivo })
  }

  return {
    empresa: empresas.join(' + '),
    trabajo: trabajos.join(' + '),
    fechas: fechas.sort(),
    pilotos,
    drones,
    num_vuelos,
    vuelos,
    hora_inicio: '',
    hora_final: '',
    errores,
  }
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

// Tope de lineas del log crudo. El pipeline emite un "." por imagen: en un
// vuelo de 5.000 fotos el array llegaba a 5.000+ entradas y CADA una se anexaba
// con `[...l, txt]`, que copia el array entero -> O(n^2) en el hilo de React.
// 500 lineas son mas de las que nadie lee hacia atras en un log en vivo, y el
// resumen final (evento `done`) no vive aqui, asi que no se pierde nada util.
const MAX_LOG = 500

function anexarLog(lineas, texto) {
  const siguiente = lineas.length >= MAX_LOG
    ? [...lineas.slice(lineas.length - MAX_LOG + 1), texto]
    : [...lineas, texto]
  return siguiente
}

function App() {
  const [ready, setReady] = useState(false)
  const [section, setSection] = useState('trabajo')
  const [running, setRunning] = useState(false)

  // Confirmación de primer frame pintado (render_confirmar). Si Python no
  // recibe esta señal, el siguiente arranque asume pantalla negra y degrada
  // a rasterizado software. Doble rAF anidado: garantiza que el frame ya se
  // compuso, no solo que React ya hizo commit. Nunca debe romper la UI.
  // Los 3 s de espera son a propósito: el rAF sólo prueba que el renderer
  // pintó, no que Qt haya presentado esa superficie en la ventana; si el
  // usuario ve negro cierra en el acto, y al no llegar la confirmación el
  // arranque siguiente cae a software, que es el lado seguro del fallo.
  useEffect(() => {
    let timer = null
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        timer = setTimeout(() => api.renderConfirmar().catch(() => {}), 3000)
      })
    })
    return () => { if (timer) clearTimeout(timer) }
  }, [])

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

  // El explorador in-app sustituye al diálogo nativo en modo servidor: se
  // registra una vez y `api.pickFolder`/`api.pickFile` lo invocan solos.
  const [picker, setPicker] = useState(null)
  useEffect(() => {
    registerPicker((mode) => new Promise((resolve) => setPicker({ mode, resolve })))
    return () => registerPicker(null)
  }, [])

  // Pantalla por defecto en la Raspberry Pi (modo `--server`): la UI completa
  // sigue montada debajo, pero el kiosco es un callejón sin salida táctil (el
  // avatar ya no abre nada); solo `setKiosco(true)` desde "Volver al kiosco"
  // vuelve a levantar este flag. La detección reutiliza el helper de
  // bridge.js (no hay heurística propia aquí).
  const [kiosco, setKiosco] = useState(() => isServerMode())
  // Splash de arranque: solo en el kiosco de la Pi. Se desmonta solo (el
  // propio componente avisa por `onFin`), no bloquea nada de debajo.
  const [splash, setSplash] = useState(() => isServerMode())
  // Estado propio del kiosco, separado a propósito del de `BucketScreen`
  // (`carpeta`) y `OrganizarScreen` (`destino`/`origen`): son pantallas
  // independientes y no deben compartir selección.
  const [kioskCarpeta, setKioskCarpeta] = useState('')
  // Igual que `estadRutas` en `BucketScreen`: lista ORDENADA de rutas, no un
  // string suelto (así lo consume `EstadilloField`, que sustituye al input
  // de texto plano que había aquí antes).
  const [kioskEstadillo, setKioskEstadillo] = useState([])
  const [kioskInspeccion, setKioskInspeccion] = useState(null)
  // Estado de sesión cloud + catálogo de inspecciones para el kiosco. Se
  // replica aquí lo que ya hace `BucketScreen` (misma llamada, mismo shape)
  // porque esa pantalla no está montada cuando el kiosco es la vista activa.
  const [kioskCloudStatus, setKioskCloudStatus] = useState(null)
  const [kioskInspecciones, setKioskInspecciones] = useState([])
  // Cartel a pantalla completa (Task 6) ante `sin-credencial`/`sin-conexion`.
  // Descartable: el operario puede seguir organizando/subiendo sin sesión.
  // Se reabre solo si el estado empeora (ver `onCloud` de más abajo).
  const [avisoCerrado, setAvisoCerrado] = useState(false)
  // Contador que dispara la pantalla "cuenta" (PairScreen) dentro de
  // `KioskScreen` desde fuera de ese componente: no se puede llamar a su
  // `setAccion` directamente, así que cada incremento reabre el paso de
  // emparejamiento sin duplicar la lógica de pairing que ya vive allí.
  const [kioskAbrirCuenta, setKioskAbrirCuenta] = useState(0)
  // Único job de subida «en crudo» propio del kiosco (no hay equivalente
  // reutilizable a nivel de App: la subida normal vive dentro de
  // `BucketScreen`, que no está montado en modo kiosco).
  const [kioskSubiendo, setKioskSubiendo] = useState(false)
  // Resultado de la última subida del kiosco (done/error/no-arrancada). Se
  // pinta como pantalla propia y solo lo borra el operador.
  const [kioskResultado, setKioskResultado] = useState(null)
  const [kioskCloudPct, setKioskCloudPct] = useState(null)
  // Últimas estadísticas de la subida tal cual las manda el backend por
  // `atom:cloud` (`kind: 'stats'`): files_done/total, mbps, eta... La pantalla
  // de subida del kiosco las pinta; el porcentaje suelto no dice si van 3 o
  // 3.000 fotos ni cuánto queda.
  const [kioskCloudStats, setKioskCloudStats] = useState(null)

  // Misma llamada que `BucketScreen.cargarInspecciones`: se extrae para
  // poder invocarla también desde el botón «Actualizar lista» de
  // `InspeccionSelector` dentro del kiosco.
  const kioskCargarInspecciones = useCallback(() => {
    return api
      .cloudInspecciones()
      .then((r) => setKioskInspecciones(r?.inspecciones || []))
      .catch(() => setKioskInspecciones([]))
  }, [])

  useEffect(() => {
    if (!ready || !kiosco) return
    api.cloudStatus().then(setKioskCloudStatus).catch(() => setKioskCloudStatus(null))
    kioskCargarInspecciones()
  }, [ready, kiosco, kioskCargarInspecciones])

  // Progreso de la subida «en crudo»: no hay suscripción a `atom:cloud` a
  // nivel de App (solo la tiene `BucketScreen`, montada aparte), así que se
  // añade aquí una mínima y local al kiosco, ignorando los eventos con
  // `scope: 'estadillo'` (el kiosco no sube estadillos).
  useEffect(
    () =>
      onCloud((d) => {
        if (d.scope === 'estadillo') return
        switch (d.kind) {
          case 'start':
            setKioskSubiendo(true)
            setKioskCloudPct(0)
            setKioskCloudStats(null)
            break
          case 'stats':
            setKioskCloudPct(d.bytes_total ? Math.round((d.bytes_done / d.bytes_total) * 100) : 0)
            setKioskCloudStats(d)
            break
          case 'done':
            // Feedback SIEMPRE: la subida no puede desaparecer sin decir qué
            // hizo. `kioskResultado` mantiene la pantalla de resumen hasta que
            // el operador la cierra (en la Pi no hay logs a mano).
            setKioskSubiendo(false)
            setKioskCloudPct(null)
            setKioskCloudStats(null)
            setKioskResultado({
              ok: d.ok !== false && !d.cancelled,
              cancelada: Boolean(d.cancelled),
              subidos: d.uploaded ?? 0,
              omitidos: (d.skipped ?? 0) + (d.skipped_remoto ?? 0),
              bytes: d.bytes ?? 0,
              elapsed: d.elapsed ?? null,
              fallidos: d.failed_total ?? 0,
              // Garantia de completitud (backend): objetos comprobados contra
              // el bucket antes de escribir el manifest, y cuantas rondas de
              // subida hicieron falta. Sin mapearlas aqui no llegan al kiosco.
              verificado: d.verificado ?? null,
              verificados: d.verificados ?? null,
              items_total: d.items_total ?? null,
              rondas: d.rondas ?? null,
            })
            break
          case 'error':
            setKioskSubiendo(false)
            setKioskCloudPct(null)
            setKioskCloudStats(null)
            setKioskResultado({ ok: false, error: d.text || 'Error en la subida' })
            break
          // `login` lo emite el emparejamiento por QR; sin este caso el
          // kiosco se quedaba bloqueado despues de emparejar, porque
          // `kioskCloudStatus` seguia con el `sin-credencial` anterior.
          case 'login':
          case 'session':
            // Cambio de sesión (login/logout/expiración): refresca el status
            // del kiosco para recoger `estado`/`pendientes` y reabre el
            // aviso si el estado ha empeorado (no lo abre si ya estaba ok).
            api
              .cloudStatus()
              .then((s) => {
                setKioskCloudStatus(s)
                if (s?.estado && s.estado !== 'ok') setAvisoCerrado(false)
              })
              .catch(() => {})
            break
          default:
            break
        }
      }),
    []
  )

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
          if (d.text) setDetail((l) => anexarLog(l, d.text))
          break
        case 'summary':
          if (d.text && d.text.trim() && !/^_+$/.test(d.text)) {
            setDetail((l) => anexarLog(l, d.text))
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
    const estadillos = Array.isArray(params.estadillo) ? params.estadillo.filter(Boolean) : []
    if (task === 'split_images' && estadillos.length) {
      setPreflight({ loading: true, info: null, task, params, advanced })
      try {
        const infos = await Promise.all(estadillos.map((p) => api.readEstadilloInfo(p)))
        const info = mergeEstadilloInfos(estadillos, infos)
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
    // `estadillo` viaja como array por toda la UI (así es como se arma el
    // modal previo), pero el bridge/backend sigue esperando un único string
    // por ese campo: se empaqueta justo antes de mandarlo.
    const sendParams = Array.isArray(params.estadillo)
      ? { ...params, estadillo: empaquetarRutas(params.estadillo) }
      : params
    ;(async () => {
      try {
        const res = await api.runTask(task, sendParams, advanced)
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

  // Replica `OrganizarScreen.handleRun`: mismo `task`/params que «Organizar
  // completo» (`rename` por defecto, sin panel avanzado → `advanced: null`),
  // así que pasa por el mismo `run()` de arriba y abre el mismo modal previo
  // (si hay estadillo) y el mismo `ProgressModal`.
  function kioskOrganizar({ origen, destino, estadillo }) {
    // `estadillo` ya llega como array (viene de `EstadilloField`); solo se
    // filtran huecos, sin volver a empaquetar un string suelto.
    const estadillos = Array.isArray(estadillo) ? estadillo.filter(Boolean) : []
    run('split_images', { origen, destino, estadillo: estadillos, rename: true }, null)
  }

  async function kioskPickCarpeta() {
    const path = await api.pickFolder()
    if (path) setKioskCarpeta(path)
  }

  // En cuanto el kiosco tiene carpeta E inspección elegidas, se lanza el
  // listado del bucket EN BACKGROUND (fire-and-forget): así, cuando el
  // operario llegue a «Antes de subir» (`kioskComprobarSubida`), el
  // inventario ya está calculado o casi, en vez de arrancar de cero en ese
  // momento. El resultado se ignora aquí a propósito: quien pinta el resumen
  // es `kioskComprobarSubida`, que vuelve a preguntar.
  const kioskPrefijo = kioskInspeccion?.prefijo
  useEffect(() => {
    if (!kioskCarpeta || !kioskPrefijo) return
    api.cloudPrepare(kioskCarpeta, kioskPrefijo).catch(() => {})
  }, [kioskCarpeta, kioskPrefijo])

  // Espera al evento `atom:cloud` (`kind:'inventario'`) del prefijo dado.
  // Con timeout: un bucket enorme o un fallo de red no puede dejar al
  // operario colgado en «Antes de subir» sin poder continuar.
  function esperarInventario(prefijo) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        off()
        resolve(false)
      }, 30000)
      const off = onCloud((d) => {
        if (d.kind !== 'inventario' || d.prefix !== prefijo) return
        clearTimeout(timer)
        off()
        resolve(Boolean(d.ok))
      })
    })
  }

  // Espera al evento `atom:analisis` (scope 'estadillos') lanzado por
  // `estadillosDetectarStart`: el escaneo (`os.walk` + parseo pandas de cada
  // candidato) de una carpeta de vuelo grande congelaba la ventana igual que
  // `detect_suffixes`, así que va en hilo (Task async estadillos_detectar).
  // Con timeout: un fallo o carpeta enorme no puede dejar al operario colgado
  // en «Antes de subir» sin poder continuar.
  function esperarEstadillos() {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        off()
        resolve({ n_estadillos: 0, info: null, error: 'timeout' })
      }, 30000)
      const off = onAnalisis((d) => {
        if (d.scope !== 'estadillos') return
        if (d.kind === 'done') { clearTimeout(timer); off(); resolve(d.data) }
        if (d.kind === 'error') { clearTimeout(timer); off(); resolve({ n_estadillos: 0, info: null, error: d.text }) }
        if (d.kind === 'cancelled') { clearTimeout(timer); off(); resolve({ n_estadillos: 0, info: null, error: null }) }
      })
    })
  }

  // Comprobación EN SECO previa a subir: `cloudPrepare` dice cuántos ficheros
  // hay pendientes de verdad y `estadillosDetectarStart` qué se ha volado
  // (días, vuelos, pilotos, drones), en hilo aparte para no congelar la
  // ventana. No sube nada; el kiosco pinta el resultado como resumen y solo
  // entonces `kioskSubirCrudo` arranca la subida real.
  async function kioskComprobarSubida({ carpeta, inspeccion }) {
    const prefijo = inspeccion?.prefijo
    if (!carpeta || !prefijo) return null
    const estadillosPromise = esperarEstadillos()
    const [prepareInicial, arranqueEstadillos] = await Promise.all([
      api.cloudPrepare(carpeta, prefijo).catch((e) => ({ ok: false, error: String(e) })),
      // Que falle la detección de estadillos NO impide subir: es informativa.
      api.analisisReset().then(() => api.estadillosDetectarStart(carpeta))
        .catch((e) => ({ started: false, reason: String(e) })),
    ])
    let prepare = prepareInicial
    // Si no llegó a arrancar (p.ej. ya había otro análisis en curso), no hay
    // evento que esperar: se resuelve al instante en vez de agotar el timeout.
    const estadillos = arranqueEstadillos?.started === false
      ? { n_estadillos: 0, info: null, error: arranqueEstadillos.reason || 'No se pudo iniciar la detección de estadillos.' }
      : await estadillosPromise
    // Si el listado (precalentado arriba, o recién lanzado por este mismo
    // `cloudPrepare` si no había caché) aún no ha terminado, se espera al
    // evento y se repregunta UNA vez para traer los pendientes reales. Si
    // llega `ok:false` o hay timeout, se sigue con el `prepare` a secas: la
    // subida no puede quedar bloqueada por no saber el número exacto.
    if (prepare?.inventario === 'calculando') {
      const ok = await esperarInventario(prefijo)
      if (ok) prepare = await api.cloudPrepare(carpeta, prefijo).catch(() => prepare)
    }
    return { prepare, estadillos }
  }

  // Replica el tramo relevante de `BucketScreen.subir`, sin el `cloudPrepare`
  // (ya lo hizo `kioskComprobarSubida` para pintar el resumen) y sin subida de
  // estadillo (fuera del alcance de esta pantalla).
  async function kioskSubirCrudo({ carpeta, inspeccion }) {
    const prefijo = inspeccion?.prefijo
    if (!carpeta || !prefijo) return
    setKioskResultado(null)
    setKioskSubiendo(true)
    try {
      const r = await cloudUploadConfirmando(carpeta, prefijo, inspeccion?.id)
      // `started:false` no emite ningún evento `atom:cloud`: sin esto la
      // pantalla de subida se cerraría sola y en la Pi no quedaría rastro.
      if (r && r.started === false) {
        setKioskSubiendo(false)
        setKioskResultado({ ok: false, error: r.reason || 'No se pudo iniciar la subida.' })
      }
    } catch (e) {
      setKioskSubiendo(false)
      setKioskResultado({ ok: false, error: String(e?.message || e) })
    }
  }

  const kioskBusy = running || kioskSubiendo
  const kioskFaseActiva = phases.find((p) => p.status === 'active')?.name || plant
  const kioskProgreso = running
    ? { fase: kioskFaseActiva || 'Procesando…', pct: progress }
    : kioskSubiendo
      ? {
          fase: kioskCloudStats ? 'Subiendo' : 'Empezando subida…',
          pct: kioskCloudPct ?? 0,
          subida: true,
          stats: kioskCloudStats,
        }
      : null

  return (
    // `app-kiosco` marca el modo kiosco en la RAIZ, no en cada pantalla: asi la
    // regla anti-seleccion de texto cubre tambien lo que se monta fuera de
    // `.kiosk` (AvisoSesion, SplashInicio) y lo que se anada en el futuro.
    <div className={kiosco ? 'app app-kiosco' : 'app'}>
      {splash && <SplashInicio onFin={() => setSplash(false)} />}
      {kiosco && !avisoCerrado && (
        <AvisoSesion
          estado={kioskCloudStatus?.estado}
          pendientes={kioskCloudStatus?.pendientes || 0}
          onEmparejar={() => {
            setAvisoCerrado(true)
            setKioskAbrirCuenta((n) => n + 1)
          }}
          onCerrar={() => setAvisoCerrado(true)}
        />
      )}
      {!kiosco && (
        <header className="brand">
          <h1>
            <span className="atom">ATOM</span> <span className="org">ORGANIZER</span>
          </h1>
          {version && <span className="ver">v{version}</span>}
          {/* El kiosco es un callejón sin salida táctil (el avatar ya no
              abre nada): sin este botón, en la Pi no habría forma de volver
              a él desde la UI completa, porque Chromium arranca en modo
              kiosco y no tiene barra de URL para recargar. Solo en modo
              servidor: en escritorio no existe el kiosco. */}
          {isServerMode() && (
            <button type="button" className="btn-ghost volver-kiosco" onClick={() => setKiosco(true)}>
              <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                <polyline points="9 22 9 12 15 12 15 22" />
              </svg>
              Volver al kiosco
            </button>
          )}
        </header>
      )}

      {!kiosco && (
        <nav className="seg" role="tablist">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={'seg-btn' + (section === n.id ? ' active' : '')}
              onClick={() => setSection(n.id)}
              title={n.label}
              aria-label={n.label}
              role="tab"
              aria-selected={section === n.id}
            >
              <NavIcon id={n.id} />
              <span className="seg-txt">{n.corto}</span>
            </button>
          ))}
        </nav>
      )}

      <main>
        {kiosco ? (
          <KioskGuard status={kioskCloudStatus} ocupado={Boolean(kioskBusy || kioskSubiendo)}>
            <KioskScreen
              status={kioskCloudStatus}
              carpeta={kioskCarpeta}
              onPickCarpeta={kioskPickCarpeta}
              inspecciones={kioskInspecciones}
              inspeccion={kioskInspeccion}
              onSelectInspeccion={setKioskInspeccion}
              onActualizarInspecciones={kioskCargarInspecciones}
              estadillo={kioskEstadillo}
              onEstadillo={setKioskEstadillo}
              onOrganizar={kioskOrganizar}
              onSubirCrudo={kioskSubirCrudo}
              onComprobarSubida={kioskComprobarSubida}
              onRefreshStatus={() =>
                api.cloudStatus().then(setKioskCloudStatus).catch(() => setKioskCloudStatus(null))
              }
              credencialOk={kioskCloudStatus?.estado === 'ok'}
              abrirCuenta={kioskAbrirCuenta}
              busy={kioskBusy}
              progreso={kioskProgreso}
              resultado={kioskResultado}
              onCerrarResultado={() => setKioskResultado(null)}
              onRunTask={(task, params) => run(task, params, null)}
            />
          </KioskGuard>
        ) : section === 'trabajo' ? (
          <TrabajoScreen
            ready={ready}
            running={running}
            onRun={run}
            onCloudStatusChange={setKioskCloudStatus}
          />
        ) : section === 'herramientas' ? (
          <HerramientasScreen running={running} onRun={run} />
        ) : (
          <ConfigScreen ready={ready} />
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

      {picker && (
        <FolderPicker
          mode={picker.mode}
          startPath={null}
          onPick={(p) => { picker.resolve(p); setPicker(null) }}
          onCancel={() => { picker.resolve(null); setPicker(null) }}
        />
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

  // Aceleración gráfica (render_estado/render_set_modo). No va por
  // readConfig/writeConfig: es un ajuste propio del shell (WebView2/Qt), no
  // de la config del organizador.
  const [renderModo, setRenderModo] = useState('auto')
  const [renderActiva, setRenderActiva] = useState(null) // null hasta cargar
  const [renderSaved, setRenderSaved] = useState(null) // null | {ok, msg}

  useEffect(() => {
    if (!ready) return
    api
      .renderEstado()
      .then((r) => {
        if (r?.modo) setRenderModo(r.modo)
        setRenderActiva(r?.activa ?? null)
      })
      .catch(() => {})
  }, [ready])

  async function cambiarRenderModo(modo) {
    setRenderModo(modo)
    setRenderSaved(null)
    try {
      const res = await api.renderSetModo(modo)
      setRenderSaved(
        res?.ok
          ? { ok: true, msg: 'Se aplicará al reiniciar la app.' }
          : { ok: false, msg: res?.error || 'No se pudo cambiar.' }
      )
    } catch (e) {
      setRenderSaved({ ok: false, msg: String(e) })
    }
  }

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

      <div className="field">
        <span className="field-label">Aceleración gráfica</span>
        <select
          className="glass-input"
          value={renderModo}
          disabled={!ready}
          onChange={(e) => cambiarRenderModo(e.target.value)}
        >
          <option value="auto">Automática (recomendado)</option>
          <option value="gpu">Siempre activada</option>
          <option value="software">Desactivada (si la ventana sale en negro)</option>
        </select>
        <span className="field-hint">
          {renderActiva === null
            ? 'Cargando…'
            : renderActiva
            ? 'Ahora mismo: acelerada'
            : 'Ahora mismo: por software'}
        </span>
        {renderSaved && (
          <span className={`field-hint ${renderSaved.ok ? 'hint-ok' : 'hint-warn'}`}>
            {renderSaved.msg}
          </span>
        )}
      </div>
    </div>
  )
}

export default App
