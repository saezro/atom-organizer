import { cloneElement, useCallback, useEffect, useRef, useState } from 'react'
import KioskLock from './KioskLock.jsx'
import { api, onControlUi } from './bridge.js'

// Acciones que `KioskScreen` reproduce EN PANTALLA "como si las hiciera una
// persona" (Task «se ve como se mueve a los sitios»). 'login' no entra aqui:
// la anima este mismo componente (`simularEntrada`, ver abajo).
const ACCIONES_REPRODUCIBLES = ['carpeta', 'organizar', 'cancelar']

export const INACTIVIDAD_MS = 10 * 60 * 1000

/**
 * Puerta del kiosco. Envuelve a `KioskScreen`:
 *
 * - hay PIN            -> pide el PIN, sin salida posible
 * - sin PIN + sesion   -> obliga a crear uno (la Pi no se queda sin PIN)
 * - sin PIN sin sesion -> pasa: organizar es local y no expone nada
 *
 * `ocupado` congela el temporizador de inactividad: bloquear a mitad de
 * una subida dejaria el lote a medias sin nadie mirando.
 *
 * Ante la duda se BLOQUEA, nunca se abre: si no sabemos si hay PIN (el
 * backend no responde) asumir que no lo hay abriria la Pi de par en par
 * justo cuando algo va mal.
 */
export default function KioskGuard({ status, ocupado, desbloqueadoInicial = false, children }) {
  // null = cargando, 'error' = no se pudo saber, true/false = respuesta real.
  const [hayPin, setHayPin] = useState(null)
  const [desbloqueado, setDesbloqueado] = useState(desbloqueadoInicial)
  const [ultimoToque, setUltimoToque] = useState(() => Date.now())
  const [intento, setIntento] = useState(0)
  // Login por control remoto (`atom:control_ui` accion 'login', portátil
  // vinculado): cada incremento dispara en `KioskLock` la animacion de
  // rellenar el PIN "como si lo tecleara una persona" y, al terminar, el
  // MISMO camino de exito que el pad (ver `KioskLock.jsx`). Solo tiene
  // sentido si el kiosco esta bloqueado ahora mismo: si ya esta
  // desbloqueado no hay nada que animar.
  const [simularEntrada, setSimularEntrada] = useState(0)

  // Cola de acciones remotas "carpeta"/"organizar"/"cancelar": si llegan
  // mientras el kiosco esta bloqueado (PIN), `KioskScreen` ni siquiera esta
  // montado para reproducirlas — se acumulan aqui (este componente NUNCA se
  // desmonta) y se entregan de una en una en cuanto `children` vuelve a
  // pintarse, via `cloneElement` (`accionRemota`/`onAccionRemotaConsumida`
  // abajo). Si el kiosco ya estaba desbloqueado se entregan igual, sin
  // esperar nada: la cola no es solo para el caso bloqueado.
  const [colaRemota, setColaRemota] = useState([])
  const idColaRef = useRef(0)
  useEffect(() => onControlUi?.((d) => {
    if (!ACCIONES_REPRODUCIBLES.includes(d?.accion)) return
    idColaRef.current += 1
    setColaRemota((prev) => [...prev, { ...d, _id: idColaRef.current }])
  }), [])
  const consumirAccionRemota = useCallback(() => {
    setColaRemota((prev) => prev.slice(1))
  }, [])

  const reintentar = useCallback(() => {
    setHayPin(null)
    setIntento((n) => n + 1)
  }, [])

  useEffect(() => {
    let vivo = true
    api.pinEstado()
      .then((res) => {
        if (!vivo) return
        // Un `{ok:false}` o una respuesta sin `hay_pin` no es un "no hay
        // PIN": es que no lo sabemos.
        if (res && res.ok !== false && typeof res.hay_pin === 'boolean') setHayPin(res.hay_pin)
        else setHayPin('error')
      })
      .catch(() => { if (vivo) setHayPin('error') })
    return () => { vivo = false }
  }, [intento])

  useEffect(() => {
    const toque = () => setUltimoToque(Date.now())
    window.addEventListener('pointerdown', toque)
    window.addEventListener('keydown', toque)
    return () => {
      window.removeEventListener('pointerdown', toque)
      window.removeEventListener('keydown', toque)
    }
  }, [])

  useEffect(() => {
    if (!desbloqueado || ocupado) return undefined
    const t = setTimeout(() => setDesbloqueado(false), INACTIVIDAD_MS)
    return () => clearTimeout(t)
  }, [desbloqueado, ocupado, ultimoToque])

  // El portátil dispara el login "a distancia" (ya validado por el
  // backend, ver `atom_core`): si el kiosco esta bloqueado ahora mismo se
  // dispara la animacion de `KioskLock`; si ya esta desbloqueado no hay
  // nada que hacer. `desbloqueadoRef` evita el cierre obsoleto del handler
  // (el listener se monta una sola vez).
  const desbloqueadoRef = useRef(desbloqueado)
  useEffect(() => { desbloqueadoRef.current = desbloqueado }, [desbloqueado])
  useEffect(() => onControlUi?.((d) => {
    if (d?.accion !== 'login') return
    if (desbloqueadoRef.current) return
    setSimularEntrada((n) => n + 1)
  }), [])

  if (hayPin === null) return null

  if (hayPin === 'error') {
    return (
      <div className="kiosk kiosk-pin" data-testid="kiosk-pin-error-estado">
        <h2 className="kiosk-pin-titulo">No se pudo comprobar el PIN</h2>
        <p className="kiosk-pin-error">El servicio del kiosco no responde.</p>
        <button type="button" className="kiosk-pin-tecla kiosk-pin-aux" onClick={reintentar}>
          Reintentar
        </button>
      </div>
    )
  }

  if (hayPin && !desbloqueado) {
    return (
      <KioskLock
        modo="verificar"
        simularEntrada={simularEntrada}
        onOk={() => { setUltimoToque(Date.now()); setDesbloqueado(true) }}
      />
    )
  }

  // Sin PIN: hasta que no sepamos si hay sesion no se decide nada. Pasar
  // aqui con `status` aun sin resolver abre la pantalla en el hueco entre
  // que arranca la app y responde `cloudStatus()`.
  if (!hayPin) {
    if (status == null) return null
    if (status.logged_in === true) {
      return <KioskLock modo="fijar" onOk={() => { setHayPin(true); setUltimoToque(Date.now()); setDesbloqueado(true) }} />
    }
  }

  return cloneElement(children, {
    accionRemota: colaRemota[0] || null,
    onAccionRemotaConsumida: consumirAccionRemota,
  })
}
