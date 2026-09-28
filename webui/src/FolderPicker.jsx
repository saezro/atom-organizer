import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, isServerMode } from './bridge.js'
import BotonToque, { pxDeRem, UMBRAL_REM } from './pulsacion.jsx'
import Paginador from './Paginador.jsx'

// Sustituye al dialogo nativo de ficheros, que solo existia via Qt/pywebview.
// Aparte de no estar disponible en modo servidor (Raspberry Pi), un dialogo
// nativo en una pantalla de 480x320 manejada con el dedo seria inutilizable
// de todas formas: aqui las filas son objetivos de toque grandes y la
// navegacion es un nivel de carpeta cada vez.
//
// Contrato real de `api.listDir` (Task 7, `Api.list_dir` en app_webview.py):
//   exito -> {ok:true, path, parent: string|null, dirs:[{name,path}],
//             files:[{name,path,size}]}
//   error -> {ok:false, error}
// `parent` es null en la raiz. Sin argumento lista el home del usuario.
//
// En el kiosco Linux (`_listado_raiz_discos_pi`/`_list_dir_confinado_pi`,
// app_webview.py:679-762) el selector va CONFINADO a los discos externos
// montados y el shape trae ademas `is_root`, `disk_name`, `rel_parts`. En la
// raiz (`is_root:true`) `dirs` son los discos, con `libre_gb`/`total_gb` en
// vez de subcarpetas. En Windows/escritorio esos campos no existen: el
// comportamiento de siempre (ruta plana, ".. subir") queda intacto.

// Los iconos van en SVG y no en emoji a proposito: la Pi no tiene fuente de
// emoji instalada (y meterla exige sudo, que no tenemos), asi que los emoji
// salian como el cuadrado del glifo ausente.
function Ico({ tipo }) {
  const comun = {
    className: 'picker-ico', width: '1em', height: '1em', viewBox: '0 0 16 16',
    fill: 'none', stroke: 'currentColor', strokeWidth: '1.4',
    strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
  }
  if (tipo === 'subir') {
    return <svg {...comun}><path d="M8 13V3M8 3 4 7M8 3l4 4" /></svg>
  }
  if (tipo === 'fichero') {
    return <svg {...comun}><path d="M9.5 2H4.5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V5zM9.5 2v3h3" /></svg>
  }
  if (tipo === 'disco') {
    return (
      <svg {...comun}>
        <rect x="1.7" y="4.5" width="12.6" height="7" rx="1.2" />
        <path d="M1.7 8h12.6" />
        <circle cx="11.4" cy="10.2" r="0.6" fill="currentColor" stroke="none" />
      </svg>
    )
  }
  return <svg {...comun}><path d="M2 12.5v-9a1 1 0 0 1 1-1h3l1.5 2H13a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z" /></svg>
}

// Texto de espacio libre de un disco ("123.4 GB libres de 500.0 GB"). Null si
// el backend no pudo leer el `statvfs` del punto de montaje.
function textoEspacio(libreGb, totalGb) {
  if (libreGb == null || totalGb == null) return null
  return `${libreGb.toFixed(1)} GB libres de ${totalGb.toFixed(1)} GB`
}

// Breadcrumb relativo al disco: "Discos › DISCO › carpeta › sub". `datos`
// trae `path` absoluto, `disk_name` y `rel_parts` (nombres de carpeta desde
// la raiz del disco, sin la ruta del punto de montaje). Reconstruye la ruta
// absoluta de cada segmento pelando `rel_parts` por el final de `path`, sin
// pedirle esa ruta al backend.
function migasDeDisco(datos) {
  const partes = datos.path.split('/')
  const raizPartes = datos.rel_parts.length
    ? partes.slice(0, partes.length - datos.rel_parts.length)
    : partes
  const raizDisco = raizPartes.join('/') || '/'
  const migas = [
    { label: 'Discos', path: null },
    { label: datos.disk_name, path: raizDisco },
  ]
  datos.rel_parts.forEach((seg, i) => {
    migas.push({
      label: seg,
      path: raizPartes.concat(datos.rel_parts.slice(0, i + 1)).join('/'),
    })
  })
  return migas
}

