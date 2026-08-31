import { useCallback, useEffect, useState } from 'react'
import { api } from '../bridge.js'

// Clave de localStorage para la sesión "sin cuenta" (invitado). Se persiste
// aparte de `cloud_status`: un invitado no tiene token de Google, así que el
// backend no sabe nada de él — la marca vive solo en el navegador/WebView.
const CLAVE_INVITADO = 'atom.sesion.invitado'

// Todo acceso a localStorage va envuelto en try/catch: en el WebView (Qt/
// WebView2) puede lanzar (perfil restringido, modo privado, primera carga
// antes de que el storage esté listo).
function leerInvitado() {
  try {
    return localStorage.getItem(CLAVE_INVITADO) === '1'
  } catch {
    return false
  }
}

function escribirInvitado(valor) {
  try {
    if (valor) localStorage.setItem(CLAVE_INVITADO, '1')
    else localStorage.removeItem(CLAVE_INVITADO)
  } catch {
    // Sin storage disponible no hay nada que persistir; la sesión de
    // invitado solo dura lo que dure el estado en memoria.
  }
}

// Centraliza el estado de sesión de la app (cuenta Google vinculada o modo
// invitado) para la pantalla de entrada. Sin polling: esto se pinta en un
// WebView y un `setInterval` en reposo lo lagea, así que solo se refresca
// bajo demanda (montaje, tras login/logout).
export function useSesion() {
  const [cargando, setCargando] = useState(true)
  const [cuenta, setCuenta] = useState(null)
  const [invitado, setInvitado] = useState(false)
  const [error, setError] = useState(null)

  const refrescar = useCallback(async () => {
    setCargando(true)
    setError(null)
    try {
      const status = await api.cloudStatus()
      if (status && status.logged_in) {
        setCuenta({
          email: status.email ?? null,
          nombre: status.nombre ?? null,
          picture: status.picture ?? null,
        })
        setInvitado(false)
        escribirInvitado(false)
      } else {
        setCuenta(null)
        setInvitado(leerInvitado())
      }
    } catch (e) {
      // Sin conexión con el bridge no hay sesión de Google que ofrecer, pero
      // el invitado guardado localmente sigue siendo válido.
      setCuenta(null)
      setInvitado(leerInvitado())
      setError(String(e?.message || e))
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => {
    refrescar()
    // Solo al montar: nada de polling en reposo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const entrarConGoogle = useCallback(async () => {
    setError(null)
    setCargando(true)
    try {
      await api.cloudLogin()
      await refrescar()
    } catch (e) {
      setError(String(e?.message || e))
      setCargando(false)
    }
  }, [refrescar])

  const entrarSinCuenta = useCallback(() => {
    setError(null)
    setCuenta(null)
    setInvitado(true)
    escribirInvitado(true)
  }, [])

  const salir = useCallback(async () => {
    setError(null)
    if (cuenta) {
      try {
        await api.cloudLogout()
      } catch (e) {
        setError(String(e?.message || e))
      }
    }
    escribirInvitado(false)
    setCuenta(null)
    setInvitado(false)
  }, [cuenta])

  return {
    cargando,
    entrado: Boolean(cuenta) || invitado,
    invitado,
    cuenta,
    error,
    entrarConGoogle,
    entrarSinCuenta,
    salir,
    refrescar,
  }
}
