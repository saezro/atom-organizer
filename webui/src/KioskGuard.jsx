import { useEffect, useState } from 'react'
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
 */
export default function KioskGuard({ status, ocupado, desbloqueadoInicial = false, children }) {
  const [hayPin, setHayPin] = useState(null)
  const [desbloqueado, setDesbloqueado] = useState(desbloqueadoInicial)
  const [ultimoToque, setUltimoToque] = useState(() => Date.now())

  useEffect(() => {
    let vivo = true
    api.pinEstado()
      .then((res) => { if (vivo) setHayPin(Boolean(res?.hay_pin)) })
      .catch(() => { if (vivo) setHayPin(false) })
    return () => { vivo = false }
  }, [])

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

  if (hayPin && !desbloqueado) {
    return <KioskLock modo="verificar" onOk={() => { setUltimoToque(Date.now()); setDesbloqueado(true) }} />
  }

  if (!hayPin && status?.logged_in === true) {
    return <KioskLock modo="fijar" onOk={() => { setHayPin(true); setUltimoToque(Date.now()); setDesbloqueado(true) }} />
  }

  return children
}
