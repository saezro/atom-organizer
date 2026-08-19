// Pantalla "kiosco" para la Raspberry Pi con pantalla táctil de 480x320 px,
// que sustituye a la UI normal cuando el Organizer corre en modo servidor.
//
// El flujo va en DOS pasos y en este orden: primero se elige QUÉ se quiere
// hacer (organizar / subir en crudo) y solo después se piden los datos que esa
// acción necesita. Antes se pedían todos los datos por delante y las dos
// acciones compartían pantalla, lo que obligaba a elegir carpeta sin saber
// para qué y dejaba a la vista campos que no aplicaban (el selector de
// inspección no pinta nada al organizar).
//
// El panel de la Pi es resistivo (ADS7846): menos preciso que uno capacitivo,
// así que el paso 1 son dos botones que ocupan media pantalla cada uno y en el
// paso 2 nada táctil baja de `--toque-min`.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, isServerMode } from './bridge.js'
import BotonToque, { pxDeRem, UMBRAL_REM } from './pulsacion.jsx'
import PairScreen from './PairScreen.jsx'
import InspeccionSelector, { COLOR_FASE, COLOR_FASE_DEFECTO, ORDEN_FASES, chip } from './InspeccionSelector.jsx'
import EstadilloField from './EstadilloField.jsx'
import { formatBytes, formatDuracion } from './formato.js'

// Deriva la ruta de destino a partir de la carpeta de origen, añadiendo el
// sufijo "_ORGANIZADO". Función pura: sin efectos, sin acceso a props/estado.
export function derivarDestino(carpeta) {
  if (!carpeta) return ''
  const sinBarraFinal = carpeta.replace(/[/\\]+$/, '')
  if (!sinBarraFinal) return ''
  return `${sinBarraFinal}_ORGANIZADO`
}

