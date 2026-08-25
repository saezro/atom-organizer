import { useEffect, useRef, useState } from 'react'
import { api, onCloud } from '../bridge'
import EstadilloField from '../EstadilloField'

// Estadillo → ubicación canónica del bucket: acción propia, no depende de
// haber organizado ni de la carpeta a subir de arriba. Preview obligatorio
// (`estadCheck`) antes de poder subir: el resumen se invalida en cuanto
// cambia la lista de ficheros, para no subir con un resumen que ya no
// corresponde a la selección.
export default function PasoEstadillo({ prefijo, disabled, onEstado }) {
  const [estadRutas, setEstadRutas] = useState([])
  const [estadCheck, setEstadCheck] = useState(null) // null | {ok, error, vuelos_detectados, filas_con_problemas}
  const [estadComprobando, setEstadComprobando] = useState(false)
  // `estadSubiendo` es la única guarda de doble-click: `estadillo_subir` en
  // Python no tiene mutex propio (a diferencia de `cloud_upload`, que sí usa
  // `self._uploading`), así que dos clicks lanzarían dos hilos con eventos
  // `atom:cloud` (`scope: 'estadillo'`) intercalados. Lo pone a `true` el
  // propio `subirEstadillo` antes de llamar al backend —NO el evento `start`,
  // que llega demasiado tarde— y lo baja `done`/`error`, o el retorno
  // `started: false` si la subida ni siquiera arranca.
  const [estadSubiendo, setEstadSubiendo] = useState(false)
  const [estadResult, setEstadResult] = useState(null) // {ok, ...} | {error}
  // El estadillo pasa a ser obligatorio en la subida: `omitirEstadillo` es la
  // única vía para saltárselo (resubida de una jornada cuyo estadillo ya está
  // en el bucket, o cuando de verdad no hay estadillo). `estadPrevio` guarda
  // si el backend ve ya un estadillo subido para la inspección elegida, para
  // auto-marcar el checkbox y cambiar su etiqueta sin que el operador tenga
  // que saberlo de memoria.
  const [omitirEstadillo, setOmitirEstadillo] = useState(false)
  const [estadPrevio, setEstadPrevio] = useState(null) // null | {existe, error}
  // Puente entre el `await` de `subirEstadilloEsperando` y el evento
  // `atom:cloud` (`scope: 'estadillo'`) que trae el resultado real: la llamada
  // a `estadillo_subir` solo devuelve `{started}`, así que la promesa se
  // resuelve/rechaza desde el handler de `onCloud` de más abajo.
  const estadPromesaRef = useRef(null)
  // Guarda el PREFIJO para el que ya se aplicó el auto-marcado de
  // «omitir estadillo» a partir de `estadPrevio`. El auto-marcado solo debe
  // disparar UNA vez por inspección: si gobernara `omitirEstadillo` en un
  // efecto reactivo a `estadRutas`, marcar el checkbox a mano (que vacía
  // `estadRutas` vía `cambiarEstadRutas([])`) redispararía el efecto y lo
  // desharía, dejando el botón SUBIR muerto sin explicación.
  const autoOmitAplicadoRef = useRef(null)
  // Token de carrera para `comprobarEstadillo`: si la selección de ficheros
  // cambia mientras una validación anterior sigue en vuelo, la respuesta
  // tardía de la selección vieja no debe pisar el resultado de la nueva.
  const estadCheckTokenRef = useRef(0)

  function cambiarEstadRutas(next) {
    setEstadRutas(next)
    setEstadCheck(null)
    setEstadResult(null)
  }

  // El estadillo ya no se «comprueba» a mano con un botón: se valida solo en
  // cuanto cambia la selección de ficheros (y se limpia el resultado si se
  // vacía la selección).
  useEffect(() => {
    if (estadRutas.length > 0) {
      comprobarEstadillo()
    } else {
      // Invalida cualquier validación en vuelo: sin esto, una respuesta
      // tardía de la selección anterior podría pisar este `null` con un
      // resumen que ya no corresponde a nada seleccionado.
      estadCheckTokenRef.current += 1
      setEstadCheck(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estadRutas])

  // Detecta si la inspección elegida ya tiene un estadillo subido en el
  // bucket, para auto-marcar «omitir estadillo» en una resubida y cambiar la
  // etiqueta del checkbox. Fail-open: un error de red aquí no debe bloquear
  // ni asustar al operador, como mucho se queda sin la pista.
  useEffect(() => {
    if (!prefijo) {
      setEstadPrevio(null)
      return
    }
    let cancelado = false
    ;(async () => {
      try {
        const r = await api.estadilloExistente(prefijo)
        if (!cancelado) setEstadPrevio(r)
      } catch (e) {
        if (!cancelado) setEstadPrevio({ existe: false, error: String(e) })
      }
    })()
    return () => {
      cancelado = true
    }
  }, [prefijo])

  // Auto-marcado: si el backend confirma que ya hay estadillo subido, se
  // asume resubida y se marca solo. Si no hay estadillo previo, se deja sin
  // marcar (requiere acción explícita, con confirmación, más abajo). Se
  // aplica UNA sola vez por inspección (guardado en `autoOmitAplicadoRef`
  // por `prefijo`), para no pisar la decisión manual del usuario: marcar el
  // checkbox a mano vacía `estadRutas`, y si este efecto reaccionara a ese
  // cambio revertiría lo que el operador acaba de confirmar.
  useEffect(() => {
    if (!estadPrevio) return
    if (autoOmitAplicadoRef.current === prefijo) return
    autoOmitAplicadoRef.current = prefijo
    // Fail-open: un error de red al consultar `estadPrevio` no debe forzar
    // ningún estado, solo se marca como «ya intentado» para no reintentar en
    // bucle en cada render.
    if (estadPrevio.error) return
    setOmitirEstadillo(estadPrevio.existe === true)
  }, [estadPrevio, prefijo])

  // La subida del estadillo comparte canal (`atom:cloud`) con la subida
  // general de carpeta, pero es una acción independiente: sus eventos vienen
  // marcados con `scope: 'estadillo'` y se gestionan aparte para no cruzar
  // sus `start`/`done`/`error` con el panel de progreso de «Subir al
  // bucket» (que vive en otro paso).
  useEffect(
    () =>
      onCloud((d) => {
        if (d.scope !== 'estadillo') return
        switch (d.kind) {
          case 'start':
            setEstadSubiendo(true)
            setEstadResult(null)
            break
          case 'done':
            setEstadSubiendo(false)
            setEstadResult({ ok: true, vuelos: d.vuelos_detectados, ruta_manifest: d.ruta_manifest })
            if (estadPromesaRef.current) {
              estadPromesaRef.current.resolve(d)
              estadPromesaRef.current = null
            }
            break
          case 'error':
            setEstadSubiendo(false)
            setEstadResult({ error: d.error })
            if (estadPromesaRef.current) {
              estadPromesaRef.current.reject(new Error(d.error))
              estadPromesaRef.current = null
            }
            break
          default:
            break
        }
      }),
    [],
  )

  // Preview obligatorio: qué se ha entendido del/de los estadillo(s) elegidos,
  // ANTES de permitir subir nada (síncrono en el backend a propósito).
  async function comprobarEstadillo() {
    // Token de carrera: si `estadRutas` vuelve a cambiar antes de que esta
    // validación termine, la respuesta de esta llamada ya no corresponde a
    // la selección actual y no debe pisar `estadCheck`.
    const token = ++estadCheckTokenRef.current
    setEstadComprobando(true)
    try {
      const r = await api.estadilloValidar(estadRutas)
      if (estadCheckTokenRef.current === token) setEstadCheck(r)
    } catch (e) {
      if (estadCheckTokenRef.current === token) setEstadCheck({ ok: false, error: String(e) })
    } finally {
      if (estadCheckTokenRef.current === token) setEstadComprobando(false)
    }
  }

  // El PRIMER argumento es el PREFIJO de la inspección elegida (`prefijo`),
  // NO `carpeta` (la carpeta local del vuelo a subir) ni ninguna otra ruta de
  // disco: `estadillo_subir(folder, rutas)` pasa ese primer argumento tal
  // cual a `prefijo_desde_carpeta` para construir la ruta canónica dentro del
  // bucket (igual que `cloudUpload`/`cloudPrepare` con `prefijo`). Mandar ahí
  // la carpeta local escribiría el estadillo bajo un nombre de planta
  // equivocado.
  async function subirEstadillo() {
    setEstadResult(null)
    // `true` SÍNCRONO antes del await. El evento `start` tarda en llegar: IPC
    // de ida, chequeo de sesión, validación completa de los ficheros y
    // arranque del hilo, todo antes del primer `_push_cloud`. Esperar a ese
    // evento para deshabilitar el botón deja una ventana de doble-click, y
    // `estadillo_subir` no tiene mutex propio en Python (a diferencia de
    // `cloud_upload`), así que dos clicks arrancarían dos hilos escribiendo
    // los mismos objetos del bucket a la vez.
    setEstadSubiendo(true)
    try {
      const r = await api.estadilloSubir(prefijo, estadRutas)
      if (r && r.started === false) {
        setEstadSubiendo(false)
        setEstadResult({ error: r.reason })
        // Si había una promesa pendiente de `subirEstadilloEsperando`, hay que
        // resolverla también aquí: `estadillo_subir` ni siquiera llegó a
        // arrancar, así que no va a llegar ningún evento `atom:cloud` que la
        // cierre. Sin esto el ref se queda colgado para siempre.
        if (estadPromesaRef.current) {
          estadPromesaRef.current.reject(new Error(r.reason || 'No se pudo iniciar la subida del estadillo.'))
          estadPromesaRef.current = null
        }
      }
    } catch (e) {
      // El IPC puede rechazar (p.ej. el puente se cae a medias). Sin este
      // catch la promesa de `subirEstadilloEsperando` no se resuelve nunca y
      // `estadSubiendo` se queda en `true` para siempre, matando el botón
      // SUBIR (que exige `!estadSubiendo`).
      setEstadSubiendo(false)
      setEstadResult({ error: String(e) })
      if (estadPromesaRef.current) {
        estadPromesaRef.current.reject(e)
        estadPromesaRef.current = null
      }
    }
  }

  // Igual que `subirEstadillo`, pero para uso interno desde `subir()`:
  // devuelve una promesa que no resuelve hasta que llega el resultado REAL
  // (evento `atom:cloud`, `scope: 'estadillo'`, gestionado en el `onCloud` de
  // arriba), para poder esperarlo antes de subir ninguna imagen. Reutiliza
  // `subirEstadillo` tal cual: solo prepara el ref ANTES de llamarla, para no
  // perder la carrera con un evento que llegara antes de que la promesa
  // exista.
  function subirEstadilloEsperando() {
    return new Promise((resolve, reject) => {
      estadPromesaRef.current = { resolve, reject }
      subirEstadillo()
    })
  }

  // Dependencias explícitas a los valores reactivos de los que depende el
  // objeto publicado (y por tanto el propio `subir`, que los captura por
  // closure en el momento en que este efecto se recalcula). SIN esta lista
  // el efecto se relanza tras CUALQUIER render — incluidos los que provoca
  // el propio `onEstado` al guardar el objeto en el estado del padre (nueva
  // identidad de objeto siempre) — y eso encadena un bucle infinito de
  // renders en cuanto `onEstado` es un `setState` real (como en
  // `TrabajoScreen`), no el mock sin efecto de los tests de este fichero.
  useEffect(() => {
    onEstado({
      rutas: estadRutas,
      listo: estadCheck?.ok === true || omitirEstadillo,
      subiendo: estadSubiendo,
      subir: async () => {
        if (omitirEstadillo || estadRutas.length === 0) return
        await subirEstadilloEsperando()
      },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estadRutas, estadCheck, estadSubiendo, omitirEstadillo, prefijo, onEstado])

  return (
    <div className="field">
      <span className="field-label">Estadillo (ubicación canónica del bucket)</span>
      <EstadilloField
        value={estadRutas}
        onChange={cambiarEstadRutas}
        disabled={disabled || estadSubiendo || omitirEstadillo}
      />
      <label className="check">
        <input
          type="checkbox"
          checked={omitirEstadillo}
          disabled={disabled || estadSubiendo}
          onChange={(e) => {
            const marcar = e.target.checked
            if (marcar && estadPrevio?.existe !== true) {
              const ok = window.confirm(
                'No hay ningún estadillo subido para esta inspección. Si continúas, las ' +
                  'imágenes se subirán sin estadillo. ¿Seguro?'
              )
              if (!ok) return
            }
            setOmitirEstadillo(marcar)
            if (marcar) cambiarEstadRutas([])
          }}
        />
        <span>{estadPrevio?.existe ? 'Ya subí el estadillo de esta inspección' : 'Subir sin estadillo'}</span>
      </label>
      {estadComprobando && <span className="field-hint">Comprobando el estadillo…</span>}
      {estadCheck?.ok && (
        <span className="field-hint hint-ok">
          {estadCheck.vuelos_detectados} vuelo{estadCheck.vuelos_detectados === 1 ? '' : 's'} detectado
          {estadCheck.vuelos_detectados === 1 ? '' : 's'}
          {estadCheck.filas_con_problemas > 0
            ? ` · ${estadCheck.filas_con_problemas} fila${estadCheck.filas_con_problemas === 1 ? '' : 's'} con problemas`
            : ''}
        </span>
      )}
      {estadCheck && !estadCheck.ok && (
        <span role="alert" className="field-hint hint-warn">
          {estadCheck.error}
        </span>
      )}
      {estadCheck?.ok && !prefijo && (
        <span className="field-hint hint-warn">
          Elige la inspección de arriba antes de subir el estadillo.
        </span>
      )}
      {estadResult?.error && <span className="field-hint hint-warn">{estadResult.error}</span>}
      {estadResult?.ok && (
        <span className="field-hint hint-ok">
          Estadillo subido a «{prefijo}/» ({estadResult.vuelos} vuelo{estadResult.vuelos === 1 ? '' : 's'}).
        </span>
      )}
    </div>
  )
}
