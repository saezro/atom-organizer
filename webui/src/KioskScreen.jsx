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
import FolderPicker from './FolderPicker.jsx'
import PairScreen from './PairScreen.jsx'
import InspeccionSelector, { COLOR_FASE, COLOR_FASE_DEFECTO, ORDEN_FASES, chip } from './InspeccionSelector.jsx'
import EstadilloField from './EstadilloField.jsx'
import EsperaEstadillo from './trabajo/EsperaEstadillo.jsx'
import { formatBytes, formatDuracion } from './formato.js'
import MenuApps from './MenuApps.jsx'
import BotonAtras from './BotonAtras.jsx'
import Paginador from './Paginador.jsx'
import KioskTareas from './KioskTareas.jsx'
import KioskAjustes from './KioskAjustes.jsx'
import KioskLock from './KioskLock.jsx'
import BarraEstado from './BarraEstado.jsx'
import BannerConexion from './BannerConexion.jsx'
import Avatar from './Avatar.jsx'
import { APPS } from './apps/registry.js'

// Tiempos de la reproduccion visual de `accionRemota` (control remoto del
// kiosco, Task «se ve como se mueve a los sitios»): pasos de navegacion
// (menu -> pantalla) y resaltado de botones.
const PASO_MS = 450
// Tras entrar en la pantalla Organizer (accion='organizar'), tiempo que se
// deja a la vista antes de abrir el FolderPicker en modo reproduccion: que
// de tiempo a leerse el cambio de pantalla antes de que aparezca el overlay.
const ANTES_DE_ABRIR_SELECTOR_MS = 800
// Cuanto se resalta (pulso azul) el boton "Recibir estadillo" al terminar
// una reproduccion remota de carpeta, antes de "pulsarlo" de verdad (mismo
// patron que `resaltarSelector` de arriba, duracion propia porque aqui SI
// hace falta esperar a que se vea antes de disparar la accion).
const PULSO_ESTADILLO_MS = 1200

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
  // Resumen rico del estadillo recibido por red (planta/fechas/pilotos/
  // drones/vuelos/tiempo total, ver `EsperaEstadillo.jsx`): `null` cuando el
  // estadillo actual viene de autodetección o elección a mano. Se pinta
  // como tarjeta de solo lectura en `EstadilloField` en vez del selector.
  estadilloInfo,
  // `{buscando, encontrado, nombre, recibidoLan}` de la carpeta elegida
  // (App.jsx, autodetección al cambiar `kioskCarpeta`): indicador junto al
  // selector de carpeta, ver `.kiosk-carpeta` más abajo.
  estadilloEnCarpeta = null,
  // Confirma un candidato de `estadilloEnCarpeta.candidatosPadre` (estadillo
  // válido suelto en la carpeta PADRE de `carpeta`) como estadillo del
  // trabajo, mismo camino que `onEstadillo` con una elección manual
  // (`App.jsx`, `kioskUsarCandidatoPadre`). Nunca se usa sin pulsar.
  onUsarCandidatoPadre,
  onOrganizar,
  onSubirCrudo,
  onComprobarSubida,
  onRefreshStatus,
  // Sin sesión válida se bloquea elegir planta (la lista la sirve ATOM Suite)
  // y también "Subir en crudo": subir sin dispositivo emparejado solo dejaba
  // el lote en cola, y en un kiosco sin teclado eso se lee como que ha subido.
  // "Organizar" sigue habilitado: es 100% local y no necesita sesión.
  credencialOk = true,
  // Contador que llega desde `App.jsx` (el cartel `AvisoSesion`, montado
  // fuera de este componente): cada incremento reabre el paso "cuenta"
  // (PairScreen), reutilizando el mismo camino que el menú de cuenta.
  abrirCuenta = 0,
  busy,
  progreso,
  // Resultado de la última subida: {ok, error?, subidos, omitidos, bytes,
  // elapsed, fallidos, cancelada}. Mientras exista se pinta su pantalla.
  resultado,
  onCerrarResultado,
  // Lanza una tarea suelta del catálogo (`schema.js`) por el mismo camino que
  // el escritorio; sin ella la app «Tareas» no puede ejecutar nada.
  onRunTask,
  // Solo para pruebas: permite montar el componente directamente en un paso.
  accionInicial = null,
  // Accion remota en cola a reproducir (`KioskGuard`, Task «control remoto
  // del kiosco: se ve como se mueve a los sitios»): {accion, path?, _id} o
  // null si no hay ninguna pendiente. `onAccionRemotaConsumida` avisa a
  // `KioskGuard` de que ya se reprodujo entera, para que saque la siguiente
  // de la cola (llegan de una en una, nunca en paralelo).
  accionRemota = null,
  onAccionRemotaConsumida,
}) {
  // El puntero de X sigue al dedo en el panel resistivo y se queda clavado
  // donde tocaste. `cursor: none` sobre `.kiosk` no basta: el hueco entre las
  // tarjetas del launcher cae fuera y ahi reaparece. Se marca el documento
  // entero mientras el kiosco esta montado; en escritorio (Windows) este
  // componente no se monta nunca, asi que no puede filtrarse.
  useEffect(() => {
    document.documentElement.classList.add('kiosk-modo')
    return () => document.documentElement.classList.remove('kiosk-modo')
  }, [])

  const [accion, setAccion] = useState(accionInicial)
  const [cambiandoPin, setCambiandoPin] = useState(false)
  // `abrirCuenta` llega de fuera (el cartel `AvisoSesion` en `App.jsx`): cada
  // vez que cambia (incluida la primera si arranca > 0) salta al paso
  // "cuenta". Se compara contra el valor anterior, no contra 0, para no
  // reabrirlo en cada render si el operario ya volvió atrás.
  const abrirCuentaPrev = useRef(abrirCuenta)
  useEffect(() => {
    if (abrirCuenta !== abrirCuentaPrev.current) {
      abrirCuentaPrev.current = abrirCuenta
      setAccion('cuenta')
    }
  }, [abrirCuenta])
  // El catálogo trae cientos de inspecciones de todo el ciclo de vida, pero si
  // estás subiendo material la inspección está en vuelo: arrancar filtrado a esa
  // fase deja una lista corta. El resto sigue a un toque, en la pantalla de fases.
  const [fasesKiosco, setFasesKiosco] = useState(['Vuelo'])
  const [eligiendoFases, setEligiendoFases] = useState(false)
  // Comprobación en seco previa a subir (ver `confirmar`): `null` mientras no
  // se ha pedido, `{prepare, estadillos}` cuando ya hay resumen que pintar.
  const [resumen, setResumen] = useState(null)
  // Comprobación final tras subir: se vuelve a listar el bucket para confirmar
  // que no queda nada pendiente. null = aún no lanzada, 'curso' | {pendientes}
  const [verificacion, setVerificacion] = useState(null)
  // Pantalla «Sistema»: modo pendiente de confirmar ('poweroff'|'reboot'),
  // orden ya enviada, y último error de apagado.
  const [confirmarApagado, setConfirmarApagado] = useState(null)
  const [apagando, setApagando] = useState(false)
  const [errorSistema, setErrorSistema] = useState(null)
  const [comprobando, setComprobando] = useState(false)
  // Flujo «Organizar»: en vez de elegir el estadillo a mano (`EstadilloField`),
  // «Recibir desde Estadillo Digital» pone la Pi en espera del CSV que manda
  // el portátil (`EsperaEstadillo`, mismo componente que `PasoEstadillo` en
  // escritorio). Al llegar rellena el estadillo del flujo (`onEstadillo`) y
  // vuelve a la vista normal; al cancelar/desmontarse (salir del paso), la
  // propia `EsperaEstadillo` cancela la espera en el backend.
  const [esperandoEstadillo, setEsperandoEstadillo] = useState(false)
  // Si la espera ya estaba activa en el backend (retomada al montar, ver
  // efecto de abajo), `EsperaEstadillo` no debe reiniciarla.
  const [retomarEspera, setRetomarEspera] = useState(false)

  // Mientras la espera está activa, cualquier cambio de la carpeta del
  // kiosco (el botón «Elegir carpeta…» de arriba, o el que enseña
  // `EsperaEstadillo` cuando aún no hay ninguna) se propaga al backend: la
  // espera puede arrancarse sin carpeta y elegirla después sin perder la
  // cuenta atrás (`estadillo_espera_carpeta`, no reinicia caducidad).
  // Fire-and-forget, igual que el resto de llamadas de este flujo.
  useEffect(() => {
    if (!esperandoEstadillo) return
    api.estadilloEsperaCarpeta(carpeta || null).catch(() => {})
  }, [carpeta, esperandoEstadillo])

  // Retoma la espera de estadillo si Chromium se recargó (o el PIN estaba
  // bloqueado) mientras el backend seguía esperando: sin esto la UI se
  // queda en el paso 1 del kiosco aunque el portátil siga mandando datos.
  // Solo al montar: `KioskGuard` desmonta este componente entero mientras el
  // PIN está bloqueado, así que un solo chequeo al montar cubre también el
  // caso "se retoma tras desbloquear".
  useEffect(() => {
    let vivo = true
    Promise.resolve(api.estadilloEsperaEstado?.())
      .then((r) => {
        if (!vivo) return
        if (r?.esperando && !r?.caducado) {
          setRetomarEspera(true)
          setAccion('organizar')
          setEsperandoEstadillo(true)
        }
      })
      .catch(() => {})
    return () => { vivo = false }
  }, [])

  // Reproduccion visual de acciones remotas (`KioskGuard`, cola
  // `accionRemota`): cada una "se ve" en pantalla como si la tocara una
  // persona, no solo surte efecto en silencio. Van siempre a la vista
  // Organizer (mismo camino que el boton del menu), con pasos fijos de
  // ~450ms para que de tiempo a leer el movimiento en un panel de 480x320.
  // `carpeta` abre ademas el `FolderPicker` en modo reproduccion (mas abajo,
  // `carpetaRepro`): esa es la unica accion que NO se marca consumida aqui,
  // la marca `terminarCarpetaRepro` cuando el propio picker termina de
  // recorrer los segmentos.
  const [carpetaRepro, setCarpetaRepro] = useState(null)
  const [resaltarSelector, setResaltarSelector] = useState(null)
  useEffect(() => {
    if (!accionRemota) return undefined
    let cancelado = false
    const timers = []
    const tras = (ms, fn) => { timers.push(setTimeout(() => { if (!cancelado) fn() }, ms)) }
    const acabar = () => { if (!cancelado) onAccionRemotaConsumida?.() }

    if (accionRemota.accion === 'carpeta') {
      setAccion('organizer')
      tras(PASO_MS, () => setAccion('organizar'))
      tras(PASO_MS + ANTES_DE_ABRIR_SELECTOR_MS, () => setCarpetaRepro(accionRemota.path || ''))
    } else if (accionRemota.accion === 'organizar') {
      setAccion('organizer')
      tras(PASO_MS, () => setResaltarSelector('[data-control-resaltar="organizar"]'))
      tras(PASO_MS + 600, () => { setResaltarSelector(null); acabar() })
    } else if (accionRemota.accion === 'cancelar') {
      // No hay navegacion: se resalta el boton de cancelar que YA este
      // visible en la pantalla actual (si hay alguno) y nada mas — el
      // backend ya cancelo el analisis, aqui solo se enseña.
      setResaltarSelector('[data-control-resaltar="cancelar"]')
      tras(600, () => { setResaltarSelector(null); acabar() })
    } else {
      acabar()
    }
    return () => { cancelado = true; timers.forEach(clearTimeout) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accionRemota])

  // Pinta la clase de pulso azul sobre el elemento real del DOM que marca
  // `resaltarSelector`: mas simple que enhebrar una prop de resaltado por
  // cada boton candidato (viven en pantallas y hasta ficheros distintos,
  // `EsperaEstadillo.jsx` incluido).
  useEffect(() => {
    if (!resaltarSelector) return undefined
    const el = document.querySelector(resaltarSelector)
    if (!el) return undefined
    el.classList.add('kiosk-control-pulso')
    return () => el.classList.remove('kiosk-control-pulso')
  }, [resaltarSelector])

  // Timer del "pulso" de "Recibir estadillo" al terminar una reproduccion
  // remota de carpeta: en un ref para poder limpiarlo si la pantalla se
  // desmonta a medio pulso (navegacion) o si otra reproduccion arranca antes
  // de que termine.
  const pulsoEstadilloRef = useRef(null)
  useEffect(() => () => { if (pulsoEstadilloRef.current) clearTimeout(pulsoEstadilloRef.current) }, [])

  // Al terminar de "enseñar" una carpeta recibida por control remoto
  // (`accionRemota.accion === 'carpeta'`, ver el `FolderPicker` de mas
  // abajo): si esa carpeta NO tiene ya un estadillo detectado, el kiosco
  // "pulsa" solo el boton "Recibir estadillo" -mismo pulso azul que resalta
  // acciones remotas reales, `.kiosk-control-pulso`- y, tras dejarlo ver
  // ~1200ms, dispara EL MISMO handler que un toque real (arranca la espera
  // del estadillo, `estadillo_espera_iniciar`), para que quede esperando
  // sin que haga falta otro toque. Con estadillo ya encontrado en la
  // carpeta no hace nada: no tiene sentido esperar uno que ya esta.
  const terminarCarpetaRepro = useCallback(() => {
    setCarpetaRepro(null)
    onAccionRemotaConsumida?.()
    if (pulsoEstadilloRef.current) {
      clearTimeout(pulsoEstadilloRef.current)
      pulsoEstadilloRef.current = null
    }
    if (accion !== 'organizar' || esperandoEstadillo) return
    if (estadilloEnCarpeta?.encontrado) return
    const el = document.querySelector('.kiosk-card-estadillo')
    if (!el) return
    el.classList.add('kiosk-control-pulso')
    pulsoEstadilloRef.current = setTimeout(() => {
      pulsoEstadilloRef.current = null
      el.classList.remove('kiosk-control-pulso')
      setRetomarEspera(false)
      setEsperandoEstadillo(true)
    }, PULSO_ESTADILLO_MS)
  }, [onAccionRemotaConsumida, accion, esperandoEstadillo, estadilloEnCarpeta])

  const destino = derivarDestino(carpeta)
  const email = status?.email || ''
  const nombre = status?.nombre || ''
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
      {status && status.logged_in && status.estado !== 'sin-credencial' ? (
        <Avatar
          src={status.picture}
          alt={`Avatar de ${email}`}
          inicial={inicial}
          imgClassName="kiosk-avatar-img"
          fallbackClassName="kiosk-avatar-fallback"
        />
      ) : (
        <span className="kiosk-sin-sesion">Sin sesión</span>
      )}
    </BotonToque>
  )

  // Se declara una vez y se pinta en cada pantalla del flujo: el estado de red
  // importa igual (o más) al elegir inspección o al confirmar una subida que en
  // el launcher. `compacto` en los pasos, que van justos de alto.
  const bannerConexion = (
    <BannerConexion estado={status?.estado} pendientes={status?.pendientes || 0} compacto />
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
  // Comprobación final: al aparecer el resultado de una subida correcta se
  // vuelve a preguntar al bucket qué falta. Es lo que convierte «acabó» en
  // «acabó BIEN» sin tener que mirar logs desde la Pi.
  useEffect(() => {
    if (!resultado || !resultado.ok || !onComprobarSubida || !carpeta || !inspeccion) return
    let vivo = true
    setVerificacion('curso')
    onComprobarSubida({ carpeta, inspeccion })
      .then((r) => {
        if (!vivo) return
        const prep = r?.prepare || {}
        setVerificacion(
          prep.ok === false
            ? { error: prep.error || 'No se pudo comprobar el bucket.' }
            : { pendientes: prep.pendientes ?? prep.files ?? 0 }
        )
      })
      .catch((e) => { if (vivo) setVerificacion({ error: String(e?.message || e) }) })
    return () => { vivo = false }
  }, [resultado, onComprobarSubida, carpeta, inspeccion])

  // ---------------------------------------------------------- resultado
  // Toda subida termina en una pantalla explícita (bien, vacía o error): en la
  // Pi no hay consola ni logs a mano, y una subida que se cierra sola parece
  // que «no ha pasado nada» (feedback pedido tras la subida de prueba de 0
  // archivos).
  if (resultado) {
    const okey = Boolean(resultado.ok)
    const vacia = okey && !resultado.subidos
    return (
      <div className="kiosk kiosk-resultado" data-testid="kiosk-resultado">
        <div className={'kiosk-resultado-icono' + (okey ? ' ok' : ' mal')} aria-hidden="true">
          {okey ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="4 13 9 18 20 6" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 4v10" />
              <circle cx="12" cy="19" r="0.6" fill="currentColor" />
            </svg>
          )}
        </div>
        <span className="kiosk-resultado-titulo">
          {resultado.cancelada
            ? 'Subida cancelada'
            : !okey
              ? 'La subida no se completó'
              : vacia
                ? 'No había nada nuevo que subir'
                : 'Subida completada'}
        </span>
        <span className="kiosk-resultado-detalle" data-testid="kiosk-resultado-detalle">
          {okey
            ? `${resultado.subidos ?? 0} archivos subidos` +
              (resultado.omitidos ? ` · ${resultado.omitidos} ya estaban` : '') +
              (resultado.bytes ? ` · ${formatBytes(resultado.bytes)}` : '') +
              (resultado.elapsed ? ` · ${formatDuracion(resultado.elapsed)}` : '') +
              // Garantía de completitud del backend: no «la barra llegó al
              // 100 %», sino los objetos comprobados uno a uno contra el
              // bucket antes de dar el lote por bueno.
              (resultado.items_total == null
                ? ''
                : resultado.verificado
                  ? ` · ${resultado.verificados ?? 0}/${resultado.items_total} verificados en la nube`
                  : ' · sin comprobar en la nube') +
              (resultado.rondas > 1 ? ` · ${resultado.rondas} intentos` : '')
            : (resultado.error || `${resultado.fallidos ?? 0} archivos fallaron`) +
                (resultado.items_total != null && resultado.verificados != null
                  ? ` · Verificados ${resultado.verificados}/${resultado.items_total}`
                  : '')}
        </span>
        {okey && (
          <span className="kiosk-resultado-verifica" data-testid="kiosk-resultado-verifica">
            {verificacion === 'curso' || verificacion === null
              ? 'Comprobando que todo está en la nube…'
              : verificacion.error
                ? `No se pudo comprobar: ${verificacion.error}`
                : verificacion.pendientes === 0
                  ? '✓ Comprobado: no queda nada pendiente'
                  : `⚠ Quedan ${verificacion.pendientes} archivos sin subir`}
          </span>
        )}
        {/* `.kiosk-acciones` es flex ROW: `kiosk-resultado` ES el `.kiosk`
            (flex COLUMN, alto fijo a viewport), así que sin envolver el botón
            su `flex:1` se comía todo el hueco que dejan icono/textos. */}
        <div className="kiosk-acciones">
          <BotonToque
            className="btn kiosk-btn"
            tactil={tactil}
            onActivar={() => { setVerificacion(null); onCerrarResultado?.() }}
            data-testid="kiosk-resultado-aceptar"
          >
            Aceptar
          </BotonToque>
        </div>
      </div>
    )
  }

  if (progreso?.subida) {
    const st = progreso.stats || {}
    const pct = Math.max(0, Math.min(Math.round(progreso.pct || 0), 100))
    const CIRC = 2 * Math.PI * 46
    return (
      <div className="kiosk kiosk-subida" data-testid="kiosk-subida">
        <BarraEstado compacto />
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
            {progreso.stats
              ? `${st.files_total
                    ? `${st.files_done ?? 0}/${st.files_total} archivos`
                    : formatBytes(st.bytes_done || 0)}${
                  st.mbps ? ` · ${st.mbps.toFixed(1)} MB/s` : ''
                }${st.eta != null ? ` · quedan ~${formatDuracion(st.eta)}` : ''}`
              : 'Comprobando qué falta por subir…'}
          </span>
        </div>
      </div>
    )
  }

  // ------------------------------------------------------------ sistema
  // Apagar/reiniciar la Pi desde la propia pantalla: es el unico control
  // fisico que hay (no hay teclado ni boton de encendido accesible). Dos
  // toques SIEMPRE: el panel resistivo produce toques fantasma y un apagado
  // accidental a mitad de una subida es caro.
  if (accion === 'sistema') {
    const pedir = (modo) => setConfirmarApagado(modo)
    return (
      <div className="kiosk kiosk-sistema">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras
            tactil={tactil}
            onActivar={() => { setConfirmarApagado(null); setAccion(null) }} />
          <span className="kiosk-titulo">Sistema</span>
        </div>
        {confirmarApagado ? (
          <div className="kiosk-sistema-confirmar" data-testid="kiosk-sistema-confirmar">
            <span className="kiosk-sistema-pregunta">
              {confirmarApagado === 'poweroff' ? '¿Apagar el equipo?' : '¿Reiniciar el equipo?'}
            </span>
            {busy && <span className="kiosk-sistema-aviso">Hay trabajo en curso.</span>}
            <div className="kiosk-sistema-botones">
              <BotonToque
                className="btn kiosk-btn"
                tactil={tactil}
                data-testid="kiosk-sistema-si"
                onActivar={async () => {
                  setApagando(true)
                  const r = await api.sistemaApagar(confirmarApagado).catch((e) => ({ ok: false, error: String(e) }))
                  if (!r || r.ok === false) {
                    setApagando(false)
                    setConfirmarApagado(null)
                    setErrorSistema(r?.error || 'No se pudo ejecutar.')
                  }
                }}
              >
                {apagando ? 'Enviando…' : 'Sí'}
              </BotonToque>
              <BotonToque
                className="btn-ghost kiosk-btn"
                tactil={tactil}
                data-testid="kiosk-sistema-cancelar"
                onActivar={() => setConfirmarApagado(null)}
              >
                Cancelar
              </BotonToque>
            </div>
          </div>
        ) : (
          <div className="kiosk-sistema-botones">
            <BotonToque
              className="btn kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-sistema-apagar"
              onActivar={() => pedir('poweroff')}
            >
              Apagar
            </BotonToque>
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              data-testid="kiosk-sistema-reiniciar"
              onActivar={() => pedir('reboot')}
            >
              Reiniciar
            </BotonToque>
          </div>
        )}
        {errorSistema && (
          <p className="kiosk-sistema-error" data-testid="kiosk-sistema-error">{errorSistema}</p>
        )}
      </div>
    )
  }

  // ------------------------------------------------------------- cuenta
  // Sin sesión vinculada la única salida es el QR: este equipo no tiene
  // navegador propio para el consentimiento OAuth (ver PairScreen).
  if (accion === 'cuenta') {
    const logueado = Boolean(status?.logged_in)
    const pendientes = status?.pendientes || 0
    const ultimoAcceso = status?.validada_en
      ? new Date(status.validada_en * 1000).toLocaleString('es-ES', {
          day: '2-digit', month: '2-digit', year: 'numeric',
          hour: '2-digit', minute: '2-digit',
        })
      : 'Sin registrar'
    return (
      <div className="kiosk kiosk-cuenta">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={() => { setCambiandoPin(false); setAccion(null) }} />
          <span className="kiosk-titulo">Cuenta</span>
        </div>
        {!logueado ? (
          <PairScreen onPaired={() => { onRefreshStatus?.(); setAccion(null) }} />
        ) : cambiandoPin ? (
          <KioskLock
            modo="cambiar"
            onOk={() => setCambiandoPin(false)}
            onCancelar={() => setCambiandoPin(false)}
          />
        ) : (
          // 480x320 es apaisada y muy baja: en columna única (foto grande +
          // email + datos + botones apilados) no cabía sin scroll. Dos
          // columnas usando el ANCHO: izquierda foto+email, derecha datos y
          // acciones.
          <div className="kiosk-perfil">
            <div className="kiosk-perfil-izq">
              <div className="kiosk-perfil-foto-marco">
                <Avatar
                  src={status?.picture}
                  alt={`Foto de ${email}`}
                  inicial={inicial}
                  imgClassName="kiosk-perfil-foto"
                  fallbackClassName="kiosk-perfil-inicial"
                  testId="kiosk-perfil-foto"
                />
              </div>
              {nombre && (
                <span className="kiosk-perfil-nombre" data-testid="kiosk-perfil-nombre">{nombre}</span>
              )}
              <span className="kiosk-perfil-email">{email}</span>
            </div>
            <div className="kiosk-perfil-der">
              <dl className="kiosk-perfil-datos">
                <dt>Último acceso</dt>
                <dd>{ultimoAcceso}</dd>
                <dt>Subidas pendientes</dt>
                <dd>{pendientes}</dd>
              </dl>
              <div className="kiosk-perfil-acciones">
                <BotonToque
                  className="btn kiosk-btn"
                  tactil={tactil}
                  onActivar={() => setCambiandoPin(true)}
                >
                  Cambiar PIN
                </BotonToque>
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
            </div>
          </div>
        )}
      </div>
    )
  }

  // ------------------------------------------------------------- organizer
  // La app «Organizer» es el flujo de fotos de siempre; dentro elige entre sus
  // dos acciones. Antes eran dos entradas del launcher, pero son un mismo
  // trabajo en dos pasos, no dos apps.
  if (accion === 'organizer') {
    return (
      <div className="kiosk kiosk-organizer">
        <div className="kiosk-header kiosk-header-paso">
          <BotonAtras tactil={tactil} onActivar={() => setAccion(null)} disabled={busy} />
          <span className="kiosk-titulo">Organizer</span>
        </div>
        {bannerConexion}
        {barraProgreso}
        <div className="kiosk-menu">
          <BotonToque
            className="kiosk-menu-btn kiosk-menu-organizar"
            tactil={tactil}
            onActivar={() => setAccion('organizar')}
            disabled={busy}
            data-control-resaltar="organizar"
          >
            Organizar
          </BotonToque>
          <BotonToque
            className="kiosk-menu-btn kiosk-menu-subir"
            tactil={tactil}
            onActivar={() => setAccion('subir')}
            disabled={busy || !credencialOk}
          >
            Subir en crudo
          </BotonToque>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------- tareas
  if (accion === 'tareas') {
    return (
      <KioskTareas
        tactil={tactil}
        busy={busy}
        onEjecutar={(task, params) => onRunTask?.(task, params)}
        onVolver={() => setAccion(null)}
      />
    )
  }

  // --------------------------------------------------------------- ajustes
  if (accion === 'ajustes') {
    return <KioskAjustes tactil={tactil} onVolver={() => setAccion(null)} />
  }

  // ---------------------------------------------------------------- paso 1
  // Menu tipo launcher: rejilla de apps paginada (`apps/registry.js`). Cada
  // entrada del registro abre una `accion` con el mismo id, asi que anadir una
  // app es anadir una entrada al registro y su pantalla aqui abajo.
  if (!accion) {
    return (
      <div className="kiosk">
        <div className="kiosk-header"><BarraEstado />{avatar}</div>
        <BannerConexion estado={status?.estado} pendientes={status?.pendientes || 0} />
        {barraProgreso}
        <MenuApps
          apps={APPS}
          tactil={tactil}
          disabled={busy}
          onAbrir={(id) => setAccion(id)}
        />
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
          <BotonAtras tactil={tactil} onActivar={() => setEligiendoFases(false)} disabled={busy} etiqueta="Listo" />
          <span className="kiosk-titulo">Fases</span>
        </div>
        {bannerConexion}
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
          <BotonAtras tactil={tactil} onActivar={() => setAccion('organizer')} disabled={busy} />
          <span className="kiosk-titulo">Subir en crudo</span>
          {/* Con `soloLista` el botón "Actualizar lista" de InspeccionSelector
              no se pinta (no cabe encima de la lista en 480x320): esta es la
              única vía para recargar el catálogo en este sub-paso. */}
          {/* El contador va FUERA del boton: `.pulsable` recorta con
              `overflow:hidden` para contener la onda del toque, y dentro del
              boton circular el numero salia cortado por el borde del circulo. */}
          <span className="kiosk-filtro-wrap">
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
            </BotonToque>
            {fasesKiosco.length > 0 && <span className="kiosk-filtro-num">{fasesKiosco.length}</span>}
          </span>
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
        </div>
        {bannerConexion}

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
            {credencialOk ? (
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
            ) : (
              // Gating PARCIAL: solo se bloquea elegir planta (la lista la
              // sirve ATOM Suite). "Organizar" y "Subir en crudo" no se tocan.
              <div className="kiosk-sistema-error" data-testid="kiosk-sin-credencial">
                No se puede elegir planta sin sesión: la lista de inspecciones la sirve ATOM Suite.
                Vuelve a emparejar el dispositivo con el QR.
              </div>
            )}
          </div>
          <Paginador
            pagina={pagina}
            totalPaginas={paginas}
            onPagina={(n) => paginarInsp(n - pagina)}
            tactil={tactil}
            disabled={busy}
            testidPrefijo="kiosk-insp-"
            contexto="la lista"
          />
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
          <BotonAtras tactil={tactil} onActivar={() => setResumen(null)} disabled={busy} />
          <span className="kiosk-titulo">Antes de subir</span>
        </div>
        {bannerConexion}

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
          {/* Candidatos de la carpeta PADRE: solo informativo aquí (nunca se
              usan en automático, decisión de Rodrigo, caso Marcos). Confirmar
              uno desde el kiosco requeriría enchufarlo al flujo de selección
              de `kioskEstadillo`, fuera de este paso de resumen; se avisa con
              la ruta completa para que el operario lo resuelva desde el paso
              anterior o desde el escritorio. */}
          {Array.isArray(est.candidatos_padre) && est.candidatos_padre.length > 0 && (
            <p className="kiosk-resumen-aviso" data-testid="kiosk-resumen-candidatos-padre">
              Hay estadillo(s) FUERA de la carpeta seleccionada, sin usar: {est.candidatos_padre.join(', ')}
            </p>
          )}
          {/* Aviso temprano de `estadillo.aviso_estadillos_misma_carpeta`: 2+
              estadillos en la MISMA carpeta van a abortar el run más tarde
              (`ErrorEstadillosMismaCarpeta`). Se avisa ya aquí, antes de
              subir nada. */}
          {est.aviso_misma_carpeta && (
            <p className="kiosk-resumen-aviso kiosk-resumen-aviso-bloqueo" data-testid="kiosk-resumen-misma-carpeta">
              {est.aviso_misma_carpeta}
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
        <BotonAtras
          tactil={tactil}
          onActivar={() => { setEsperandoEstadillo(false); setRetomarEspera(false); setAccion('organizer') }}
          disabled={busy}
        />
        <span className="kiosk-titulo">{esOrganizar ? 'Organizar' : 'Subir en crudo'}</span>
      </div>
      {bannerConexion}

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

      {/* Mientras se espera el estadillo del portátil, o ya con uno recibido
          (`EstadilloField` pinta la tarjeta «Estadillo recibido», que en
          480x320 necesita TODO el hueco de `.kiosk-estadillo` — ver
          `RECIBIDO_FILAS_POR_PAGINA` en `EstadilloField.jsx`), el control de
          carpeta deja de pintarse: la carpeta ya se eligió antes de llegar
          aquí (es el paso previo del propio flujo) y volver a mostrarla aquí
          solo le robaba altura a la tarjeta — con RECIBIDO_FILAS_POR_PAGINA=2
          hacían falta hasta 5 páginas para ver un estadillo de 3 vuelos
          (queja de Rodrigo, 2026-09-23: "esta full cortado... lo de elegir
          carpeta se queda fijo"). Solo se rescata como línea mínima (sin
          engordar, un botón + texto) en el caso raro de haber arrancado la
          espera SIN carpeta todavía (`EsperaEstadillo` avisa "Elige carpeta
          arriba" en ese caso, `faltaCarpeta`): ahí sí hace falta un sitio
          desde el que elegirla sin salir de la espera. */}
      {(esperandoEstadillo || (esOrganizar && estadillo?.length === 1 && estadilloInfo)) && !carpeta ? (
        <div className="kiosk-carpeta kiosk-carpeta-compacta">
          <BotonToque className="btn-ghost kiosk-btn-carpeta" tactil={tactil} onActivar={onPickCarpeta} disabled={busy}>
            Elegir carpeta
          </BotonToque>
        </div>
      ) : esperandoEstadillo || (esOrganizar && estadillo?.length === 1 && estadilloInfo) ? null : (
        // Dos tarjetas táctiles grandes lado a lado (pedido de Rodrigo,
        // 2026-09-22): "Elegir carpeta" y "Recibir estadillo" en vez de un
        // botón suelto + texto aparte. En "Subir en crudo" solo existe la de
        // carpeta (no hay estadillo que recibir en ese flujo), así que ocupa
        // el ancho entero (`.kiosk-cards-una`).
        <div className={esOrganizar ? 'kiosk-cards' : 'kiosk-cards kiosk-cards-una'}>
          <BotonToque
            className="kiosk-card kiosk-card-carpeta"
            tactil={tactil}
            onActivar={onPickCarpeta}
            disabled={busy}
            aria-label="Elegir carpeta"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            <span className="kiosk-card-texto">
              <span className="kiosk-card-label">Elegir carpeta</span>
              {/* Estado corto ya existente: destino derivado si ya hay
                  carpeta (organizar), o la propia carpeta/«Sin carpeta». */}
              <span className="kiosk-card-estado">
                {esOrganizar && carpeta ? destino : (carpeta || 'Sin carpeta')}
              </span>
            </span>
          </BotonToque>

          {esOrganizar && (
            <BotonToque
              className="kiosk-card kiosk-card-estadillo"
              tactil={tactil}
              onActivar={() => { setRetomarEspera(false); setEsperandoEstadillo(true) }}
              disabled={busy}
              aria-label="Recibir estadillo"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M5 12.55a11 11 0 0 1 14.08 0" />
                <path d="M1.42 9a16 16 0 0 1 21.16 0" />
                <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
                <line x1="12" y1="20" x2="12.01" y2="20" />
              </svg>
              <span className="kiosk-card-texto">
                <span className="kiosk-card-label">Recibir estadillo</span>
                {/* Indicador de estadillo en la carpeta elegida (pedido de
                    Rodrigo): «si aplica» — sin carpeta no hay nada que decir
                    todavía. */}
                {carpeta && estadilloEnCarpeta && (
                  <span
                    className={
                      'field-hint kiosk-card-estado kiosk-estadillo-en-carpeta ' +
                      (estadilloEnCarpeta.buscando
                        ? ''
                        : estadilloEnCarpeta.aviso
                          ? 'hint-warn'
                          : estadilloEnCarpeta.encontrado ? 'hint-ok' : 'hint-warn')
                    }
                    data-testid="kiosk-estadillo-en-carpeta"
                  >
                    {estadilloEnCarpeta.buscando
                      ? 'Buscando estadillo…'
                      // Aviso de `aviso_estadillos_misma_carpeta` (2+ ficheros en la
                      // MISMA carpeta): manda sobre el "encontrado" normal, va a
                      // abortar el run más tarde si no se separan.
                      : estadilloEnCarpeta.aviso
                        ? estadilloEnCarpeta.aviso
                        : estadilloEnCarpeta.encontrado
                          ? `Estadillo encontrado: ${estadilloEnCarpeta.nombre}`
                          : 'Sin estadillo en la carpeta'}
                    {estadilloEnCarpeta.recibidoLan && (
                      <span className="kiosk-estadillo-lan-pendiente">
                        {' '}· Estadillo recibido por red: se añadirá al organizar
                      </span>
                    )}
                  </span>
                )}
              </span>
            </BotonToque>
          )}
        </div>
      )}

      {/* Candidatos de la carpeta PADRE (`estadilloEnCarpeta.candidatosPadre`,
          App.jsx): NUNCA se añaden solos (decisión de Rodrigo, caso Marcos),
          un aviso por candidato con su ruta completa y el botón para
          confirmarlo explícitamente, mismo flujo que `PasoEstadillo.jsx` en
          escritorio. `onUsarCandidatoPadre` quita el candidato usado de la
          lista tras confirmarlo, así el aviso desaparece solo. */}
      {esOrganizar && carpeta && Array.isArray(estadilloEnCarpeta?.candidatosPadre) &&
        estadilloEnCarpeta.candidatosPadre.length > 0 && (
        <div className="kiosk-candidatos-padre">
          {estadilloEnCarpeta.candidatosPadre.map((ruta) => (
            <div
              key={ruta}
              className="field-hint hint-warn kiosk-candidato-padre"
              role="alert"
              data-testid="kiosk-candidato-padre"
            >
              <span>Estadillo encontrado FUERA de la carpeta seleccionada: {ruta}</span>
              <BotonToque
                className="btn-ghost kiosk-btn-usar-candidato"
                tactil={tactil}
                disabled={busy}
                onActivar={() => onUsarCandidatoPadre?.(ruta)}
                data-testid="kiosk-usar-candidato-padre"
              >
                Usar este estadillo
              </BotonToque>
            </div>
          ))}
        </div>
      )}

      {esOrganizar && (
        <div className="kiosk-estadillo">
          {esperandoEstadillo ? (
            <EsperaEstadillo
              carpeta={carpeta}
              inspeccion={null}
              disabled={busy}
              retomar={retomarEspera}
              onRecibido={(rutas, resumen) => { setEsperandoEstadillo(false); setRetomarEspera(false); onEstadillo(rutas, resumen) }}
              onCancelar={() => { setEsperandoEstadillo(false); setRetomarEspera(false) }}
            />
          ) : (
            <EstadilloField
              value={estadillo}
              onChange={onEstadillo}
              disabled={busy}
              tactil={tactil}
              infoRecibido={estadilloInfo}
            />
          )}
        </div>
      )}

      {barraProgreso}

      {/* Mientras se espera el estadillo del portátil, "Organizar" no pinta
          nada (no hay nada que organizar todavía) y en 480x320 competía por
          el mismo hueco que el panel de espera: se oculta entero mientras
          `esperandoEstadillo` está activo. Sus propias acciones
          (Cancelar/Empezar) siguen visibles en `.espera-pie`, ver
          EsperaEstadillo.jsx. */}
      {(!esOrganizar || carpeta) && !esperandoEstadillo && (
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
      )}

      {/* Reproduccion de una accion remota "carpeta" (`accionRemota`, ver
          arriba): el backend YA fijo la carpeta (`kioskCarpeta` llega por
          `atom:control_carpeta`, sincronizada en `App.jsx`), este picker
          solo la ENSEÑA recorriendo sus segmentos sola, ignorando toques
          reales — nunca llama al backend para fijar. */}
      {carpetaRepro !== null && (
        <FolderPicker
          mode="folder"
          reproduccion
          rutaObjetivo={carpetaRepro}
          onPick={() => {}}
          onCancel={() => {}}
          onFinReproduccion={terminarCarpetaRepro}
        />
      )}
    </div>
  )
}
