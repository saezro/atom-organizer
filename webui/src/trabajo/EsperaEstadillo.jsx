import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../bridge'

const INTERVALO_POLL_MS = 2000
const INTERVALO_RELOJ_MS = 1000
// El backend puede no mandar caducidad (`caduca_en`/`segundos_restantes`):
// 10 min es el plazo por defecto que se cuenta en el propio cliente, igual
// que documenta el pedido.
const ESPERA_LOCAL_MS = 10 * 60 * 1000
// Etiqueta de cada IP según su interfaz: `169.254.77.1` es la IP fija del
// modo «cable directo» (link-local, sin router de por medio) y manda sobre
// cualquier otra regla; `eth*` es un cable normal contra un router; `wlan*`
// es wifi — tanto conectado a una red ajena como emitiendo el hotspot de
// configuración (`nmcli device wifi hotspot ifname wlan0`, app_webview.py
// ~L2876): la propia Pi NO usa una interfaz `ap0` separada, así que no hay
// forma de distinguir «Wifi del Organizer» de un wifi normal por el nombre
// de interfaz — se etiquetan igual, «Wifi».
function etiquetaInterfaz(interfaz, ip) {
  if (ip === '169.254.77.1') return 'Cable directo'
  if (/^eth/i.test(interfaz || '')) return 'Cable'
  if (/^(wlan|ap)/i.test(interfaz || '')) return 'Wifi'
  return null
}

// Redes mesh (Tailscale, Netbird): no son la LAN local en la que está el
// portátil, solo confunden al operador con IPs que no puede teclear en la
// misma red. Se descartan tanto por nombre de interfaz (`tailscale*`,
// `wt*`) como por rango de IP (100.64.0.0/10, CGNAT que usa Tailscale).
function esRedMesh(interfaz, ip) {
  if (/^(tailscale|wt)/i.test(interfaz || '')) return true
  const m = /^(\d{1,3})\.(\d{1,3})\./.exec(ip || '')
  if (m) {
    const a = Number(m[1])
    const b = Number(m[2])
    if (a === 100 && b >= 64 && b <= 127) return true
  }
  return false
}

function formatearHora(iso) {
  if (!iso) return null
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return null
  }
}

// Hora con segundos (HH:MM:SS), para el registro de actividad: a diferencia
// del rango de fotos, aquí sí importa el segundo exacto de cada evento.
function formatearHoraSegundos(iso) {
  if (!iso) return null
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return null
  }
}

// "HH:MM" o "HH:MM:SS" -> minutos desde medianoche. `null` si no se puede
// interpretar (hora vacía, formato raro): nunca se inventa un número.
function minutosDesdeHHMM(hhmm) {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || '').trim())
  if (!m) return null
  return Number(m[1]) * 60 + Number(m[2])
}

// Duración de un vuelo en minutos a partir de sus horas "HH:MM" de inicio y
// fin. Si cruza medianoche (marcado por el backend, o el fin sale antes que
// el inicio) se le suma un día. `null` si falta cualquiera de las dos horas.
function duracionVueloMin(inicio, fin, cruzaMedianoche) {
  const a = minutosDesdeHHMM(inicio)
  const b = minutosDesdeHHMM(fin)
  if (a == null || b == null) return null
  let d = b - a
  if (cruzaMedianoche || d < 0) d += 24 * 60
  return d
}

