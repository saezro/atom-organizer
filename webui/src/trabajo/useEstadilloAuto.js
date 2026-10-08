import { useEffect, useState } from 'react'
import { api } from '../bridge'
import { conPlazo } from '../plazo'

// Mismos plazos que `PasoEstadillo`.
const ESPERA_AUTODETECCION_MS = 15000
const REINTENTO_CARPETA_MS = 3000
const ESPERA_CARPETA_MAX_MS = 120000

// Autodetecta el estadillo de la carpeta de origen cuando el módulo de
// estadillos no está disponible (no hay `PasoEstadillo`). Misma llamada que
// `PasoEstadillo`: `estadillosDetectar(carpeta, true)`. Sin fallback
// silencioso: devuelve `{estado, rutas, mensaje}` con estado
// idle | buscando | ok | nada | error. Si el origen cambia, la respuesta en
// vuelo y los reintentos pendientes se descartan.
export default function useEstadilloAuto(origen, activo) {
  const [res, setRes] = useState({ origen: '', estado: 'idle', rutas: [], mensaje: '' })

  useEffect(() => {
    if (!activo || !origen) return
    let vivo = true
    let temporizador = null
    const inicio = Date.now()
    const poner = (estado, rutas = [], mensaje = '') => setRes({ origen, estado, rutas, mensaje })
    poner('buscando')
    const lanzar = async () => {
      try {
        const r = await conPlazo(
          api.estadillosDetectar(origen, true),
          ESPERA_AUTODETECCION_MS,
          'La búsqueda del estadillo ha tardado demasiado.'
        )
        if (!vivo) return
        if (r?.no_existe) {
          if (Date.now() - inicio >= ESPERA_CARPETA_MAX_MS) {
            poner('error', [], 'La carpeta de origen sigue sin estar disponible tras 2 minutos. Comprueba el montaje.')
            return
          }
          poner('buscando', [], 'La carpeta de origen aún no está disponible, reintentando…')
          temporizador = setTimeout(() => { if (vivo) lanzar() }, REINTENTO_CARPETA_MS)
          return
        }
        if (r?.error) { poner('error', [], String(r.error)); return }
        const rutas = Array.isArray(r?.rutas) ? r.rutas : []
        if (rutas.length === 0) poner('nada')
        else poner('ok', rutas)
      } catch (e) {
        if (vivo) poner('error', [], String(e?.message || e || 'error'))
      }
    }
    lanzar()
    return () => { vivo = false; if (temporizador) clearTimeout(temporizador) }
  }, [origen, activo])

  // Resultado de otro origen (aún en vuelo): se trata como «buscando».
  if (!activo || !origen) return { estado: 'idle', rutas: [], mensaje: '' }
  if (res.origen !== origen) return { estado: 'buscando', rutas: [], mensaje: '' }
  return res
}
