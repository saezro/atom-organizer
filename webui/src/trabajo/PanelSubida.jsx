import { useEffect, useRef, useState } from 'react'
import { api, onAnalisis, onCloud } from '../bridge'
import { formatBytes, formatDuracion } from '../formato'
import cloudUploadConfirmando from './cloudUploadConfirmando'

// « a las 17:42 » para la última comprobación de sesión. Devuelve cadena vacía
// si no hay fecha, para poder concatenarla sin condicionales en el JSX.
function horaCorta(epochSegundos) {
  if (!epochSegundos) return ''
  const d = new Date(epochSegundos * 1000)
  if (Number.isNaN(d.getTime())) return ''
  return ` a las ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

// Destino nube: cuenta de Google, plan de subida (en hilo, Task 4), progreso
// y resultado. `carpeta`/`prefijo`/`inspeccionId` llegan por props (los
// elige otro paso); el estadillo también vive en otro paso (`PasoEstadillo`)
// y llega aquí ya resuelto (`estadilloListo`/`subirEstadillo`).
export default function PanelSubida({
  carpeta,
  prefijo,
  inspeccionId,
  destino,
  estadilloListo,
  estadilloSubiendo,
  subirEstadillo,
  ready,
  onAntesDeSubir,
  onSubidaOk,
  onCloudStatusChange,
  onOcupadoChange,
  onLoginOk,
}) {
  const [status, setStatus] = useState(null) // {configured, logged_in, email, bucket, help}
  const [sesion, setSesion] = useState(null)
  const [comprobando, setComprobando] = useState(false)
  const [busy, setBusy] = useState(false) // login o preparación en curso
  const [plan, setPlan] = useState(null) // {ok, prefix, files, bytes, existing, error}
  // Avance del listado en hilo del plan (evento `atom:analisis`, `scope:
  // 'plan'`, `kind: 'scan'`). Se resetea a 0 en cuanto llega cualquier evento
  // terminal (cancelled/error/done).
  const [escaneados, setEscaneados] = useState(0)
  const [uploading, setUploading] = useState(false)
  // Última foto del progreso que mandó el backend (kind 'stats'), con bytes,
  // ficheros, velocidad y ETA.
  const [stats, setStats] = useState(null)
  // Cronómetro propio de la UI. NO se usa el `elapsed` del backend para pintar
  // el reloj: si la red se cae del todo no llegan eventos, y un contador
  // congelado justo cuando algo va mal es la peor señal posible. Aquí el
  // tiempo corre siempre y es el resto de cifras lo que deja de moverse.
  const [desde, setDesde] = useState(null) // Date.now() al empezar la subida
  const [ahora, setAhora] = useState(0) // segundos transcurridos
  const [lines, setLines] = useState([])
  const [result, setResult] = useState(null) // {ok, ...} | {error}
  // Organización automática en el destino «nube»: se encadena SOLO tras una
  // subida completada con éxito (evento 'done', d.ok && !d.cancelled). El
  // listener de `onCloud` se suscribe una única vez (deps []), así que
  // `destino`/`inspeccionId` se leen de refs actualizadas en cada render, no
  // de las props cerradas en el primer render.
  const destinoRef = useRef(destino)
  destinoRef.current = destino
  const inspeccionIdRef = useRef(inspeccionId)
  inspeccionIdRef.current = inspeccionId
  // Evita disparar la organización dos veces para la misma subida (p.ej. si
  // `done` llegara duplicado). Se resetea al arrancar una subida nueva.
  const organizarDisparadoRef = useRef(false)
  const [organizando, setOrganizando] = useState(false)
  const [organizarResult, setOrganizarResult] = useState(null) // {ok, operacionId} | {error}
  // Token de subida: se incrementa en cada `case 'start'`. Al resolver
  // `lanzarOrganizar` se compara con el token vigente para descartar una
  // respuesta tardía de una subida anterior que pisaría el estado de una
  // subida nueva ya en marcha.
  const subidaTokenRef = useRef(0)

  async function lanzarOrganizar(id) {
    const token = subidaTokenRef.current
    setOrganizando(true)
    setOrganizarResult(null)
    try {
      const r = await api.cloudOrganizar(id)
      if (subidaTokenRef.current !== token) return
      setOrganizando(false)
      if (r && r.ok) {
        setOrganizarResult({ ok: true, operacionId: r.operacion_id })
      } else {
        setOrganizarResult({ error: (r && r.error) || 'No se pudo lanzar la organización.' })
      }
    } catch (e) {
      if (subidaTokenRef.current !== token) return
      setOrganizando(false)
      setOrganizarResult({ error: String(e.message || e) })
    }
  }

  async function refresh() {
    let s
    try {
      s = await api.cloudStatus()
    } catch (e) {
      s = { ok: false, configured: false, help: String(e) }
    }
    setStatus(s)
    // El kiosco (`App`) tiene su propia copia del estado de nube
    // (`kioskCloudStatus`) porque `PanelSubida` no está montado cuando el
    // kiosco es la vista activa: sin avisar aquí, tras un logout el kiosco se
    // queda con el `estado: 'ok'` anterior y "Subir en crudo" sigue
    // pulsable sin sesión.
    onCloudStatusChange?.(s)
  }

  // Pregunta a Google si el token guardado sigue sirviendo. La respuesta llega
  // por el evento `atom:cloud` (kind 'session'), no por el return.
  async function comprobarSesion() {
    setComprobando(true)
    try {
      const r = await api.cloudVerify()
      if (r && r.started === false) {
        setComprobando(false)
        // Sin sesión guardada no hay nada que comprobar; no es un error.
        if (r.logged_in === false) setSesion(null)
      }
    } catch (e) {
      setComprobando(false)
      setSesion({ ok: false, text: String(e) })
    }
  }

  useEffect(() => {
    if (ready) {
      refresh()
      // Al abrir la pantalla se confirma la sesión de una vez, en vez de que el
      // operador se entere de que caducó cuando ya lleva media subida.
      comprobarSesion()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready])

  // Plan en hilo (Task 4): `analisis_reset()` va SIEMPRE antes de
  // `cloud_prepare_start`: el backend no resetea `_cancel_analisis` dentro de
  // `cloud_prepare_start`, solo lo limpia `analisis_reset()`; sin este orden,
  // tras una cancelación previa el siguiente análisis aborta al instante en
  // silencio.
  useEffect(() => {
    if (!carpeta || !prefijo) return
    setPlan(null)
    setEscaneados(0)
    let vivo = true
    const off = onAnalisis((d) => {
      if (d.scope !== 'plan') return
      if (d.kind === 'scan') setEscaneados(d.done)
      if (d.kind === 'cancelled') { setEscaneados(0); setPlan(null) }
      if (d.kind === 'error') { setEscaneados(0); setPlan({ ok: false, error: d.text }) }
      if (d.kind === 'done') { setEscaneados(0); setPlan(d.data) }
    })
    ;(async () => {
      await api.analisisReset()
      if (vivo) api.cloudPrepareStart(carpeta, prefijo)
    })()
    return () => { vivo = false; off() }
  }, [carpeta, prefijo])

  useEffect(
    () =>
      onCloud((d) => {
        switch (d.kind) {
          case 'login':
            setBusy(false)
            if (d.ok) {
              refresh()
              // El canje del código acaba de funcionar: la sesión está viva sin
              // necesidad de volver a preguntar.
              setSesion({ ok: true, text: 'Sesión válida.', validada_en: Date.now() / 1000 })
              // El catálogo de inspecciones vive en `PasoInspeccion` (otro
              // paso del padre): sin avisar aquí, tras iniciar sesión desde
              // cero la lista se queda vacía hasta que el operario pulsa
              // «Actualizar» a mano (mismo refresco cruzado que hacía el
              // `BucketScreen` original al recibir este evento).
              onLoginOk?.()
            } else setResult({ error: d.text || 'No se pudo iniciar sesión.' })
            break
          case 'session':
            setComprobando(false)
            setSesion({ ok: !!d.ok, text: d.text, validada_en: d.validada_en })
            // Siempre se relee el estado, no solo al fallar: una sesión revocada
            // deja de estar «iniciada» también en el backend (el refresh fallido
            // borra el token), y al revés, la comprobación puede terminar justo
            // después de que el usuario cerrara sesión. En ambos casos la UI
            // seguiría enseñando algo que ya no es verdad.
            refresh()
            break
          case 'start':
            // El evento es la señal de que la subida está de verdad en
            // marcha (no solo el `setUploading(true)` síncrono de `subir()`):
            // así el botón «Cancelar subida» aparece también si el
            // componente se remonta con una subida ya en curso en el backend.
            setUploading(true)
            setLines([`Subiendo ${d.files} ficheros (${formatBytes(d.bytes)}) a ${d.prefix}/`])
            // Nueva subida: se puede volver a lanzar la organización cuando
            // esta termine.
            subidaTokenRef.current += 1
            organizarDisparadoRef.current = false
            setOrganizando(false)
            setOrganizarResult(null)
            break
          case 'stats':
            setStats(d)
            break
          case 'log':
            if (d.text) setLines((l) => [...l, d.text])
            break
          case 'done':
            setUploading(false)
            setResult(d)
            setStats(null)
            if (d.ok && !d.cancelled) {
              onSubidaOk?.(d)
              // Destino «nube»: la subida al bucket es solo el primer paso,
              // hay que encadenar la organización en el servidor. Fail-open:
              // si esto falla, la subida ya terminó bien igualmente.
              if (destinoRef.current === 'nube' && !organizarDisparadoRef.current) {
                organizarDisparadoRef.current = true
                if (inspeccionIdRef.current) {
                  lanzarOrganizar(inspeccionIdRef.current)
                } else {
                  setOrganizarResult({
                    error: 'Hace falta elegir una inspección para poder organizar en la nube.',
                  })
                }
              }
            }
            break
          case 'error':
            setUploading(false)
            setResult({ error: d.text })
            break
          default:
            break
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  )

  async function login() {
    setResult(null)
    setBusy(true)
    const r = await api.cloudLogin()
    if (r && r.started === false) {
      setBusy(false)
      setResult({ error: r.reason })
    }
  }

  async function logout() {
    await api.cloudLogout()
    setPlan(null)
    setSesion(null)
    await refresh()
  }

  async function subir() {
    setResult(null)
    // El estadillo va PRIMERO: si `subirEstadillo` (de `PasoEstadillo`)
    // falla, no se sube ninguna imagen.
    try {
      await subirEstadillo()
    } catch (e) {
      setResult({ error: `No se ha subido el estadillo: ${String(e.message || e)}. No se ha subido ninguna imagen.` })
      return
    }
    await onAntesDeSubir?.()
    setLines([])
    setStats(null)
    setDesde(Date.now())
    setAhora(0)
    setUploading(true)
    const r = await cloudUploadConfirmando(carpeta, prefijo, inspeccionId)
    if (r && r.started === false) {
      setUploading(false)
      setDesde(null)
      setResult({ error: r.reason })
    }
  }

  // El reloj de la subida. Late en la UI mientras `uploading`, con
  // independencia de que lleguen o no eventos del backend.
  useEffect(() => {
    if (!uploading || !desde) return undefined
    const id = setInterval(() => setAhora((Date.now() - desde) / 1000), 1000)
    return () => clearInterval(id)
  }, [uploading, desde])

  const logged = !!status?.logged_in
  const ocupado = Boolean(busy || uploading || estadilloSubiendo || organizando)
  const puedeSubir = ready && logged && !!prefijo && plan?.ok && !ocupado && estadilloListo

  // El original (`BucketScreen`) usaba este mismo `ocupado` para deshabilitar
  // TODO el formulario (carpeta, inspección, estadillo), no solo los
  // controles de este panel: aquí se avisa al padre (`TrabajoScreen`), que es
  // quien compone esos otros pasos. Se reporta desde un efecto con
  // dependencias explícitas, nunca durante el render (mismo motivo que
  // `onEstado` en `PasoEstadillo`: un `onOcupadoChange` que fuera un
  // `setState` directo en el cuerpo del componente encadenaría un bucle de
  // renders).
  useEffect(() => {
    onOcupadoChange?.(ocupado)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ocupado, onOcupadoChange])

  // Al desmontar (el operario cambia de destino en mitad de una subida) hay
  // que soltar el candado: si no, el padre se queda con `ocupado` en true
  // para siempre y los pasos nunca vuelven a habilitarse. Efecto aparte del
  // de arriba, para que solo corra en el desmontaje.
  useEffect(() => {
    return () => onOcupadoChange?.(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Sin `<div className="card">`/`<h2>` propios a propósito: este panel es
  // un tramo del flujo de subida (Task 11 lo compone junto a los demás
  // pasos dentro de una única card), igual que ya hace `PasoEstadillo`.
  return (
    <>
      <div className="field">
        <span className="field-label">Cuenta de Google</span>
        {logged ? (
          <>
            <div className="field-row">
              <input className="glass-input" type="text" value={status.email || 'sesión iniciada'} readOnly />
              {/* Una sesión caducada no se arregla cerrándola: se vuelve a
                  entrar. El botón principal cambia según el estado real. */}
              {sesion && !sesion.ok ? (
                <button type="button" className="btn-ghost" onClick={login} disabled={!ready || busy}>
                  {busy ? 'Esperando…' : 'Volver a iniciar sesión'}
                </button>
              ) : null}
              <button type="button" className="btn-ghost" onClick={logout} disabled={ocupado}>
                Cerrar sesión
              </button>
            </div>
            <span className={`field-hint ${sesion ? (sesion.ok ? 'hint-ok' : 'hint-warn') : ''}`}>
              {comprobando || !sesion
                ? 'Comprobando que la sesión sigue activa…'
                : sesion.ok
                  ? `Sesión activa y comprobada${horaCorta(sesion.validada_en)}.`
                  : sesion.text || 'La sesión ya no es válida. Vuelve a iniciar sesión.'}
              {!comprobando ? (
                <>
                  {' '}
                  <button type="button" className="link-inline" onClick={comprobarSesion}>
                    Comprobar de nuevo
                  </button>
                </>
              ) : null}
            </span>
          </>
        ) : (
          <>
            <div className="field-row">
              <input
                className="glass-input"
                type="text"
                value="Sin iniciar sesión"
                readOnly
              />
              <button type="button" className="btn-ghost" onClick={login} disabled={!ready || busy}>
                {busy ? 'Esperando…' : 'Iniciar sesión'}
              </button>
            </div>
            {/* Por qué no hay sesión, cuando el motivo no es «nunca entraste»:
                un perfil copiado de otro equipo deja el almacén ilegible y sin
                esto el operador solo vería un «sin iniciar sesión» inexplicable. */}
            {status?.aviso ? (
              <span className="field-hint hint-warn">{status.aviso}</span>
            ) : null}
          </>
        )}
        <span className="field-hint">
          Se abre el navegador para identificarte con tu cuenta de Aerotools. Los datos van
          al bucket «{status?.bucket || 'datos_para_organizar'}»; quién puede subir lo decide
          el permiso de la cuenta, no la aplicación.
          {destino === 'nube' ? ' En cuanto la subida termine, ATOM organizará la inspección automáticamente.' : ''}
        </span>
      </div>

      {escaneados > 0 && (
        <span className="field-hint">
          Analizando la carpeta… {escaneados} imágenes
          {' '}<button type="button" className="btn-ghost" onClick={() => api.analisisCancel()}>Cancelar</button>
        </span>
      )}

      {plan && plan.ok && (
        <span className="field-hint hint-ok">
          {plan.files} ficheros · {formatBytes(plan.bytes)} → {plan.prefix}/
        </span>
      )}
      {plan && !plan.ok && <span className="field-hint hint-warn">{plan.error}</span>}

      {/* Lo que de verdad se va a subir. Ya no se pide confirmar nada: lo que
          está en el destino se reconoce y se descarta, así que volver a lanzar
          la misma carpeta es seguro. */}
      {plan?.ok && plan.pendientes != null && plan.ya_subidos > 0 && (
        <span className={`field-hint ${plan.pendientes ? '' : 'hint-ok'}`}>
          {plan.pendientes === 0
            ? `Esta carpeta ya está subida entera en «${plan.prefix}/». No hay nada que hacer.`
            : `En «${plan.prefix}/» ya están ${plan.ya_subidos} de estos ${plan.files} ficheros. ` +
              `Se subirán solo los ${plan.pendientes} que faltan (${formatBytes(plan.bytes_pendientes)}).`}
        </span>
      )}

      {/* Panel de progreso: cuánto lleva, cuánto queda y a qué velocidad. El
          reloj corre aunque el backend deje de mandar datos — es la diferencia
          entre «va lento» y «se ha colgado». */}
      {uploading && (
        <div className="subida-panel">
          <div
            className="subida-barra"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={
              stats?.bytes_total ? Math.round((stats.bytes_done / stats.bytes_total) * 100) : 0
            }
          >
            <span
              className="subida-relleno"
              style={{
                width: stats?.bytes_total
                  ? `${Math.min(100, (stats.bytes_done / stats.bytes_total) * 100).toFixed(1)}%`
                  : '0%',
              }}
            />
          </div>
          <div className="subida-cifras">
            <span>
              <strong>{formatDuracion(ahora)}</strong> transcurridos
            </span>
            <span>
              {stats?.eta != null ? `quedan ~${formatDuracion(stats.eta)}` : 'calculando…'}
            </span>
            <span>
              {stats
                ? `${stats.files_done}/${stats.files_total} ficheros · ${formatBytes(stats.bytes_done)} de ${formatBytes(stats.bytes_total)}`
                : 'preparando…'}
            </span>
            <span>{stats?.mbps != null ? `${stats.mbps.toFixed(0)} Mbps` : ''}</span>
            {stats?.retries > 0 && (
              <span className="hint-warn">
                {stats.retries} reintento{stats.retries === 1 ? '' : 's'} por cortes de red
              </span>
            )}
          </div>
        </div>
      )}

      {lines.length > 0 && (
        <ul className="config-list">
          {lines.slice(-6).map((l, i) => (
            <li key={i} className="config-item">
              <span className="config-model">{l}</span>
            </li>
          ))}
        </ul>
      )}

      {result && result.error && <span className="field-hint hint-warn">{result.error}</span>}
      {result && !result.error && (
        <span className={`field-hint ${result.ok ? 'hint-ok' : 'hint-warn'}`}>
          {result.cancelled
            ? `Subida cancelada tras ${formatDuracion(result.elapsed)}. ${result.uploaded} ficheros subidos; al volver a lanzarla continúa donde se quedó.`
            : result.ok
              ? `Subida completa en ${formatDuracion(result.elapsed)}: ${result.uploaded} ficheros ` +
                `(${formatBytes(result.bytes)}) a ${result.mbps} Mbps.` +
                (result.skipped ? ` ${result.skipped} ya estaban subidos.` : '') +
                (result.retries
                  ? ` Hubo ${result.retries} reintento${result.retries === 1 ? '' : 's'} por cortes de red.`
                  : '')
              : `Terminó tras ${formatDuracion(result.elapsed)} con ${result.failed_total} fallo(s): ${(
                  result.failed || []
                )
                  .map((f) => `${f.objeto} (${f.error})`)
                  .join('; ')}. Vuelve a lanzarla: sólo reintenta lo que falta.`}
          {result.log ? ` Detalle en ${result.log}` : ''}
        </span>
      )}

      {/* Segundo paso, solo en destino «nube»: la subida al bucket ya
          terminó, ahora se encadena la organización en el servidor. */}
      {destino === 'nube' && organizando && (
        <span className="field-hint">Lanzando la organización en la nube…</span>
      )}
      {destino === 'nube' && organizarResult && organizarResult.ok && (
        <span className="field-hint hint-ok">
          ATOM está organizando la inspección.
          {organizarResult.operacionId ? ` (operación ${organizarResult.operacionId})` : ''}
        </span>
      )}
      {destino === 'nube' && organizarResult && organizarResult.error && (
        <span className="field-hint hint-warn">
          La subida terminó bien, pero no se pudo lanzar la organización: {organizarResult.error}
        </span>
      )}

      {uploading ? (
        <button className="btn-run" onClick={() => api.cloudCancel()}>
          Cancelar subida
        </button>
      ) : (
        <button className="btn-run" disabled={!puedeSubir} onClick={subir}>
          {busy ? 'Comprobando…' : destino === 'nube' ? 'Subir y organizar en la nube' : 'Subir al bucket'}
        </button>
      )}
    </>
  )
}