// Giro en CSS (.picker-spin), no en JS: mas barato en la Pi. Solo aparece en
// la fila que se acaba de tocar, mientras `cargar()` sigue en vuelo.
function IconoCargando() {
  return (
    <svg className="picker-spin" width="1em" height="1em" viewBox="0 0 16 16" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <path d="M14 8A6 6 0 1 1 8 2" />
    </svg>
  )
}

// Espera apoyada en `setTimeout`, para el modo `reproduccion`: espacia cada
// paso ~450ms de forma que se lea como un recorrido, no un salto.
function esperar(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Tiempos del recorrido en modo reproduccion (control remoto del kiosco):
// suficientes para leerse el movimiento en un panel de 480x320 sin hacerse
// eterno con rutas de varios niveles.
// Pausa tras abrir el selector (listado de la raiz ya cargado) antes de
// resaltar el primer segmento: da tiempo a leer que el selector se acaba de
// abrir antes de que arranque el recorrido.
const PAUSA_INICIAL_MS = 1200
// Por cada segmento: cuanto se resalta la fila ANTES de entrar en ella...
const RESALTAR_SEGMENTO_MS = 700
// ...y cuanto se espera YA DENTRO de la carpeta (con el nuevo listado en
// pantalla) antes de pasar al siguiente segmento. Total por segmento
// ~RESALTAR_SEGMENTO_MS + ESPERA_TRAS_ENTRAR_MS.
const ESPERA_TRAS_ENTRAR_MS = 1100
// Cuanto se resalta el boton "Usar esta carpeta" antes de cerrar el selector.
const RESALTAR_CONFIRMAR_MS = 1500

export default function FolderPicker({
  mode = 'folder', startPath = null, onPick, onCancel,
  // Modo "reproduccion" (control remoto del kiosco, Task «se ve como se
  // mueve a los sitios»): en vez de esperar toques, recorre SOLA los
  // segmentos de `rutaObjetivo` desde la raiz, resaltando con
  // `.kiosk-control-pulso` la fila que "pulsa" en cada paso. El backend ya
  // fijo la carpeta (`carpeta_trabajo_fijar`, ver KioskScreen.jsx): este
  // picker NUNCA llama a `onPick`, solo la enseña; los toques reales se
  // ignoran (`.picker-reproduccion`, App.css). Si `listDir` falla en algun
  // paso se sigue igual (las migas/segmentos ya dicen a donde va).
  reproduccion = false, rutaObjetivo = null, onFinReproduccion,
}) {
  const [estado, setEstado] = useState({ cargando: true, datos: null, error: null })
  // Reproduccion: fila que "esta pulsando" el recorrido ahora mismo (path
  // absoluto del segmento) y si toca resaltar el boton de confirmar (ultimo
  // paso, antes de cerrarse). `null`/`false` fuera de modo reproduccion.
  const [reproResaltada, setReproResaltada] = useState(null)
  const [reproConfirmar, setReproConfirmar] = useState(false)
  const listaRef = useRef(null)
  const arrastre = useRef({ activo: false, y0: 0, top0: 0, umbral: pxDeRem(UMBRAL_REM), movido: false })
  const tactil = isServerMode()
  // En el kiosco el panel resistivo no tiene hover ni el destello de
  // BotonToque dice nada de "se esta cargando" (dura solo --onda). Esta ruta
  // pendiente marca la FILA pulsada al instante, en el mismo frame del toque,
  // sin esperar la respuesta HTTP de listDir. Solo tactil: en escritorio el
  // "Cargando..." de siempre (pm-status) sigue siendo la unica senal.
  const [rutaPendiente, setRutaPendiente] = useState(null)

  const cargar = useCallback(async (ruta) => {
    setEstado((s) => ({ ...s, cargando: true, error: null }))
    try {
      const datos = await api.listDir(ruta)
      if (!datos.ok) {
        setEstado({ cargando: false, datos: null, error: datos.error })
        return
      }
      setEstado({ cargando: false, datos, error: null })
    } catch (e) {
      setEstado({ cargando: false, datos: null, error: String(e.message || e) })
    } finally {
      setRutaPendiente(null)
    }
  }, [])

  // En modo servidor (Raspberry Pi), sin ruta inicial explicita, arrancamos
  // en el disco USB de inspecciones si hay uno montado (`api.defaultDir`)
  // en vez del home. `defaultDir` ya devuelve el listado completo (mismo
  // shape que `listDir`): una sola llamada HTTP, no dos encadenadas, que es
  // lo que hacia percibir el selector como colgado en el kiosco. Si falla o
  // no hay disco, cae al home de siempre. En escritorio (pywebview) o con
  // `startPath` explicito el comportamiento no cambia: se sigue cargando
  // tal cual.
  useEffect(() => {
    if (startPath !== null || !isServerMode()) {
      cargar(startPath)
      return
    }
    let cancelado = false
    setEstado((s) => ({ ...s, cargando: true, error: null }))
    api.defaultDir()
      .then((r) => {
        if (cancelado) return
        if (r && r.ok) {
          setEstado({ cargando: false, datos: r, error: null })
        } else {
          cargar(null)
        }
      })
      .catch(() => { if (!cancelado) cargar(null) })
    return () => { cancelado = true }
  }, [cargar, startPath])

  // Recorre SOLA los segmentos de `rutaObjetivo` (solo rutas Unix: el kiosco
  // Linux es el unico que usa este modo, ver contrato del backend arriba):
  // arranca desde la raiz para que se vea el trayecto completo, carga cada
  // nivel, resalta ~450ms el segmento siguiente antes de entrar y, al
  // llegar, resalta el boton "Usar esta carpeta" y avisa con
  // `onFinReproduccion`. Nunca llama a `onPick`: el backend ya fijo la
  // carpeta (`carpeta_trabajo_fijar`), esto solo la enseña.
  useEffect(() => {
    if (!reproduccion) return undefined
    let cancelado = false
    const segmentos = (rutaObjetivo || '').split('/').filter(Boolean)
    const acumuladas = segmentos.map((_, i) => '/' + segmentos.slice(0, i + 1).join('/'))

    async function recorrer() {
      if (cancelado) return
      await cargar(null)
      if (cancelado) return
      await esperar(PAUSA_INICIAL_MS)
      for (const ruta of acumuladas) {
        if (cancelado) return
        setReproResaltada(ruta)
        await esperar(RESALTAR_SEGMENTO_MS)
        if (cancelado) return
        await cargar(ruta)
        if (cancelado) return
        await esperar(ESPERA_TRAS_ENTRAR_MS)
      }
      if (cancelado) return
      setReproResaltada(null)
      setReproConfirmar(true)
      await esperar(RESALTAR_CONFIRMAR_MS)
      if (cancelado) return
      onFinReproduccion?.()
    }
    recorrer()
    return () => { cancelado = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reproduccion, rutaObjetivo])

  // Deslizar sobre la lista tiene que scrollear: el panel resistivo llega como
  // puntero de raton, asi que no hay scroll tactil que aprovechar y se mueve
  // scrollTop a mano. Solo se engancha en modo servidor (ver el <ul>): en
  // escritorio el scroll nativo ya funciona y este arrastre se comeria clicks
  // legitimos si el raton tiembla mas de UMBRAL_REM.
  const alPulsar = (e) => {
    const ul = listaRef.current
    if (!ul) return
    arrastre.current = {
      activo: true, y0: e.clientY, top0: ul.scrollTop,
      umbral: pxDeRem(UMBRAL_REM), movido: false,
    }
  }

  const alMover = (e) => {
    const a = arrastre.current
    const ul = listaRef.current
    if (!a.activo || !ul) return
    const dy = e.clientY - a.y0
    if (!a.movido && Math.abs(dy) < a.umbral) return
    a.movido = true
    ul.scrollTop = a.top0 - dy
  }

  const alSoltar = () => { arrastre.current.activo = false }

  // Red de seguridad para el camino de escritorio, donde las filas si activan
  // por click: si hubo arrastre no es un toque, es un scroll.
  const alHacerClick = (e) => {
    if (!arrastre.current.movido) return
    arrastre.current.movido = false
    e.preventDefault()
    e.stopPropagation()
  }

  // Envuelve `cargar` para dar feedback en el MISMO frame del toque: la fila
  // pulsada queda marcada antes de que llegue la respuesta de listDir (que en
  // la Pi, sobre USB, puede tardar). Solo en tactil; en escritorio `cargar`
  // se llama tal cual, sin marcar nada.
  const irA = (ruta) => {
    if (tactil) setRutaPendiente(ruta)
    cargar(ruta)
  }

  // Paginado a botones ▲/▼, mismo patron que KioskScreen (InspeccionSelector):
  // el arrastre no siempre prende en el panel resistivo. Una "pagina" es el
  // alto visible menos un solape de una fila, igual que alli.
  const [pagina, setPagina] = useState(0)
  const [paginas, setPaginas] = useState(1)
  const saltoLista = (el) => Math.max(el.clientHeight - pxDeRem(3), pxDeRem(6))

  const recalcularPaginas = useCallback(() => {
    const el = listaRef.current
    if (!el) return
    const sobrante = Math.max(el.scrollHeight - el.clientHeight, 0)
    const salto = saltoLista(el)
    const total = sobrante > 1 ? Math.ceil(sobrante / salto) + 1 : 1
    setPaginas(total)
    setPagina(Math.min(Math.round(el.scrollTop / salto), total - 1))
  }, [])

  useLayoutEffect(() => {
    if (!tactil) return
    recalcularPaginas()
  }, [recalcularPaginas, tactil, estado])
  useEffect(() => {
    if (!tactil) return undefined
    const el = listaRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(recalcularPaginas)
    ro.observe(el)
    return () => ro.disconnect()
  }, [recalcularPaginas, tactil])

  // Igual que en KioskScreen: mientras dura el scroll animado se ignoran los
  // `onScroll` intermedios, que si no hacen parpadear el indicador.
  const animando = useRef(null)
  useEffect(() => () => { if (animando.current) clearTimeout(animando.current) }, [])
  const paginar = (signo) => {
    const el = listaRef.current
    if (!el) return
    const salto = saltoLista(el)
    const siguiente = Math.max(0, Math.min(pagina + signo, paginas - 1))
    const destino = Math.max(0, Math.min(siguiente * salto, el.scrollHeight - el.clientHeight))
    setPagina(siguiente)
    if (typeof el.scrollTo === 'function') {
      if (animando.current) clearTimeout(animando.current)
      animando.current = setTimeout(() => { animando.current = null }, 450)
      el.scrollTo({ top: destino, behavior: 'smooth' })
    } else {
      el.scrollTop = destino
    }
  }
  const alScrollLista = () => {
    const el = listaRef.current
    if (!el || animando.current) return
    const siguiente = Math.min(Math.round(el.scrollTop / saltoLista(el)), Math.max(paginas - 1, 0))
    setPagina((prev) => (prev === siguiente ? prev : siguiente))
  }

  const { cargando, datos, error } = estado
  // En tactil, folder tambien enseña los ficheros (atenuados, no elegibles):
  // que la persona vea que hay ademas del PDF que va a escoger. En escritorio
  // no cambia nada: solo se listan en mode="file", como siempre.
  const ficherosVisibles = mode === 'file' || (tactil && mode === 'folder')

  // Shape del kiosco Linux (confinado a discos externos): solo lo trae esta
  // rama del backend, nunca Windows/escritorio (ver comentario de contrato
  // arriba). `hasOwnProperty` en vez de `datos?.is_root` porque en la raiz de
  // disco `is_root` es `true` pero en el resto de niveles es `false`: lo que
  // distingue el shape nuevo del viejo es que el campo EXISTA, no su valor.
  const modoDiscos = !!datos && Object.prototype.hasOwnProperty.call(datos, 'is_root')
  const enRaizDiscos = modoDiscos && datos.is_root

  return (
    <div className={reproduccion ? 'pm-overlay picker-reproduccion' : 'pm-overlay'} role="dialog" aria-modal="true">
      <div className="pm-card picker-card">
        <h2 className="pm-title">
          {mode === 'file' ? 'Elegir fichero' : 'Elegir carpeta'}
        </h2>
        {modoDiscos && !enRaizDiscos ? (
          <div className="picker-migas" title={datos.path}>
            {migasDeDisco(datos).map((miga, i, arr) => (
              <span key={miga.path ?? 'raiz'} className="picker-miga-item">
                {i > 0 && <span className="picker-miga-sep" aria-hidden="true">›</span>}
                {i === arr.length - 1 ? (
                  <span className="picker-miga picker-miga-actual">{miga.label}</span>
                ) : (
                  <BotonToque className="picker-miga" tactil={tactil} onActivar={() => irA(miga.path)}>
                    {miga.label}
                  </BotonToque>
                )}
              </span>
            ))}
          </div>
        ) : (
          <div className="picker-ruta" title={datos?.path || ''}>
            {enRaizDiscos ? 'Discos externos' : (datos?.path || '…')}
          </div>
        )}

        {error && <p className="pm-status err">{error}</p>}

        <div className={tactil ? 'picker-paginado' : undefined}>
          <ul
            className="picker-lista"
            ref={listaRef}
            {...(tactil ? {
              onScroll: alScrollLista,
              onPointerDown: alPulsar,
              onPointerMove: alMover,
              onPointerUp: alSoltar,
              onPointerCancel: alSoltar,
              onPointerLeave: alSoltar,
              onClickCapture: alHacerClick,
            } : {})}
          >
            {cargando && !datos && (
              <li className="picker-cargando">Cargando…</li>
            )}
            {datos?.parent && (
              <li>
                <BotonToque
                  className={rutaPendiente === datos.parent ? 'picker-fila picker-fila-cargando' : 'picker-fila'}
                  tactil={tactil} cancelarAlMover onActivar={() => irA(datos.parent)}
                >
                  <Ico tipo="subir" /> <span className="picker-txt">.. subir</span>
                  {tactil && rutaPendiente === datos.parent && <IconoCargando />}
                </BotonToque>
              </li>
            )}
            {datos?.dirs.map((d) => {
              const espacio = enRaizDiscos ? textoEspacio(d.libre_gb, d.total_gb) : null
              const claseFila = ['picker-fila', 'picker-dir']
              if (rutaPendiente === d.path) claseFila.push('picker-fila-cargando')
              if (reproResaltada === d.path) claseFila.push('kiosk-control-pulso')
              return (
                <li key={d.path}>
                  <BotonToque
                    className={claseFila.join(' ')}
                    tactil={tactil} cancelarAlMover onActivar={() => irA(d.path)}
                  >
                    <Ico tipo={enRaizDiscos ? 'disco' : 'carpeta'} />
                    <span className="picker-txt-col">
                      <span className="picker-txt">{d.name}</span>
                      {espacio && <span className="picker-txt-sub">{espacio}</span>}
                    </span>
                    {tactil && rutaPendiente === d.path && <IconoCargando />}
                  </BotonToque>
                </li>
              )
            })}
            {mode === 'file' && datos?.files.map((f) => (
              <li key={f.path}>
                <BotonToque className="picker-fila picker-file" tactil={tactil} cancelarAlMover onActivar={() => onPick(f.path)}>
                  <Ico tipo="fichero" /> <span className="picker-txt">{f.name}</span>
                </BotonToque>
              </li>
            ))}
            {tactil && mode === 'folder' && datos?.files.map((f) => (
              <li key={f.path} className="picker-fila picker-file picker-file-solo" aria-disabled="true">
                <Ico tipo="fichero" /> <span className="picker-txt">{f.name}</span>
              </li>
            ))}
            {datos && datos.dirs.length === 0 && !(ficherosVisibles && datos.files.length > 0) && (
              <li className="picker-vacio">Carpeta vacía.</li>
            )}
          </ul>

          {tactil && (
            <Paginador
              pagina={pagina}
              totalPaginas={paginas}
              onPagina={(n) => paginar(n - pagina)}
              tactil={tactil}
              testidPrefijo="picker-"
              contexto="la lista"
            />
          )}
        </div>

        {cargando && <p className="pm-status">Cargando…</p>}

        <div className="pm-actions picker-acciones">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancelar
          </button>
          {mode === 'folder' && (
            <button
              type="button"
              className={reproConfirmar ? 'btn-run kiosk-control-pulso' : 'btn-run'}
              disabled={!datos}
              onClick={() => onPick(datos.path)}
            >
              Usar esta carpeta
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