// `12345 ms` -> `"mm:ss"`. Nunca negativo: por debajo de 0 se enseña 00:00
// (el estado pasa a "caducado" antes de que importe).
function mmss(ms) {
  const total = Math.max(0, Math.round(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// Check verde de "recibido".
function IconoCheck() {
  return (
    <svg viewBox="0 0 24 24" width="2.5rem" height="2.5rem" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" />
      <path d="M7.5 12.5l3 3 6-6.5" />
    </svg>
  )
}

// Aviso de "caducado"/"rechazado".
function IconoAlerta() {
  return (
    <svg viewBox="0 0 24 24" width="2.5rem" height="2.5rem" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2.5L23 21H1z" />
      <path d="M12 9.5v5" />
      <circle cx="12" cy="17.5" r="0.5" fill="currentColor" />
    </svg>
  )
}

// Spinner de "recibiendo" (llegando datos, aún sin validar).
function IconoSpinner() {
  return (
    <svg className="espera-spinner" viewBox="0 0 24 24" width="2.5rem" height="2.5rem" fill="none"
         stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <path d="M12 3a9 9 0 1 1-9 9" />
    </svg>
  )
}

// Enchufe/portátil conectado (sin datos todavía).
function IconoEnlace() {
  return (
    <svg viewBox="0 0 24 24" width="2.5rem" height="2.5rem" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 12a4 4 0 0 1 4-4h1" />
      <path d="M16 12a4 4 0 0 1-4 4h-1" />
      <path d="M14 5l2 2-2 2" />
      <path d="M10 15l-2 2 2 2" />
    </svg>
  )
}

// Deriva la fase visible a partir de `estado`. El backend manda ya `fase`
// ('inactivo'|'esperando'|'conectado'|'recibiendo'|'recibido_ok'|'rechazado'
// |'caducado'), pero mientras no lo haga (o en respuestas viejas) se deriva
// del resto de campos, que sí existían antes: eso es lo único que garantiza
// que la pantalla nunca se quede sin decir qué está pasando.
function derivarFase(estado, caducadoLocal) {
  if (!estado) return 'esperando'
  if (typeof estado.fase === 'string' && estado.fase) return estado.fase
  if (estado.recibido) return 'recibido_ok'
  if (estado.caducado === true || caducadoLocal) return 'caducado'
  return 'esperando'
}

// Pantalla de espera del estadillo enviado desde el portátil (misma red
// local, `organizer.local`): alternativa a elegirlo a mano en `EstadilloField`
// mientras `PasoEstadillo` está en modo «Recibir del portátil». Solo se
// encarga de esperar y avisar con `onRecibido(rutas)`: de ahí en adelante
// sigue exactamente el mismo camino de validación que un CSV elegido a mano
// (`comprobarEstadillo`, en `PasoEstadillo`).
//
// Siempre dice, grande y a un vistazo, qué está pasando (pedido de Rodrigo:
// pantalla de kiosco vista a distancia): una cabecera de color por `fase` y,
// debajo, un registro con los últimos eventos que manda el backend.
// `retomar`: la espera ya está activa en el backend (p. ej. se retoma tras
// recargar Chromium o desbloquear el PIN con una espera en curso) y no hay
// que reiniciarla — reiniciar (`estadillo_espera_iniciar`) resetea el
// contador de caducidad y sustituye el estado entero (`app_webview.py`
// ~L2259-2261). Con `retomar` el efecto se salta esa llamada y arranca
// directo a hacer poll del estado existente.
export default function EsperaEstadillo({
  carpeta, inspeccion, disabled, retomar = false, onRecibido, onCancelar,
}) {
  const [estado, setEstado] = useState(null) // null mientras arranca
  const [error, setError] = useState(null)
  const [ahora, setAhora] = useState(() => Date.now())
  // Cambia de valor para relanzar el efecto de abajo (reintentar tras
  // caducar): no puede ser el mismo `useEffect` reactivo a `estado`, porque
  // eso lo dispararía la propia respuesta del poll.
  const [intento, setIntento] = useState(0)
  // Evita la doble cancelación: en cuanto se recibe el estadillo se deja de
  // hacer poll y se corta el intervalo, y sin este guard el cleanup del
  // efecto (que corre al desmontar, p. ej. cuando el padre navega tras
  // pulsar «OK, seguir») llamaría a `estadillo_espera_cancelar` sobre una
  // espera que ya se resolvió.
  const resueltoRef = useRef(false)
  // Rutas del último "recibido": el resumen se queda en pantalla (modal) hasta
  // que el operador pulsa «OK, seguir» a mano — nada de avanzar solo.
  const rutasRef = useRef([])
  // Ancla del contador local: se fija al arrancar (o reintentar) y, si el
  // backend informa `segundos_restantes` en vez de `caduca_en`, se realinea
  // con cada respuesta para no acumular deriva de los propios `setTimeout`.
  const finLocalRef = useRef(null)

  useEffect(() => {
    let vivo = true
    let idPoll = null
    resueltoRef.current = false
    finLocalRef.current = Date.now() + ESPERA_LOCAL_MS
    setEstado(null)
    setError(null)
    ;(async () => {
      if (!retomar) {
        try {
          await api.estadilloEsperaIniciar(carpeta, inspeccion)
        } catch (e) {
          if (vivo) setError(String(e))
          return
        }
      }
      if (!vivo) return
      const consultar = async () => {
        try {
          const r = await api.estadilloEsperaEstado()
          if (!vivo) return
          setEstado(r)
          if (typeof r?.segundos_restantes === 'number' && !r?.caduca_en) {
            finLocalRef.current = Date.now() + r.segundos_restantes * 1000
          }
          if (r?.recibido && !resueltoRef.current) {
            // Solo se marca resuelto y se corta el poll: el aviso al padre
            // (`onRecibido`) NUNCA es automático — se queda en pantalla como
            // modal hasta que el operador pulse «OK, seguir» (`empezarAhora`).
            resueltoRef.current = true
            rutasRef.current = r.rutas || []
            if (idPoll) {
              clearInterval(idPoll)
              idPoll = null
            }
          }
        } catch (e) {
          if (vivo) setError(String(e))
        }
      }
      consultar()
      idPoll = setInterval(consultar, INTERVALO_POLL_MS)
    })()
    return () => {
      vivo = false
      if (idPoll) clearInterval(idPoll)
      if (!resueltoRef.current) {
        // Fire-and-forget: si el bridge falla al cancelar no hay nada más
        // que hacer desde una pantalla que ya se está desmontando.
        api.estadilloEsperaCancelar().catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intento])

  // Reloj de la cuenta atrás: independiente del poll (cada 2 s bastaría para
  // el dato, pero el mm:ss se vería a saltos).
  useEffect(() => {
    const id = setInterval(() => setAhora(Date.now()), INTERVALO_RELOJ_MS)
    return () => clearInterval(id)
  }, [])

  function cancelar() {
    resueltoRef.current = true
    api.estadilloEsperaCancelar().catch(() => {})
    onCancelar()
  }

  function reintentar() {
    setIntento((n) => n + 1)
  }

  // Único disparador de `onRecibido`: el botón «OK, seguir» del modal de
  // resumen. Nada avanza solo. Segundo argumento: el resumen rico del
  // estadillo recibido (planta/fechas/pilotos/drones/vuelos/tiempo total),
  // para que quien reciba las rutas pueda seguir mostrándolo después de que
  // este modal se desmonte (pedido de Rodrigo: la tarjeta de `EstadilloField`
  // tras pulsar «OK, seguir»). `undefined` en cualquier campo que el backend
  // no haya mandado — nunca se inventa un dato.
  function empezarAhora() {
    onRecibido(rutasRef.current, {
      origen: 'recibido',
      planta,
      fechas: fechasResumen,
      pilotos,
      drones,
      numVuelos,
      vuelos: vuelosResumen,
      tiempoTotalMin,
      avisos: avisosValidacion,
    })
  }

  const msRestantes = estado?.caduca_en
    ? new Date(estado.caduca_en).getTime() - ahora
    : finLocalRef.current != null
      ? finLocalRef.current - ahora
      : null
  const caducadoLocal = msRestantes != null && msRestantes <= 0
  const fase = derivarFase(estado, caducadoLocal)

  const fotos = estado?.fotos
  const rango =
    fotos && !fotos.calculando && fotos.primera && fotos.ultima
      ? `${formatearHora(fotos.primera)}–${formatearHora(fotos.ultima)}`
      : null
  const etiquetaInspeccion = inspeccion?.etiqueta || inspeccion?.prefijo || null
  // La Pi redirige 80→8765 (`atom_core/red_info.py`, `PUERTO_PUBLICO`): la
  // dirección que se sugiere es la del puerto público, SIN puerto salvo que
  // el backend informe uno distinto del 80 (o de ninguno).
  const puertoRed = estado?.red?.puerto
  const sufijoPuerto = puertoRed && puertoRed !== 80 ? `:${puertoRed}` : ''
  const direccionesIp = (estado?.red?.ips || [])
    .filter((d) => d.ip && !esRedMesh(d.interfaz, d.ip))
    .map((d) => ({ ip: d.ip, etiqueta: etiquetaInterfaz(d.interfaz, d.ip) }))

  // Resumen del estadillo ya recibido (piloto/dron/nº vuelos): el backend
  // (`_estadillo_recibir`/`estadillo_espera_estado` en `app_webview.py`)
  // manda esto en `estado.resumen` con claves `pilotos`, `drones` y
  // `n_vuelos` (no `info`, que era de `estadillosDetectar`/
  // `read_estadillo_info`). Se mantiene `info` como fallback por si algún
  // llamador antiguo sigue mandándolo. Fail-open: si no llega nada de esto,
  // simplemente no se enseña; el check y el aviso siguen valiendo por sí
  // solos.
  const resumen = estado?.resumen || null
  const info = estado?.info || null
  const pilotos = (resumen?.pilotos || info?.pilotos || []).filter(Boolean)
  const drones = (resumen?.drones || info?.drones || []).filter(Boolean)
  const numVuelos =
    typeof resumen?.n_vuelos === 'number'
      ? resumen.n_vuelos
      : typeof info?.num_vuelos === 'number'
        ? info.num_vuelos
        : undefined
  const planta = resumen?.planta || info?.trabajo || null
  const fechaResumen = resumen?.fecha || info?.fecha || null
  // Varias fechas (campaña de varios días): el backend las manda en
  // `resumen.fechas`/`info.fechas` (`read_estadillo_info`, `atom_core/
  // estadillo.py`). Sin eso, se cae a la única fecha de arriba.
  const fechasResumen = (resumen?.fechas?.length ? resumen.fechas : info?.fechas?.length ? info.fechas : fechaResumen ? [fechaResumen] : [])
  // Lista de vuelos con hora de inicio/fin (`resumen.vuelos`/`info.vuelos`,
  // misma forma que `read_estadillo_info`: `{pb, vuelo, fecha, inicio,
  // final, cruza_medianoche}`). Fail-open: sin esto (backend viejo) la lista
  // sale vacía y no se inventa ninguna hora ni duración.
  const vuelosResumen = (resumen?.vuelos || info?.vuelos || []).map((v, i) => {
    const duracionMin = duracionVueloMin(v.inicio, v.final, v.cruza_medianoche)
    return {
      n: v.vuelo || v.pb || String(i + 1),
      inicio: v.inicio || null,
      fin: v.final || null,
      duracionMin,
    }
  })
  const tiempoTotalMin = vuelosResumen.some((v) => typeof v.duracionMin === 'number')
    ? vuelosResumen.reduce((acc, v) => acc + (typeof v.duracionMin === 'number' ? v.duracionMin : 0), 0)
    : undefined
  const errores = estado?.errores || []
  const eventos = estado?.eventos || []
  const ultimoContacto = estado?.ultimo_contacto || null

  // `validacion` (backend, opcional): compara cada vuelo del estadillo con
  // las fotos que hay de verdad en la carpeta elegida. Fail-open total: si el
  // backend todavía no la manda (o falla), la sección entera desaparece sin
  // romper nada — el check verde y el resumen de arriba siguen valiendo por
  // sí solos, como antes de que existiera.
  const validacion = estado?.validacion || null
  const vuelosValidacion = Array.isArray(validacion?.vuelos) ? validacion.vuelos : []
  const avisosValidacion = Array.isArray(validacion?.avisos) ? validacion.avisos : []
  const fotosFuera = typeof validacion?.fotos_fuera === 'number' ? validacion.fotos_fuera : 0
  const nAvisos =
    avisosValidacion.length ||
    vuelosValidacion.filter((v) => v?.estado && v.estado !== 'ok').length ||
    (fotosFuera > 0 ? 1 : 0)

  // Motivo del rechazo: el backend puede mandarlo directo (`estado.motivo`,
  // o `{ok:false, codigo, motivo}` que ya llega aplanado en `estado`), o solo
  // queda registrado en el log de eventos (`rechazado`, `detalle` con
  // "<motivo> (<código>)"). Sin ninguno de los dos, se deja el texto genérico
  // de siempre — nunca "rechazado" a secas sin decir nada si hay un motivo
  // disponible en algún sitio.
  const motivoRechazo =
    estado?.motivo ||
    (errores.length > 0 ? null : eventos.slice().reverse().find((ev) => ev.tipo === 'rechazado')?.detalle) ||
    null
  // El backend expone `carpeta_seleccionada` (y `aviso`) mientras la espera
  // sigue viva (`esperando`): antes de recibirse nada (o mientras se sigue
  // esperando reenvío) hace falta una carpeta donde guardar lo que llegue.
  // En `recibido_ok` ya no se pide: el operador solo tiene que pulsar
  // «Empezar». El selector de carpeta YA está justo encima de este panel
  // (`.kiosk-carpeta`/`EstadilloField` según la pantalla), así que aquí solo
  // se avisa en texto — sin botón duplicado (pedido de Rodrigo). Elegirla
  // arriba ya se propaga sola al backend: `KioskScreen` llama a
  // `estadilloEsperaCarpeta` en cuanto cambia `carpeta` mientras se espera.
  const faltaCarpeta = Boolean(estado?.esperando && !estado?.carpeta_seleccionada && fase !== 'recibido_ok')

  // «Estadillo recibido»: SIEMPRE modal encima de la pantalla actual (portal a
  // `document.body`, pedido de Rodrigo tras verse el resumen cortado en el
  // kiosco 480x320). No avanza solo — `empezarAhora` (el botón «OK, seguir»)
  // es el único disparador de `onRecibido`, ver el efecto de arriba. Cabecera
  // fija (icono + título + semáforo) y pie fijo (el botón) quedan FUERA del
  // scroll; solo el cuerpo (detalle/vuelos/avisos) scrollea si no cabe.
  if (fase === 'recibido_ok') {
    return createPortal(
      <div className="espera-modal-overlay" role="presentation">
        <div
          className="espera-estadillo espera-estadillo-recibido_ok espera-modal-panel"
          data-testid="espera-estadillo"
          data-fase="recibido_ok"
          role="dialog"
          aria-modal="true"
          aria-label="Resumen del estadillo recibido"
        >
          <div className="espera-modal-cabecera">
            <IconoCheck />
            <span className="espera-titulo">
              Estadillo recibido{typeof numVuelos === 'number' ? ` · ${numVuelos} vuelo${numVuelos === 1 ? '' : 's'}` : ''}
            </span>
            {/* Semáforo de validación (fotos de la carpeta vs vuelos del
                estadillo): solo aparece si el backend manda `validacion`.
                «OK, seguir» sigue disponible aunque haya avisos — esto solo
                informa, nunca bloquea. */}
            {validacion && (
              <span
                className={
                  'espera-semaforo ' +
                  (validacion.pendiente
                    ? 'espera-semaforo-pendiente'
                    : validacion.ok
                      ? 'espera-semaforo-ok'
                      : 'espera-semaforo-avisos')
                }
                data-testid="espera-semaforo"
              >
                {validacion.pendiente
                  ? 'Comprobando fotos…'
                  : validacion.ok
                    ? 'Todo encaja'
                    : `Revisar: ${nAvisos} aviso${nAvisos === 1 ? '' : 's'}`}
              </span>
            )}
          </div>

          <div className="espera-cuerpo espera-modal-cuerpo">
            {/* El estadillo se recibe siempre, aunque aun no haya carpeta
                elegida (backend: `pendiente_carpeta`/`webserver.py`). Se
                queda asociado a esta espera y se aplica solo al elegir la
                carpeta arriba (`estadilloEsperaCarpeta` +
                `_mover_estadillo_espera_si_toca`). */}
            {!carpeta && (
              <span className="field-hint hint-warn espera-pendiente-carpeta" data-testid="espera-pendiente-carpeta">
                Se aplicará al elegir la carpeta
              </span>
            )}
            {(planta || fechaResumen || carpeta || pilotos.length > 0 || drones.length > 0) && (
              <dl className="espera-recibido-detalle">
                {planta && (
                  <>
                    <dt>Planta</dt>
                    <dd>{planta}</dd>
                  </>
                )}
                {fechaResumen && (
                  <>
                    <dt>Fecha</dt>
                    <dd>{fechaResumen}</dd>
                  </>
                )}
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
                {carpeta && (
                  <>
                    <dt>Carpeta</dt>
                    <dd className="espera-recibido-carpeta">{carpeta}</dd>
                  </>
                )}
              </dl>
            )}

            {/* Lista compacta por vuelo (fotos vistas vs esperadas), fotos
                fuera de vuelo y avisos — todo dentro de `.espera-cuerpo`, que
                ya scrollea si no cabe (ver App.css). */}
            {vuelosValidacion.length > 0 && (
              <ul className="espera-validacion-vuelos" data-testid="espera-validacion-vuelos">
                {vuelosValidacion.map((v, i) => {
                  const nombre = v.nombre || v.id || `Vuelo ${i + 1}`
                  const ok = !v.estado || v.estado === 'ok'
                  return (
                    <li
                      key={v.id || v.nombre || i}
                      className={'espera-validacion-vuelo ' + (ok ? 'espera-validacion-vuelo-ok' : 'espera-validacion-vuelo-warn')}
                    >
                      <span>{nombre}</span>
                      <span className="espera-mono">
                        {typeof v.fotos === 'number' && typeof v.esperado === 'number'
                          ? `${v.fotos}/${v.esperado} fotos`
                          : ok
                            ? 'OK'
                            : v.estado}
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
            {fotosFuera > 0 && (
              <span className="field-hint hint-warn">
                {fotosFuera} foto{fotosFuera === 1 ? '' : 's'} fuera de vuelo
              </span>
            )}
            {avisosValidacion.length > 0 && (
              <div className="field-hint hint-warn espera-errores">
                {avisosValidacion.map((a, i) => (
                  <div key={i}>{a}</div>
                ))}
              </div>
            )}
          </div>

          <div className="espera-pie espera-modal-pie">
            <button type="button" className="btn-run btn-espera-empezar" disabled={disabled} onClick={empezarAhora}>
              OK, seguir
            </button>
          </div>
        </div>
      </div>,
      document.body,
    )
  }

  // El cuerpo (icono, título, contador, direcciones, registro…) puede crecer
  // más que el hueco disponible en el kiosco (480x320): va en su propio
  // bloque con scroll interno (`.espera-cuerpo`, ver App.css), mientras que
  // las acciones (`.espera-pie`: Cancelar/Empezar/Volver a esperar) quedan
  // FUERA de ese scroll, siempre visibles al pie del panel — pedido de
  // Rodrigo tras verse la espera cortada y el botón "Organizar" del kiosco
  // compitiendo con ella por el mismo hueco.
  return (
    <div className={`espera-estadillo espera-estadillo-${fase}`} data-testid="espera-estadillo" data-fase={fase}>
      <div className="espera-cuerpo">
        {faltaCarpeta && (
          <span className="field-hint hint-warn espera-carpeta" data-testid="espera-carpeta">
            Elige carpeta arriba
          </span>
        )}

        {fase === 'esperando' && (
          <>
            <div className="espera-radar" aria-hidden="true">
              <span className="espera-radar-anillo" />
              <span className="espera-radar-punto" />
            </div>
            <span className="espera-titulo">Esperando estadillo del portátil</span>
            <span className="espera-mono espera-contador">{mmss(msRestantes ?? ESPERA_LOCAL_MS)}</span>
            <div className="espera-direcciones">
              <span className="field-hint espera-direcciones-label">Para conectar:</span>
              <span className="espera-mono espera-direccion">
                {(() => {
                  const host = estado?.red?.hostname
                  const base =
                    estado?.red?.url ||
                    (host ? `http://${host.toLowerCase().endsWith('.local') ? host : `${host}.local`}` : 'http://organizer.local')
                  return `${base}${sufijoPuerto}`
                })()}
              </span>
              {direccionesIp.map((d) => (
                <span key={d.ip} className="espera-mono espera-direccion">
                  http://{d.ip}
                  {sufijoPuerto}
                  {d.etiqueta ? ` (${d.etiqueta})` : ''}
                </span>
              ))}
            </div>
            {etiquetaInspeccion && <span className="field-hint">{etiquetaInspeccion}</span>}
            {fotos && (
              <span className="field-hint espera-mono espera-fotos">
                {fotos.total ?? 0} foto{fotos.total === 1 ? '' : 's'}
                {fotos.calculando ? ' · calculando…' : rango ? ` · ${rango}` : ''}
              </span>
            )}
            {errores.length > 0 && (
              <div role="alert" className="field-hint hint-warn espera-errores">
                {errores.map((e, i) => (
                  <div key={i}>{e}</div>
                ))}
                <div>El portátil puede reenviarlo; se sigue esperando.</div>
              </div>
            )}
            {error && (
              <span role="alert" className="field-hint hint-warn">
                {error}
              </span>
            )}
          </>
        )}

        {fase === 'conectado' && (
          <>
            <IconoEnlace />
            <span className="espera-titulo">
              Portátil conectado{ultimoContacto?.ip ? ` · ${ultimoContacto.ip}` : ''}
            </span>
            <span className="field-hint">Esperando el estadillo…</span>
          </>
        )}

        {fase === 'recibiendo' && (
          <>
            <IconoSpinner />
            <span className="espera-titulo">Recibiendo estadillo…</span>
            {ultimoContacto?.ip && <span className="field-hint">{ultimoContacto.ip}</span>}
          </>
        )}

        {fase === 'rechazado' && (
          <>
            <IconoAlerta />
            <span className="espera-titulo">Estadillo rechazado</span>
            {motivoRechazo && (
              <span className="field-hint hint-warn espera-motivo" data-testid="espera-motivo">
                {motivoRechazo}
              </span>
            )}
            {errores.length > 0 && (
              <div role="alert" className="field-hint hint-warn espera-errores">
                {errores.map((e, i) => (
                  <div key={i}>{e}</div>
                ))}
              </div>
            )}
            <span className="field-hint">El portátil puede reenviarlo; se sigue esperando.</span>
          </>
        )}

        {fase === 'caducado' && (
          <>
            <IconoAlerta />
            <span className="espera-titulo">Tiempo de espera agotado</span>
          </>
        )}

        {fase === 'inactivo' && <span className="espera-titulo">Sin actividad</span>}

        {eventos.length > 0 && (
          <div className="espera-registro" data-testid="espera-registro">
            <span className="espera-registro-titulo">Actividad</span>
            <ul>
              {eventos
                .slice()
                .reverse()
                .map((ev, i) => (
                  <li key={i} className="espera-registro-linea">
                    <span className="espera-mono">{formatearHoraSegundos(ev.cuando) || '--:--:--'}</span>
                    {ev.ip && <span className="espera-mono">{ev.ip}</span>}
                    <span>{ev.detalle ? `${ev.tipo} · ${ev.detalle}` : ev.tipo}</span>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </div>

      {(fase === 'esperando' || fase === 'conectado' || fase === 'rechazado' || fase === 'inactivo') && (
        <div className="espera-pie">
          <button
            type="button"
            className="btn-ghost btn-espera-cancelar"
            disabled={disabled}
            onClick={cancelar}
            data-control-resaltar="cancelar"
          >
            Cancelar
          </button>
        </div>
      )}

      {fase === 'caducado' && (
        <div className="espera-pie espera-acciones">
          <button type="button" className="btn-run" disabled={disabled} onClick={reintentar}>
            Volver a esperar
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={disabled}
            onClick={cancelar}
            data-control-resaltar="cancelar"
          >
            Cancelar
          </button>
        </div>
      )}
    </div>
  )
}
