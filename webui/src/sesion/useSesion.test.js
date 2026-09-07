import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

const cloudStatusMock = vi.fn()
const cloudLoginMock = vi.fn()
const cloudLogoutMock = vi.fn()
const listarPerfilesMock = vi.fn()

vi.mock('../bridge.js', () => ({
  api: {
    cloudStatus: (...args) => cloudStatusMock(...args),
    cloudLogin: (...args) => cloudLoginMock(...args),
    cloudLogout: (...args) => cloudLogoutMock(...args),
    // Se ejerce en PantallaEntrada.test.jsx, no aquí: el hook solo necesita
    // no romperse si el bridge la expone (fail-soft ya cubierto si no).
    listarPerfiles: (...args) => listarPerfilesMock(...args),
  },
  // Réplica fiel del real: escucha `atom:cloud` en window, así los tests
  // simulan el evento con `window.dispatchEvent(new CustomEvent(...))`.
  onCloud: (handler) => {
    const wrapped = (e) => handler(e.detail)
    window.addEventListener('atom:cloud', wrapped)
    return () => window.removeEventListener('atom:cloud', wrapped)
  },
}))

import { useSesion } from './useSesion.js'

const CLAVE = 'atom.sesion.invitado'

beforeEach(() => {
  cloudStatusMock.mockReset()
  cloudLoginMock.mockReset()
  cloudLogoutMock.mockReset()
  listarPerfilesMock.mockReset()
  localStorage.clear()
  cloudStatusMock.mockResolvedValue({ ok: true, configured: true, logged_in: false })
  cloudLoginMock.mockResolvedValue({ started: true })
  cloudLogoutMock.mockResolvedValue({ ok: true })
  listarPerfilesMock.mockResolvedValue([])
})

describe('useSesion', () => {
  it('sin sesión previa arranca con entrado=false', async () => {
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))
    expect(result.current.entrado).toBe(false)
    expect(result.current.invitado).toBe(false)
    expect(result.current.cuenta).toBeNull()
  })

  it('cloudStatus logged_in=true entra con cuenta rellenada', async () => {
    cloudStatusMock.mockResolvedValue({
      ok: true,
      configured: true,
      logged_in: true,
      email: 'user@example.com',
      nombre: 'Usuario',
      picture: 'http://x/pic.png',
    })
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))
    expect(result.current.entrado).toBe(true)
    expect(result.current.invitado).toBe(false)
    expect(result.current.cuenta).toEqual({
      email: 'user@example.com',
      nombre: 'Usuario',
      picture: 'http://x/pic.png',
    })
  })

  it('entrarSinCuenta marca invitado y lo persiste en localStorage', async () => {
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))
    act(() => {
      result.current.entrarSinCuenta()
    })
    expect(result.current.entrado).toBe(true)
    expect(result.current.invitado).toBe(true)
    expect(localStorage.getItem(CLAVE)).toBe('1')
  })

  it('salir limpia cuenta e invitado', async () => {
    cloudStatusMock.mockResolvedValue({
      ok: true,
      configured: true,
      logged_in: true,
      email: 'user@example.com',
    })
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))
    expect(result.current.entrado).toBe(true)

    await act(async () => {
      await result.current.salir()
    })

    expect(cloudLogoutMock).toHaveBeenCalledTimes(1)
    expect(result.current.entrado).toBe(false)
    expect(result.current.invitado).toBe(false)
    expect(result.current.cuenta).toBeNull()
    expect(localStorage.getItem(CLAVE)).toBeNull()
  })

  // Regresión: el gate de `App.jsx` no pinta nada mientras `cargando`, así que
  // un `cloudStatus` que no vuelve (bridge sin inyectar, red colgada) dejaba
  // la app en negro para siempre, sin ni siquiera poder entrar sin cuenta.
  it('si cloudStatus no responde deja de cargar y ofrece entrar', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    cloudStatusMock.mockImplementation(() => new Promise(() => {}))
    const { result } = renderHook(() => useSesion())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(7000)
    })
    expect(result.current.cargando).toBe(false)
    expect(result.current.entrado).toBe(false)
    expect(result.current.error).toBeTruthy()
    vi.useRealTimers()
  })

  // Misma trampa con el login: si se cierra la ventana de Google a medias,
  // `cloudLogin` puede no resolver nunca.
  it('si cloudLogin no responde deja de cargar en vez de colgarse', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    cloudLoginMock.mockImplementation(() => new Promise(() => {}))
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))
    act(() => {
      result.current.entrarConGoogle()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(130000)
    })
    expect(result.current.cargando).toBe(false)
    expect(result.current.error).toBeTruthy()
    vi.useRealTimers()
  })

  // Regresión: `cloudLogin` resuelve en cuanto se lanza el hilo (fire-and-
  // forget), mucho antes de que el usuario termine el consentimiento en el
  // navegador. Antes de este fix, `entrarConGoogle` refrescaba justo ahí y el
  // `cloudStatus` todavía daba `logged_in:false`, obligando a un segundo
  // clic. Debe quedarse cargando hasta el evento `atom:cloud` de `kind:'login'`.
  it('entrarConGoogle espera el evento atom:cloud antes de refrescar (sin segundo clic)', async () => {
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))

    // Mientras no llega el evento, cloudStatus todavía diría logged_in=false
    // (como en el bug real: el navegador aún no ha cerrado el consentimiento).
    cloudStatusMock.mockResolvedValue({ ok: true, configured: true, logged_in: false })

    act(() => {
      result.current.entrarConGoogle()
    })

    await waitFor(() => expect(cloudLoginMock).toHaveBeenCalledTimes(1))
    // Sigue cargando: el `atom:cloud` de login todavía no ha llegado.
    expect(result.current.cargando).toBe(true)
    expect(result.current.entrado).toBe(false)

    // Ahora sí llega el consentimiento: cloudStatus pasa a logged_in=true y
    // el backend emite el evento de login.
    cloudStatusMock.mockResolvedValue({
      ok: true,
      configured: true,
      logged_in: true,
      email: 'user@example.com',
      nombre: 'Usuario',
      picture: null,
    })

    act(() => {
      window.dispatchEvent(
        new CustomEvent('atom:cloud', { detail: { kind: 'login', ok: true, email: 'user@example.com' } })
      )
    })

    await waitFor(() => expect(result.current.cargando).toBe(false))
    expect(result.current.entrado).toBe(true)
    expect(result.current.cuenta?.email).toBe('user@example.com')
  })

  // El evento de login también puede traer un fallo (consentimiento
  // cancelado, error de red durante el intercambio de token...). Debe
  // reflejarse como error, no dejar la pantalla cargando para siempre.
  it('entrarConGoogle refleja el error si el evento atom:cloud de login trae ok:false', async () => {
    const { result } = renderHook(() => useSesion())
    await waitFor(() => expect(result.current.cargando).toBe(false))

    act(() => {
      result.current.entrarConGoogle()
    })
    await waitFor(() => expect(cloudLoginMock).toHaveBeenCalledTimes(1))

    act(() => {
      window.dispatchEvent(
        new CustomEvent('atom:cloud', { detail: { kind: 'login', ok: false, text: 'Consentimiento cancelado.' } })
      )
    })

    await waitFor(() => expect(result.current.cargando).toBe(false))
    expect(result.current.entrado).toBe(false)
    expect(result.current.error).toBe('Consentimiento cancelado.')
  })
})
