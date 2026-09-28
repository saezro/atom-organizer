import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act, fireEvent } from '@testing-library/react'
import { useEffect } from 'react'

const pinEstado = vi.fn()
const cloudStatus = vi.fn()
const pinVerificar = vi.fn().mockResolvedValue({ ok: true })

// `onControlUi` real (no un stub vacio): hace falta disparar el evento de
// login remoto desde el test tal como lo haria el SSE. `KioskGuard` se
// suscribe DOS veces (cola de acciones reproducibles + login), así que el
// stub tiene que soportar varios handlers a la vez, como el `bridge.js` real
// (basado en `addEventListener`).
let handlersControlUi = []
function handlerControlUi(d) {
  handlersControlUi.forEach((h) => h(d))
}

const puente = {
  isServerMode: () => false,
  onControlUi: (handler) => {
    handlersControlUi.push(handler)
    return () => {
      const i = handlersControlUi.indexOf(handler)
      if (i >= 0) handlersControlUi.splice(i, 1)
    }
  },
  api: {
    pinEstado,
    pinVerificar,
    pinFijar: vi.fn().mockResolvedValue({ ok: true }),
    pinCambiar: vi.fn().mockResolvedValue({ ok: true }),
    pinTelemetria: vi.fn().mockResolvedValue({ ok: true }),
    cloudStatus,
    cloudLogout: vi.fn().mockResolvedValue({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
  },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskGuard } = await import('./KioskGuard.jsx')

describe('KioskGuard', () => {
  beforeEach(() => {
    pinEstado.mockReset().mockResolvedValue({ ok: true, hay_pin: false, bloqueado: false, espera_segundos: 0 })
    pinVerificar.mockClear()
    handlersControlUi = []
  })

  it('con PIN fijado pinta el bloqueo y no los hijos', async () => {
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByTestId('kiosk-pin')).toBeTruthy())
    expect(screen.queryByText('contenido')).toBeNull()
  })

  it('sin PIN y con sesion obliga a fijarlo', async () => {
    render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByText(/pin nuevo/i)).toBeTruthy())
    expect(screen.queryByText('contenido')).toBeNull()
  })

  it('sin PIN y sin sesion deja usar el kiosco', async () => {
    render(<KioskGuard status={{ logged_in: false }} ocupado={false}><p>contenido</p></KioskGuard>)
    await vi.waitFor(() => expect(screen.getByText('contenido')).toBeTruthy())
  })

  it('tras diez minutos de inactividad vuelve a bloquear', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado={false} desbloqueadoInicial><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      expect(screen.getByText('contenido')).toBeTruthy()
      await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000 + 1000) })
      expect(screen.queryByText('contenido')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('no bloquea mientras hay una subida en curso', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado desbloqueadoInicial><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      await act(async () => { vi.advanceTimersByTime(20 * 60 * 1000) })
      expect(screen.getByText('contenido')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('login remoto (atom:control_ui accion login) anima el pad y desbloquea sin pinVerificar', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado={false}><p>contenido</p></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      expect(screen.getByTestId('kiosk-pin')).toBeTruthy()
      await vi.waitFor(() => expect(handlersControlUi.length).toBeGreaterThan(0))

      act(() => { handlerControlUi({ accion: 'login' }) })
      // 4 puntos * 250ms + el paso final de desbloqueo.
      await act(async () => { vi.advanceTimersByTime(5 * 250) })

      expect(screen.getByText('contenido')).toBeTruthy()
      expect(pinVerificar).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('cola acciones remotas llegadas en bloqueado y las entrega de una en una tras desbloquear', async () => {
    vi.useFakeTimers()
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    // Hijo de prueba: apunta cada `accionRemota` que recibe (en orden) y
    // permite "consumirla" a mano, como haria `KioskScreen` al terminar de
    // reproducirla.
    const recibidas = []
    function Hijo({ accionRemota, onAccionRemotaConsumida }) {
      useEffect(() => { if (accionRemota) recibidas.push(accionRemota.accion) }, [accionRemota])
      return accionRemota
        ? <button data-testid="consumir" onClick={onAccionRemotaConsumida}>consumir {accionRemota.accion}</button>
        : <p data-testid="sin-cola">sin cola</p>
    }
    try {
      render(<KioskGuard status={{ logged_in: true }} ocupado={false}><Hijo /></KioskGuard>)
      await act(async () => { await Promise.resolve() })
      expect(screen.getByTestId('kiosk-pin')).toBeTruthy()
      await vi.waitFor(() => expect(handlersControlUi.length).toBeGreaterThan(0))

      // Dos acciones mientras esta bloqueado: el hijo ni siquiera esta
      // montado, así que no pueden haberse "recibido" todavia.
      act(() => { handlerControlUi({ accion: 'carpeta', path: '/media/usb/VUELO' }) })
      act(() => { handlerControlUi({ accion: 'organizar' }) })
      expect(recibidas).toEqual([])

      // Desbloqueo remoto (mismo camino que el test de login de arriba).
      act(() => { handlerControlUi({ accion: 'login' }) })
      await act(async () => { vi.advanceTimersByTime(5 * 250) })
      expect(screen.getByTestId('consumir')).toBeTruthy()

      // Primero llega "carpeta" (la mas antigua), no "organizar".
      expect(recibidas).toEqual(['carpeta'])

      // Al consumirla, entrega la siguiente de la cola.
      fireEvent.click(screen.getByTestId('consumir'))
      expect(recibidas).toEqual(['carpeta', 'organizar'])

      // Al consumir la ultima, no queda ninguna.
      fireEvent.click(screen.getByTestId('consumir'))
      expect(screen.getByTestId('sin-cola')).toBeTruthy()
      expect(recibidas).toEqual(['carpeta', 'organizar'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('login remoto no hace nada si ya estaba desbloqueado', async () => {
    pinEstado.mockResolvedValue({ ok: true, hay_pin: true, bloqueado: false, espera_segundos: 0 })
    render(
      <KioskGuard status={{ logged_in: true }} ocupado={false} desbloqueadoInicial>
        <p>contenido</p>
      </KioskGuard>,
    )
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('contenido')).toBeTruthy()
    await vi.waitFor(() => expect(handlersControlUi.length).toBeGreaterThan(0))
    act(() => { handlerControlUi({ accion: 'login' }) })
    expect(screen.getByText('contenido')).toBeTruthy()
    expect(pinVerificar).not.toHaveBeenCalled()
  })
})

describe('KioskGuard — ante la duda, bloquea', () => {
  beforeEach(() => {
    pinEstado.mockReset()
  })

  it('si pinEstado falla NO abre el kiosco, aunque no haya sesion', async () => {
    // Regresion: el catch asumia "no hay PIN" y con `status` sin sesion
    // dejaba pasar a children. Un backend caido abria la Pi de par en par.
    pinEstado.mockRejectedValue(new Error('sin backend'))
    await act(async () => {
      render(
        <KioskGuard status={{ logged_in: false }} ocupado={false}>
          <div data-testid="kiosk-dentro">dentro</div>
        </KioskGuard>,
      )
    })
    expect(screen.queryByTestId('kiosk-dentro')).toBeNull()
    expect(screen.getByTestId('kiosk-pin-error-estado')).toBeTruthy()
  })

  it('una respuesta sin hay_pin tampoco se toma por "no hay PIN"', async () => {
    pinEstado.mockResolvedValue({ ok: false, error: 'store roto' })
    await act(async () => {
      render(
        <KioskGuard status={{ logged_in: false }} ocupado={false}>
          <div data-testid="kiosk-dentro">dentro</div>
        </KioskGuard>,
      )
    })
    expect(screen.queryByTestId('kiosk-dentro')).toBeNull()
    expect(screen.getByTestId('kiosk-pin-error-estado')).toBeTruthy()
  })

  it('no abre mientras `status` aun no ha resuelto', async () => {
    // Ventana de carrera al arrancar: pinEstado resuelve antes que
    // cloudStatus. Sin esta guarda la pantalla queda abierta en ese hueco.
    pinEstado.mockResolvedValue({ ok: true, hay_pin: false, bloqueado: false, espera_segundos: 0 })
    await act(async () => {
      render(
        <KioskGuard status={null} ocupado={false}>
          <div data-testid="kiosk-dentro">dentro</div>
        </KioskGuard>,
      )
    })
    expect(screen.queryByTestId('kiosk-dentro')).toBeNull()
  })
})
