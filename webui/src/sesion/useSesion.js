import { useCallback, useEffect, useRef, useState } from 'react'
import { api, onCloud } from '../bridge.js'
import { conPlazo } from '../plazo'

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
// Plazo máximo para el `cloudStatus` de arranque. El gate de `App.jsx` no
// pinta nada mientras `cargando`, así que una llamada que no vuelva (bridge
// sin inyectar, `cloud_status` colgado contra la red) dejaría la app en negro
// para siempre. Al vencer se sigue como "sin cuenta": la pantalla de entrada
// aparece y el usuario puede al menos entrar sin cuenta y trabajar en local.
const ESPERA_ESTADO_MS = 6000

export function useSesion() {
  const [cargando, setCargando] = useState(true)
  const [cuenta, setCuenta] = useState(null)
  const [invitado, setInvitado] = useState(false)
  const [error, setError] = useState(null)
  // Perfiles guardados en este equipo (selector tipo Netflix de la pantalla
  // de entrada). Aparte de `cuenta`/`invitado` (la sesión ACTIVA): esto es la
  // lista de sesiones que se podrían activar.
  const [perfiles, setPerfiles] = useState([])

  // Desuscripción del `atom:cloud` de un login en curso. Vive en un ref (no
  // en el closure del callback) para poder cortarla también al desmontar el
  // hook, no solo al terminar la espera.
  const offLoginRef = useRef(null)

  const refrescar = useCallback(async () => {
    setCargando(true)
    setError(null)
    try {
      const status = await conPlazo(api.cloudStatus(), ESPERA_ESTADO_MS)
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

  // Lista de perfiles guardados. Fail-soft: si el bridge no expone el método
  // (build vieja) o falla, se queda en `[]` y la pantalla de entrada cae al
  // camino de siempre (dos botones) en vez de romper el arranque.
  const cargarPerfiles = useCallback(async () => {
    try {
      const lista = await api.listarPerfiles()
      setPerfiles(Array.isArray(lista) ? lista : [])
    } catch {
      setPerfiles([])
    }
  }, [])

  useEffect(() => {
    refrescar()
    cargarPerfiles()
    // Solo al montar: nada de polling en reposo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Si el hook se desmonta con un login en curso (navegación fuera de la
  // pantalla de entrada), no dejar el listener de `atom:cloud` colgado.
  useEffect(() => {
    return () => {
      offLoginRef.current?.()
      offLoginRef.current = null
    }
  }, [])

  const entrarConGoogle = useCallback(async () => {
    setError(null)
    setCargando(true)
    try {
      // `cloud_login` es fire-and-forget: la promesa resuelve en cuanto se
      // lanza el hilo, mucho antes de que el usuario complete el
      // consentimiento en el navegador. El resultado real llega después por
      // el evento `atom:cloud` (`kind:'login'`), así que hay que suscribirse
      // ANTES de lanzar la llamada (por si el hilo tardara menos que este
      // `await`) y esperar a que llegue para refrescar el estado.
      const esperaLogin = new Promise((resolve) => {
        offLoginRef.current = onCloud((d) => {
          if (d.kind !== 'login') return
          offLoginRef.current?.()
          offLoginRef.current = null
          resolve(d)
        })
      })

      // Sin plazo el usuario queda atrapado: si cierra la ventana de Google a
      // medias, el evento de login no llega nunca y la pantalla de entrada se
      // quedaría con los botones inertes. Se da margen de sobra (2 min) para
      // completar el consentimiento de verdad.
      const respuesta = await conPlazo(api.cloudLogin(), 120000)
      if (!respuesta || respuesta.started !== true) {
        // Fallo inmediato (login ya en curso, falta configuración, equipo
        // emparejado por QR sin navegador...): no habrá evento de login que
        // esperar.
        throw new Error(String(respuesta?.error || respuesta?.reason || 'No se pudo iniciar sesión.'))
      }

      const evento = await conPlazo(esperaLogin, 120000)
      if (evento && evento.ok === false) {
        throw new Error(String(evento.text || 'No se pudo iniciar sesión.'))
      }
      await refrescar()
    } catch (e) {
      setError(String(e?.message || e))
      setCargando(false)
    } finally {
      offLoginRef.current?.()
      offLoginRef.current = null
    }
  }, [refrescar])

  const entrarSinCuenta = useCallback(() => {
    setError(null)
    setCuenta(null)
    setInvitado(true)
    escribirInvitado(true)
  }, [])

  // Activa un perfil guardado sin pasar por el consentimiento de Google si
  // la credencial sigue viva. Decisión explícita del dueño del producto: si
  // `ok:false` (o la llamada lanza), se relanza `entrarConGoogle()` EN
  // SILENCIO — sin mensaje de error ni confirmación — porque para el usuario
  // es solo "pedirle que se loguee otra vez", no un fallo que explicar.
  const entrarConPerfil = useCallback(async (email) => {
    try {
      const respuesta = await api.activarPerfil(email)
      if (respuesta && respuesta.ok) {
        await refrescar()
        return
      }
    } catch {
      // credencial caducada / bridge sin el método: cae al login normal.
    }
    await entrarConGoogle()
  }, [refrescar, entrarConGoogle])

  const quitarPerfil = useCallback(async (email) => {
    try {
      await api.borrarPerfil(email)
    } catch (e) {
      setError(String(e?.message || e))
    }
    await cargarPerfiles()
  }, [cargarPerfiles])

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
    perfiles,
    cargarPerfiles,
    entrarConPerfil,
    quitarPerfil,
  }
}
