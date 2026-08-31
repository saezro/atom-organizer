import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

const cloudStatusMock = vi.fn()
const cloudLoginMock = vi.fn()
const cloudLogoutMock = vi.fn()

vi.mock('../bridge.js', () => ({
  api: {
    cloudStatus: (...args) => cloudStatusMock(...args),
    cloudLogin: (...args) => cloudLoginMock(...args),
    cloudLogout: (...args) => cloudLogoutMock(...args),
  },
}))

import { useSesion } from './useSesion.js'

const CLAVE = 'atom.sesion.invitado'

beforeEach(() => {
  cloudStatusMock.mockReset()
  cloudLoginMock.mockReset()
  cloudLogoutMock.mockReset()
  localStorage.clear()
  cloudStatusMock.mockResolvedValue({ ok: true, configured: true, logged_in: false })
  cloudLoginMock.mockResolvedValue({ started: true })
  cloudLogoutMock.mockResolvedValue({ ok: true })
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
})
