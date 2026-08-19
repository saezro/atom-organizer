import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, isServerMode } from './bridge.js'
import BotonToque, { pxDeRem, UMBRAL_REM } from './pulsacion.jsx'

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
  return <svg {...comun}><path d="M2 12.5v-9a1 1 0 0 1 1-1h3l1.5 2H13a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z" /></svg>
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

export default function FolderPicker({ mode = 'folder', startPath = null, onPick, onCancel }) {
  const [estado, setEstado] = useState({ cargando: true, datos: null, error: null })
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

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card picker-card">
        <h2 className="pm-title">
          {mode === 'file' ? 'Elegir fichero' : 'Elegir carpeta'}
        </h2>
        <div className="picker-ruta" title={datos?.path || ''}>{datos?.path || '…'}</div>

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
            {datos?.dirs.map((d) => (
              <li key={d.path}>
                <BotonToque
                  className={rutaPendiente === d.path ? 'picker-fila picker-dir picker-fila-cargando' : 'picker-fila picker-dir'}
                  tactil={tactil} cancelarAlMover onActivar={() => irA(d.path)}
                >
                  <Ico tipo="carpeta" /> <span className="picker-txt">{d.name}</span>
                  {tactil && rutaPendiente === d.path && <IconoCargando />}
                </BotonToque>
              </li>
            ))}
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
            <div className="picker-nav kiosk-insp-nav">
              <BotonToque
                className="kiosk-insp-nav-btn"
                tactil={tactil}
                onActivar={() => paginar(-1)}
                disabled={pagina <= 0}
                aria-label="Subir en la lista"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M6 15l6-6 6 6" />
                </svg>
              </BotonToque>
              <span className="kiosk-insp-pagina" aria-live="polite">
                <strong>{Math.min(pagina + 1, paginas)}</strong>
                <span className="kiosk-insp-pagina-sep" />
                {paginas}
              </span>
              <BotonToque
                className="kiosk-insp-nav-btn"
                tactil={tactil}
                onActivar={() => paginar(1)}
                disabled={pagina >= paginas - 1}
                aria-label="Bajar en la lista"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </BotonToque>
            </div>
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
              className="btn-run"
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