export default function KioskScreen({
  status,
  carpeta,
  onPickCarpeta,
  inspecciones,
  inspeccion,
  onSelectInspeccion,
  onActualizarInspecciones,
  estadillo,
  onEstadillo,
  onOrganizar,
  onSubirCrudo,
  onComprobarSubida,
  onRefreshStatus,
  busy,
  progreso,
  // Solo para pruebas: permite montar el componente directamente en un paso.
  accionInicial = null,
}) {
  const [accion, setAccion] = useState(accionInicial)
  // El catálogo trae cientos de inspecciones de todo el ciclo de vida, pero si
  // estás subiendo material la inspección está en vuelo: arrancar filtrado a esa
  // fase deja una lista corta. El resto sigue a un toque, en la pantalla de fases.
  const [fasesKiosco, setFasesKiosco] = useState(['Vuelo'])
  const [eligiendoFases, setEligiendoFases] = useState(false)
  // Comprobación en seco previa a subir (ver `confirmar`): `null` mientras no
  // se ha pedido, `{prepare, estadillos}` cuando ya hay resumen que pintar.
  const [resumen, setResumen] = useState(null)
  const [comprobando, setComprobando] = useState(false)
  const destino = derivarDestino(carpeta)
  const email = status?.email || ''
  const inicial = email ? email[0].toUpperCase() : ''
  const tactil = isServerMode()

  // La lista de `InspeccionSelector` puede crecer por encima del hueco
  // disponible en el paso 2: mismo problema que la lista de `FolderPicker`
  // (panel resistivo = ratón absoluto, arrastrar el dedo no scrollea solo),
  // mismo arreglo (`FolderPicker.jsx:83-116`), aplicado al `div` que la
  // envuelve porque el componente en sí no expone su `<ul>` interno.
  const inspRef = useRef(null)
  const arrastreInsp = useRef({ activo: false, y0: 0, top0: 0, umbral: pxDeRem(UMBRAL_REM), movido: false })
  const alPulsarInsp = (e) => {
    const el = inspRef.current
    if (!el) return
    arrastreInsp.current = {
      activo: true, y0: e.clientY, top0: el.scrollTop,
      umbral: pxDeRem(UMBRAL_REM), movido: false,
    }
  }
  const alMoverInsp = (e) => {
    const a = arrastreInsp.current
    const el = inspRef.current
    if (!a.activo || !el) return
    const dy = e.clientY - a.y0
    if (!a.movido && Math.abs(dy) < a.umbral) return
    a.movido = true
    el.scrollTop = a.top0 - dy
  }
  const alSoltarInsp = () => { arrastreInsp.current.activo = false }

  // El arrastre no siempre prende en el panel resistivo (Cas: «el scroll no
  // está funcionando»), así que la lista se pagina también con dos botones,
  // con indicador de página y desplazamiento animado para que se vea HACIA
  // DÓNDE se ha movido (un salto seco desorienta: parece otra lista).
  //
  // Una "página" = el alto visible menos un solape de una fila, para no
  // perder de vista dónde estabas.
  const [pagina, setPagina] = useState(0)
  const [paginas, setPaginas] = useState(1)
  const saltoInsp = (el) => Math.max(el.clientHeight - pxDeRem(3), pxDeRem(6))

  const recalcularPaginas = useCallback(() => {
    const el = inspRef.current
    if (!el) return
    const sobrante = Math.max(el.scrollHeight - el.clientHeight, 0)
    const salto = saltoInsp(el)
    const total = sobrante > 1 ? Math.ceil(sobrante / salto) + 1 : 1
    setPaginas(total)
    setPagina(Math.min(Math.round(el.scrollTop / salto), total - 1))
  }, [])

  // Al montar la pantalla y cada vez que cambia el catálogo: el número de
  // páginas depende del alto real ya pintado, no se puede saber antes.
  useLayoutEffect(recalcularPaginas, [recalcularPaginas, accion, inspecciones, fasesKiosco, eligiendoFases])
  useEffect(() => {
    const el = inspRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(recalcularPaginas)
    ro.observe(el)
    return () => ro.disconnect()
  }, [recalcularPaginas, accion])

  // Mientras dura el desplazamiento animado el `onScroll` dispara con las
  // posiciones INTERMEDIAS: sin esta bandera el indicador iba 1 → 2 → 1 → 2
  // (el número de destino, el redondeo del punto medio, y otra vez el
  // destino), que es justo el parpadeo que se veía. Durante la animación
  // manda el número que hemos fijado nosotros; el scroll solo reconcilia
  // cuando el movimiento viene del dedo.
  const animandoInsp = useRef(null)
  const paginarInsp = (signo) => {
    const el = inspRef.current
    if (!el) return
    const salto = saltoInsp(el)
    const siguiente = Math.max(0, Math.min(pagina + signo, paginas - 1))
    const destino = Math.max(0, Math.min(siguiente * salto, el.scrollHeight - el.clientHeight))
    setPagina(siguiente)
    // `scrollTo` con `smooth` no existe en jsdom (tests) ni en navegadores
    // viejos: se cae a la asignación directa, que hace lo mismo sin animar.
    if (typeof el.scrollTo === 'function') {
      if (animandoInsp.current) clearTimeout(animandoInsp.current)
      animandoInsp.current = setTimeout(() => { animandoInsp.current = null }, 450)
      el.scrollTo({ top: destino, behavior: 'smooth' })
    } else {
      el.scrollTop = destino
    }
  }
  useEffect(() => () => { if (animandoInsp.current) clearTimeout(animandoInsp.current) }, [])

  // El arrastre también mueve la lista, así que la página mostrada se
  // reconcilia con la posición real en cada scroll que NO venga de paginar.
  const alScrollInsp = () => {
    const el = inspRef.current
    if (!el || animandoInsp.current) return
    const siguiente = Math.min(Math.round(el.scrollTop / saltoInsp(el)), Math.max(paginas - 1, 0))
    setPagina((prev) => (prev === siguiente ? prev : siguiente))
  }
  // Si hubo arrastre, el toque era scroll, no selección: se descarta el click
  // sintetizado antes de que llegue al botón de la inspección.
  const alHacerClickInsp = (e) => {
    if (!arrastreInsp.current.movido) return
    arrastreInsp.current.movido = false
    e.preventDefault()
    e.stopPropagation()
  }

  // El avatar es la ÚNICA vía táctil a la sesión: abre una pantalla propia
  // dentro del kiosco (cuenta vinculada o QR de emparejamiento). Sigue sin ser
  // una puerta a la UI completa de escritorio, que es inusable con el dedo en
  // un panel de 480x320 sin ratón ni teclado.
  const avatar = (
    <BotonToque
      className="kiosk-avatar"
      tactil={tactil}
      onActivar={() => setAccion('cuenta')}
      data-testid="kiosk-avatar"
    >
      {status ? (
        status.picture ? (
          <img src={status.picture} alt={`Avatar de ${email}`} className="kiosk-avatar-img" />
        ) : (
          <span className="kiosk-avatar-fallback">{inicial}</span>
        )
      ) : (
        <span className="kiosk-sin-sesion">Sin sesión</span>
      )}
    </BotonToque>
  )

  const barraProgreso = progreso && (
    <div className="kiosk-progreso" data-testid="kiosk-progreso">
      <span className="kiosk-progreso-fase">{progreso.fase}</span>
      <span className="kiosk-progreso-pct">{progreso.pct}%</span>
    </div>
  )

  // ------------------------------------------------------------ subiendo
  // Mientras sube no hay nada que tocar, así que la subida ocupa el kiosco
  // entero en lugar de esconderse en la barra de una esquina. Cas: «al subir
  // que salga una animación del logo de Atom en medio con el porcentaje y el
  // logo girando, y a la derecha una nube».
  //
  // El anillo es un `circle` con `stroke-dasharray`: el mismo truco de siempre
  // para un progreso circular sin dependencias. El logo gira y late en CSS
  // (`.kiosk-subida-logo`), y los puntos que suben a la nube son tres `circle`
  // con la misma animación desfasada.
  if (progreso?.subida) {
    const st = progreso.stats || {}
    const pct = Math.max(0, Math.min(Math.round(progreso.pct || 0), 100))
    const CIRC = 2 * Math.PI * 46
    return (
      <div className="kiosk kiosk-subida" data-testid="kiosk-subida">
        <div className="kiosk-subida-escena">
          <div className="kiosk-subida-anillo">
            <svg viewBox="0 0 100 100" aria-hidden="true">
              <circle className="kiosk-subida-pista" cx="50" cy="50" r="46" />
              <circle
                className="kiosk-subida-arco"
                cx="50" cy="50" r="46"
                strokeDasharray={CIRC}
                strokeDashoffset={CIRC * (1 - pct / 100)}
              />
            </svg>
            <img className="kiosk-subida-logo" src="/atom-logo.svg" alt="" />
            <span className="kiosk-subida-pct" data-testid="kiosk-subida-pct">{pct}%</span>
          </div>

          <svg className="kiosk-subida-nube" viewBox="0 0 64 48" aria-hidden="true">
            <path
              className="kiosk-subida-nube-trazo"
              d="M18 38a10 10 0 0 1-.6-19.98A14 14 0 0 1 44 16.5a9.5 9.5 0 0 1 2 18.8"
            />
            <circle className="kiosk-subida-punto kiosk-subida-punto-1" cx="24" cy="44" r="2.4" />
            <circle className="kiosk-subida-punto kiosk-subida-punto-2" cx="32" cy="44" r="2.4" />
            <circle className="kiosk-subida-punto kiosk-subida-punto-3" cx="40" cy="44" r="2.4" />
          </svg>
        </div>

        <div className="kiosk-subida-datos">
          <span className="kiosk-subida-fase">{progreso.fase}</span>
          <span className="kiosk-subida-cifras">
            {st.files_total
              ? `${st.files_done ?? 0}/${st.files_total} archivos`
              : formatBytes(st.bytes_done || 0)}
            {st.mbps ? ` · ${st.mbps.toFixed(1)} MB/s` : ''}
            {st.eta != null ? ` · quedan ~${formatDuracion(st.eta)}` : ''}
          </span>
        </div>
      </div>
    )
  }

  // ------------------------------------------------------------- cuenta
  // Sin sesión vinculada la única salida es el QR: este equipo no tiene
  // navegador propio para el consentimiento OAuth (ver PairScreen).
  if (accion === 'cuenta') {
    const logueado = Boolean(status?.logged_in)
    return (
      <div className="kiosk kiosk-cuenta">
        <div className="kiosk-header kiosk-header-paso">
          <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setAccion(null)}>
            ← Atrás
          </BotonToque>
          <span className="kiosk-titulo">Cuenta</span>
        </div>
        {logueado ? (
          <div className="kiosk-cuenta-datos">
            <span className="kiosk-cuenta-email">{email}</span>
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              onActivar={async () => {
                await api.cloudLogout().catch(() => {})
                onRefreshStatus?.()
              }}
            >
              Cerrar sesión
            </BotonToque>
          </div>
        ) : (
          <PairScreen onPaired={() => { onRefreshStatus?.(); setAccion(null) }} />
        )}
      </div>
    )
  }

  // ---------------------------------------------------------------- paso 1
  if (!accion) {
    return (
      <div className="kiosk">
        <div className="kiosk-header">{avatar}</div>
        {barraProgreso}
        <div className="kiosk-menu">
          <BotonToque
            className="kiosk-menu-btn kiosk-menu-organizar"
            tactil={tactil}
            onActivar={() => setAccion('organizar')}
            disabled={busy}
          >
            Organizar
          </BotonToque>
          <BotonToque
            className="kiosk-menu-btn kiosk-menu-subir"
            tactil={tactil}
            onActivar={() => setAccion('subir')}
            disabled={busy}
          >
            Subir en crudo
          </BotonToque>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------- paso 2
  const esOrganizar = accion === 'organizar'
  // «Subir en crudo» no tiene destino derivado: el destino es el prefijo del
  // bucket, que sale de la inspección elegida.
  const listo = esOrganizar ? Boolean(carpeta) : Boolean(carpeta && inspeccion)

  // Organizar arranca directo (ya tiene su propio modal previo en la UI
  // común). Subir en crudo pasa antes por el resumen: qué se ha volado y
  // cuántos ficheros hay pendientes de verdad. Sin `onComprobarSubida` (tests
  // antiguos, escritorio) se mantiene el comportamiento de siempre.
  async function confirmar() {
    if (esOrganizar) {
      onOrganizar({ origen: carpeta, destino, estadillo })
      return
    }
    if (!onComprobarSubida) {
      onSubirCrudo({ carpeta, inspeccion })
      return
    }
    setComprobando(true)
    try {
      const r = await onComprobarSubida({ carpeta, inspeccion })
      if (r) setResumen(r)
      else onSubirCrudo({ carpeta, inspeccion })
    } finally {
      setComprobando(false)
    }
  }

  // «Subir en crudo», sub-paso A: primero se elige la INSPECCIÓN (destino) y
  // solo después la carpeta de origen — orden invertido a como se pedían
  // antes, porque juntar los dos bloques en una sola pantalla de 480x320
  // dejaba la lista de inspecciones sin apenas alto. Pantalla dedicada: solo
  // cabecera + lista a pantalla completa, nada de carpeta ni botón de subir
  // (ese llega en el sub-paso B, una vez ya hay inspección elegida).
  // Fases realmente presentes en el catálogo, con su recuento, ordenadas por
  // prioridad de trabajo (Vuelo y Preparación primero).
  const conteoFasesKiosco = {}
  for (const i of inspecciones || []) {
    const f = i.fase || ''
    conteoFasesKiosco[f] = (conteoFasesKiosco[f] || 0) + 1
  }
  const fasesDisponibles = Object.keys(conteoFasesKiosco).sort((a, b) => {
    const pa = ORDEN_FASES.indexOf(a), pb = ORDEN_FASES.indexOf(b)
    return (pa === -1 ? 99 : pa) - (pb === -1 ? 99 : pb)
  })
  const alternarFase = (fase) =>
    setFasesKiosco((prev) => (prev.includes(fase) ? prev.filter((f) => f !== fase) : [...prev, fase]))

  if (accion === 'subir' && !inspeccion && eligiendoFases) {
    return (
      <div className="kiosk">
        <div className="kiosk-header kiosk-header-paso">
          <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setEligiendoFases(false)} disabled={busy}>
            ← Listo
          </BotonToque>
          <span className="kiosk-titulo">Fases</span>
          {avatar}
        </div>
        <div className="kiosk-fases">
          {fasesDisponibles.map((fase) => {
            const activa = fasesKiosco.includes(fase)
            const color = COLOR_FASE[fase] || COLOR_FASE_DEFECTO
            return (
              <BotonToque
                key={fase || 'sin-fase'}
                className={activa ? 'kiosk-fase-btn activa' : 'kiosk-fase-btn'}
                tactil={tactil}
                onActivar={() => alternarFase(fase)}
                disabled={busy}
                style={activa ? chip(color) : undefined}
              >
                <span className="kiosk-fase-punto" style={{ background: color }} />
                <span className="kiosk-fase-nombre">{fase || 'Sin fase'}</span>
                <span className="kiosk-fase-num">{conteoFasesKiosco[fase]}</span>
              </BotonToque>
            )
          })}
          {/* Sin ninguna marcada el selector no filtra: sale el catálogo
              entero, que es justo lo que "Todas" significa aquí. */}
          <BotonToque
            className="kiosk-fase-btn kiosk-fase-todas"
            tactil={tactil}
            onActivar={() => setFasesKiosco([])}
            disabled={busy}
          >
            Todas las fases
          </BotonToque>
        </div>
      </div>
    )
  }

  if (accion === 'subir' && !inspeccion) {
    return (
      <div className="kiosk">
        <div className="kiosk-header kiosk-header-paso">
          <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setAccion(null)} disabled={busy}>
            ← Atrás
          </BotonToque>
          <span className="kiosk-titulo">Subir en crudo</span>
          {/* Con `soloLista` el botón "Actualizar lista" de InspeccionSelector
              no se pinta (no cabe encima de la lista en 480x320): esta es la
              única vía para recargar el catálogo en este sub-paso. */}
          <BotonToque
            className="kiosk-actualizar-insp kiosk-filtro-insp"
            tactil={tactil}
            onActivar={() => setEligiendoFases(true)}
            disabled={busy}
            aria-label="Filtrar por fase"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 5h18l-7 8v6l-4 2v-8z" />
            </svg>
            {fasesKiosco.length > 0 && <span className="kiosk-filtro-num">{fasesKiosco.length}</span>}
          </BotonToque>
          <BotonToque
            className="kiosk-actualizar-insp"
            tactil={tactil}
            onActivar={onActualizarInspecciones}
            disabled={busy}
            aria-label="Actualizar lista de inspecciones"
          >
            <svg viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 12a9 9 0 0115.3-6.3M21 12a9 9 0 01-15.3 6.3" />
              <path d="M18 3v4h-4M6 21v-4h4" />
            </svg>
          </BotonToque>
          {avatar}
        </div>

        <div className="kiosk-inspeccion kiosk-inspeccion-full kiosk-insp-paginado">
          <div
            className="kiosk-insp-scroll"
            ref={inspRef}
            onScroll={alScrollInsp}
            {...(tactil ? {
              onPointerDown: alPulsarInsp,
              onPointerMove: alMoverInsp,
              onPointerUp: alSoltarInsp,
              onPointerCancel: alSoltarInsp,
              onPointerLeave: alSoltarInsp,
              onClickCapture: alHacerClickInsp,
            } : {})}
          >
            <InspeccionSelector
              inspecciones={inspecciones}
              onElegir={(prefijo) => {
                const elegida = inspecciones.find((i) => i.prefijo === prefijo) || null
                onSelectInspeccion(elegida)
              }}
              // El kiosco no crea inspecciones nuevas: requiere teclear
              // Empresa--Planta--Año--Tipo, inviable con el panel táctil sin
              // teclado. Esa vía sigue solo en la UI de escritorio.
              onNueva={() => {}}
              ocupado={busy}
              onActualizar={onActualizarInspecciones}
              // Sin teclado físico no hay dónde teclear ni sitio para chips o
              // checkbox en 480x320: en el kiosco la lista va sola, a pantalla
              // completa (el filtrado por defecto sigue siendo el mismo de
              // siempre: sin texto, sin fase activa, sin terminadas).
              soloLista
              fasesControladas={fasesKiosco}
            />
          </div>
          <div className="kiosk-insp-nav">
            <BotonToque
              className="kiosk-insp-nav-btn"
              tactil={tactil}
              onActivar={() => paginarInsp(-1)}
              disabled={busy || pagina <= 0}
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
              onActivar={() => paginarInsp(1)}
              disabled={busy || pagina >= paginas - 1}
              aria-label="Bajar en la lista"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </BotonToque>
          </div>
        </div>
      </div>
    )
  }

  // «Subir en crudo», sub-paso C: resumen EN SECO de lo que se va a subir.
  // Cas: «que los detecte y saque la info de días de vuelo, cantidad de
  // vuelos, piloto, dron etc. y de ahí diga que se han detectado 3 estadillos,
  // 3 días de vuelo y todo eso, y un Aceptar para empezar a subir».
  //
  // No encontrar estadillos NO bloquea (se avisa y se puede subir igual); no
  // haber NADA pendiente sí, porque el botón no haría nada.
  if (resumen) {
    const est = resumen.estadillos || {}
    const info = est.info || null
    const prep = resumen.prepare || {}
    const nEst = est.n_estadillos || 0
    const dias = (info?.fechas || []).length
    const nVuelos = info?.num_vuelos || 0
    const pilotos = (info?.pilotos || []).filter(Boolean)
    const drones = (info?.drones || []).filter(Boolean)
    // `pendientes` es null si no hay sesión (no se pudo listar el bucket): en
    // ese caso el total de la carpeta es la mejor estimación disponible.
    const pendientes = prep.pendientes ?? prep.files ?? 0
    const bytesPend = prep.bytes_pendientes ?? prep.bytes ?? 0
    const sinArchivos = !prep.ok || pendientes === 0
    const plural = (n, sing, pl) => `${n} ${n === 1 ? sing : pl}`
    return (
      <div className="kiosk">
        <div className="kiosk-header kiosk-header-paso">
          <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setResumen(null)} disabled={busy}>
            ← Atrás
          </BotonToque>
          <span className="kiosk-titulo">Antes de subir</span>
          {avatar}
        </div>

        <div className="kiosk-resumen" data-testid="kiosk-resumen">
          <div className="kiosk-resumen-cifras">
            <div className="kiosk-resumen-cifra">
              <strong>{nEst}</strong>
              <span>{nEst === 1 ? 'estadillo' : 'estadillos'}</span>
            </div>
            <div className="kiosk-resumen-cifra">
              <strong>{dias}</strong>
              <span>{dias === 1 ? 'día de vuelo' : 'días de vuelo'}</span>
            </div>
            <div className="kiosk-resumen-cifra">
              <strong>{nVuelos}</strong>
              <span>{nVuelos === 1 ? 'vuelo' : 'vuelos'}</span>
            </div>
          </div>

          <dl className="kiosk-resumen-detalle">
            {pilotos.length > 0 && (
              <>
                <dt>{pilotos.length === 1 ? 'Piloto' : 'Pilotos'}</dt>
                <dd>{pilotos.join(', ')}</dd>
              </>
            )}
            {drones.length > 0 && (
              <>
                <dt>{drones.length === 1 ? 'Dron' : 'Drones'}</dt>
                <dd>{drones.join(', ')}</dd>
              </>
            )}
            <dt>Archivos</dt>
            <dd data-testid="kiosk-resumen-archivos">
              {sinArchivos ? '—' : `${plural(pendientes, 'archivo', 'archivos')} · ${formatBytes(bytesPend)}`}
            </dd>
          </dl>

          {nEst === 0 && (
            <p className="kiosk-resumen-aviso" data-testid="kiosk-resumen-sin-estadillo">
              No se ha detectado ningún estadillo. Se puede subir igual.
            </p>
          )}
          {sinArchivos && (
            <p className="kiosk-resumen-aviso kiosk-resumen-aviso-bloqueo" data-testid="kiosk-resumen-sin-archivos">
              {prep.error || 'No hay ningún archivo pendiente de subir.'}
            </p>
          )}
        </div>

        <div className="kiosk-acciones">
          <BotonToque
            className="kiosk-btn kiosk-btn-subir-crudo"
            tactil={tactil}
            onActivar={() => { setResumen(null); onSubirCrudo({ carpeta, inspeccion }) }}
            disabled={busy || sinArchivos}
            data-testid="kiosk-resumen-aceptar"
          >
            Aceptar y subir
          </BotonToque>
        </div>
      </div>
    )
  }

  return (
    <div className="kiosk">
      <div className="kiosk-header kiosk-header-paso">
        <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setAccion(null)} disabled={busy}>
          ← Atrás
        </BotonToque>
        <span className="kiosk-titulo">{esOrganizar ? 'Organizar' : 'Subir en crudo'}</span>
        {avatar}
      </div>

      {!esOrganizar && (
        <div className="kiosk-inspeccion">
          <div className="field-row">
            <input className="glass-input" type="text" value={inspeccion.etiqueta || inspeccion.nombre || ''} readOnly />
            <BotonToque
              className="btn-ghost"
              tactil={tactil}
              onActivar={() => onSelectInspeccion(null)}
              disabled={busy}
            >
              Cambiar
            </BotonToque>
          </div>
        </div>
      )}

      <div className="kiosk-carpeta">
        <BotonToque className="btn-ghost kiosk-btn-carpeta" tactil={tactil} onActivar={onPickCarpeta} disabled={busy}>
          Elegir carpeta…
        </BotonToque>
        <span className="kiosk-carpeta-actual">{carpeta || 'Sin carpeta'}</span>
        {esOrganizar && carpeta && <span className="kiosk-destino">{destino}</span>}
      </div>

      {esOrganizar && (
        <div className="kiosk-estadillo">
          <EstadilloField value={estadillo} onChange={onEstadillo} disabled={busy} />
        </div>
      )}

      {barraProgreso}

      <div className="kiosk-acciones">
        <BotonToque
          className={'kiosk-btn ' + (esOrganizar ? 'kiosk-btn-organizar' : 'kiosk-btn-subir-crudo')}
          tactil={tactil}
          onActivar={confirmar}
          disabled={!listo || busy || comprobando}
        >
          {esOrganizar ? 'Organizar' : comprobando ? 'Comprobando…' : 'Subir'}
        </BotonToque>
      </div>
    </div>
  )
}
