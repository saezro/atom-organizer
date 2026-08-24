import { useCallback, useEffect, useState } from 'react'
import KioskLock from './KioskLock.jsx'
import { api } from './bridge.js'

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
    return <KioskLock modo="verificar" onOk={() => { setUltimoToque(Date.now()); setDesbloqueado(true) }} />
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

  return children
}
