import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, screen, fireEvent } from '@testing-library/react'

const pinVerificar = vi.fn()
const pinFijar = vi.fn()
const pinCambiar = vi.fn()
const pinTelemetria = vi.fn()

// vi.fn con valor por defecto false: el grueso de los tests de este fichero
// usa el teclado de escritorio (click); un unico bloque al final la pone a
// true para probar el camino tactil (pointerdown/up) del pad resistivo.
const isServerModeMock = vi.fn(() => false)
const puente = {
  isServerMode: () => isServerModeMock(),
  api: { pinVerificar, pinFijar, pinCambiar, pinTelemetria },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskLock } = await import('./KioskLock.jsx')

// Deja pasar mas de los 150ms de antirrebote del pad tactil entre cada
// tecla, para que las pulsaciones "normales" (separadas en el tiempo) del
// resto de tests no se coman ninguna.
async function teclear(digitos) {
  for (const d of digitos) {
    screen.getByRole('button', { name: d }).click()
    await new Promise((r) => setTimeout(r, 160))
  }
}

describe('KioskLock', () => {
  beforeEach(() => {
    pinVerificar.mockReset().mockResolvedValue({ ok: true })
    pinFijar.mockReset().mockResolvedValue({ ok: true })
    pinCambiar.mockReset().mockResolvedValue({ ok: true })
    pinTelemetria.mockReset().mockResolvedValue({ ok: true })
    isServerModeMock.mockReset().mockReturnValue(false)
  })

  it('pinta los diez digitos y el borrado', () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    for (const d of '0123456789') {
      expect(screen.getByRole('button', { name: d })).toBeTruthy()
    }
    expect(screen.getByRole('button', { name: /borrar/i })).toBeTruthy()
  })

  it('al completar cuatro digitos verifica y avisa al padre', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    await teclear('1234')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('no llama al backend con menos de cuatro digitos', async () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    await teclear('123')
    expect(pinVerificar).not.toHaveBeenCalled()
  })

  it('borrar quita el ultimo digito', async () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    await teclear('123')
    screen.getByRole('button', { name: /borrar/i }).click()
    // Deja pasar el antirrebote antes de la siguiente tecla: el click de
    // Borrar tambien consume la ventana de los 150ms.
    await new Promise((r) => setTimeout(r, 160))
    await teclear('45')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1245'))
  })

  it('ignora un rebote del tactil (<150ms) pero acepta toques separados (>=150ms)', async () => {
    vi.useFakeTimers()
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    const boton1 = screen.getByRole('button', { name: '1' })
    // Rebote: dos eventos de la MISMA tecla separados menos de 150ms cuentan uno solo.
    fireEvent.click(boton1)
    vi.advanceTimersByTime(50)
    fireEvent.click(boton1)
    // A partir de aqui, toques separados 150ms o mas: cuentan todos.
    for (const d of '234') {
      vi.advanceTimersByTime(150)
      fireEvent.click(screen.getByRole('button', { name: d }))
    }
    await vi.advanceTimersByTimeAsync(0)
    // '1'(rebote ignorado) + '1' + '2' + '3' + '4' = '1234'
    expect(pinVerificar).toHaveBeenCalledWith('1234')
    vi.useRealTimers()
  })

  it('digitos distintos tecleados muy seguido (0-50ms) cuentan todos, no son rebote', async () => {
    vi.useFakeTimers()
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    fireEvent.click(screen.getByRole('button', { name: '1' }))
    vi.advanceTimersByTime(0)
    fireEvent.click(screen.getByRole('button', { name: '2' }))
    vi.advanceTimersByTime(30)
    fireEvent.click(screen.getByRole('button', { name: '3' }))
    vi.advanceTimersByTime(50)
    fireEvent.click(screen.getByRole('button', { name: '4' }))
    await vi.advanceTimersByTimeAsync(0)
    expect(pinVerificar).toHaveBeenCalledWith('1234')
    vi.useRealTimers()
  })

  it('la misma tecla repetida a menos de 150ms se ignora, pero a 150ms o mas cuenta', async () => {
    vi.useFakeTimers()
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    const boton5 = screen.getByRole('button', { name: '5' })
    fireEvent.click(screen.getByRole('button', { name: '1' }))
    vi.advanceTimersByTime(150)
    fireEvent.click(boton5)
    vi.advanceTimersByTime(80)
    fireEvent.click(boton5) // rebote: mismo digito, <150ms, se ignora
    vi.advanceTimersByTime(150)
    fireEvent.click(boton5) // ya pasaron >=150ms desde el ultimo '5' aceptado: cuenta
    vi.advanceTimersByTime(150)
    fireEvent.click(screen.getByRole('button', { name: '9' }))
    await vi.advanceTimersByTimeAsync(0)
    // '1' + '5' + '5'(segundo, valido) + '9' = '1559'
    expect(pinVerificar).toHaveBeenCalledWith('1559')
    vi.useRealTimers()
  })

  it('un PIN incorrecto muestra el error y no avisa al padre', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    await teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/pin incorrecto/i)).toBeTruthy())
    expect(onOk).not.toHaveBeenCalled()
  })

  it('el error de PIN no pinta texto visible que desplace el teclado: solo el marco rojo', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
    const { container } = render(<KioskLock modo="verificar" onOk={() => {}} />)
    await teclear('9999')
    // El marco rojo a pantalla completa (fixed, fuera del flujo) es la senal visible.
    await vi.waitFor(() => expect(container.querySelector('.kiosk-pin-flash')).toBeTruthy())
    // El texto del error sigue en el DOM (para lectores de pantalla) pero
    // fuera del flujo visual: nunca en un <p> normal que empuje el teclado.
    const nodoError = screen.getByTestId('kiosk-pin-error')
    expect(nodoError.className).toBe('kiosk-pin-sr')
    expect(nodoError.textContent).toMatch(/pin incorrecto/i)
    // El teclado sigue intacto: los 10 digitos y Borrar siguen ahi.
    for (const d of '0123456789') {
      expect(screen.getByRole('button', { name: d })).toBeTruthy()
    }
  })

  it('mantener pulsado Borrar borra todo el PIN tecleado', async () => {
    vi.useFakeTimers()
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '1' }))
    fireEvent.click(screen.getByRole('button', { name: '2' }))
    const borrar = screen.getByRole('button', { name: /borrar/i })
    fireEvent.mouseDown(borrar)
    vi.advanceTimersByTime(650)
    fireEvent.mouseUp(borrar)
    vi.useRealTimers()
    // El reloj falso deja la marca del ultimo toque en un instante que no
    // corresponde al reloj real: da margen de sobra (> 150ms) antes de
    // seguir tecleando para no chocar con el antirrebote.
    await new Promise((r) => setTimeout(r, 300))
    await teclear('3456')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('3456'))
  })

  it('en modo fijar pide repetir el PIN antes de guardarlo', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="fijar" onOk={onOk} />)
    await teclear('1234')
    await vi.waitFor(() => expect(screen.getByText(/repite/i)).toBeTruthy())
    expect(pinFijar).not.toHaveBeenCalled()
    await teclear('1234')
    await vi.waitFor(() => expect(pinFijar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('en modo fijar, si la repeticion no coincide avisa y no guarda', async () => {
    render(<KioskLock modo="fijar" onOk={() => {}} />)
    await teclear('1234')
    await vi.waitFor(() => expect(screen.getByText(/repite/i)).toBeTruthy())
    await teclear('5678')
    await vi.waitFor(() => expect(screen.getByText(/no coinciden/i)).toBeTruthy())
    expect(pinFijar).not.toHaveBeenCalled()
  })

  it('en modo cambiar pide el actual, luego el nuevo y su repeticion', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="cambiar" onOk={onOk} onCancelar={() => {}} />)
    await teclear('1111')
    await teclear('2222')
    await teclear('2222')
    await vi.waitFor(() => expect(pinCambiar).toHaveBeenCalledWith('1111', '2222'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('un intento correcto manda telemetria con ok:true, n_intento:1 y sin digitos', async () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    await teclear('1234')
    await vi.waitFor(() => expect(pinTelemetria).toHaveBeenCalledTimes(1))
    const payload = pinTelemetria.mock.calls[0][0]
    expect(payload.ok).toBe(true)
    expect(payload.n_intento).toBe(1)
    expect(payload.toques_aceptados).toBe(4)
    expect(payload.borrados).toBe(0)
    expect(Array.isArray(payload.intervalos_ms)).toBe(true)
    expect(typeof payload.duracion_total_ms).toBe('number')
    const bruto = JSON.stringify(payload)
    expect(bruto).not.toMatch(/1234/)
    expect(bruto).not.toContain('pin')
  })

  it('n_intento sube en fallos consecutivos y se reinicia al acertar', async () => {
    pinVerificar
      .mockResolvedValueOnce({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
      .mockResolvedValueOnce({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    await teclear('9999')
    await vi.waitFor(() => expect(pinTelemetria).toHaveBeenCalledTimes(1))
    await teclear('9999')
    await vi.waitFor(() => expect(pinTelemetria).toHaveBeenCalledTimes(2))
    await teclear('1234')
    await vi.waitFor(() => expect(pinTelemetria).toHaveBeenCalledTimes(3))
    await teclear('9999')
    await vi.waitFor(() => expect(pinTelemetria).toHaveBeenCalledTimes(4))
    const nIntentos = pinTelemetria.mock.calls.map((c) => c[0].n_intento)
    // El intento que acierta cuenta como el propio consecutivo en curso (3);
    // el reinicio a 1 se ve en el SIGUIENTE intento, ya de la sesion nueva.
    expect(nIntentos).toEqual([1, 2, 3, 1])
  })

  it('cuenta los toques descartados por rebote en la telemetria', async () => {
    vi.useFakeTimers()
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    const boton1 = screen.getByRole('button', { name: '1' })
    fireEvent.click(boton1)
    vi.advanceTimersByTime(50)
    fireEvent.click(boton1) // rebote: descartado
    for (const d of '234') {
      vi.advanceTimersByTime(150)
      fireEvent.click(screen.getByRole('button', { name: d }))
    }
    await vi.advanceTimersByTimeAsync(0)
    vi.useRealTimers()
    await vi.waitFor(() => expect(pinTelemetria).toHaveBeenCalledTimes(1))
    expect(pinTelemetria.mock.calls[0][0].toques_descartados_debounce).toBe(1)
  })

  // Regresion: sin `finally`, un rechazo INESPERADO de `pinVerificar` (no ya
  // el `.catch` de red, sino algo que reviente despues) dejaba `ocupado` en
  // `true` para siempre y el pad muerto sin ningun mensaje.
  it('si pinVerificar rechaza, muestra error y reactiva el teclado', async () => {
    pinVerificar.mockRejectedValue(new Error('El Organizer no responde.'))
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    await teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/no responde/i)).toBeTruthy())
    expect(onOk).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '1' }).disabled).toBe(false)
    // El teclado quedo reactivado de verdad: se puede volver a teclear.
    pinVerificar.mockResolvedValue({ ok: true })
    await teclear('1234')
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('cuando el backend manda espera, deshabilita el pad', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'Demasiados intentos.', espera_segundos: 30 })
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    await teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/30/)).toBeTruthy())
    expect(screen.getByRole('button', { name: '1' }).disabled).toBe(true)
  })

  // Camino tactil real (pointerdown/up), como en el pad resistivo de la Pi:
  // aqui vivia el bug del PIN que se pierde o teclea el digito vecino.
  it('en modo tactil, 4 toques de teclas distintas con leave intermedio verifican el PIN correcto', async () => {
    isServerModeMock.mockReturnValue(true)
    // jsdom no implementa `setPointerCapture` (Chromium del panel resistivo
    // si): sin este stub el `pointerleave` de abajo desarmaria el toque en
    // vez de simular la captura real que este test cubre.
    const capturaOriginal = window.HTMLElement.prototype.setPointerCapture
    window.HTMLElement.prototype.setPointerCapture = function setPointerCapture() {}
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)

    for (const d of '1234') {
      const boton = screen.getByRole('button', { name: d })
      fireEvent.pointerDown(boton, { pointerId: 1, clientX: 10, clientY: 10 })
      // El temblor del resistivo saca el dedo del borde de la tecla antes de
      // soltar: con `setPointerCapture` esto ya no debe perder el toque.
      fireEvent.pointerLeave(boton, { pointerId: 1, clientX: 10, clientY: 10 })
      fireEvent.pointerUp(boton, { pointerId: 1, clientX: 10, clientY: 10 })
      await new Promise((r) => setTimeout(r, 160))
    }

    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
    if (capturaOriginal) window.HTMLElement.prototype.setPointerCapture = capturaOriginal
    else delete window.HTMLElement.prototype.setPointerCapture
  })

  // Login remoto (`KioskGuard` sube `simularEntrada` al recibir
  // `atom:control_ui` accion 'login', backend ya validado): se anima el
  // relleno de los 4 puntos "como si lo tecleara una persona", SIN llamar a
  // `pinVerificar`, y al final sigue el mismo camino de exito que el pad.
  it('simularEntrada rellena los cuatro puntos y desbloquea sin llamar a pinVerificar', async () => {
    vi.useFakeTimers()
    try {
      const onOk = vi.fn()
      const { rerender } = render(<KioskLock modo="verificar" onOk={onOk} simularEntrada={0} />)
      rerender(<KioskLock modo="verificar" onOk={onOk} simularEntrada={1} />)

      const puntosLlenos = () => document.querySelectorAll('.kiosk-pin-punto.lleno').length

      expect(puntosLlenos()).toBe(0)
      await act(async () => { vi.advanceTimersByTime(250) })
      expect(puntosLlenos()).toBe(1)
      await act(async () => { vi.advanceTimersByTime(250) })
      expect(puntosLlenos()).toBe(2)
      await act(async () => { vi.advanceTimersByTime(500) })
      expect(puntosLlenos()).toBe(4)
      expect(onOk).not.toHaveBeenCalled()

      await act(async () => { vi.advanceTimersByTime(250) })
      expect(onOk).toHaveBeenCalled()
      expect(pinVerificar).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  // Seguridad (Rodrigo): quien mire la pantalla durante un login remoto NO
  // debe poder leer que tecla se pulsa. Solo los puntos se van rellenando;
  // ningun boton del teclado recibe jamas una clase de resaltado.
  it('simularEntrada nunca resalta ninguna tecla del pad', async () => {
    vi.useFakeTimers()
    try {
      const onOk = vi.fn()
      const { rerender } = render(<KioskLock modo="verificar" onOk={onOk} simularEntrada={0} />)
      rerender(<KioskLock modo="verificar" onOk={onOk} simularEntrada={1} />)

      const sinResaltado = () =>
        document.querySelectorAll('.kiosk-pin-tecla-simulada, [class*="resalt"], [class*="pulso"]').length === 0

      expect(sinResaltado()).toBe(true)
      for (let i = 0; i < 5; i += 1) {
        await act(async () => { vi.advanceTimersByTime(250) })
        expect(sinResaltado()).toBe(true)
      }
      expect(onOk).toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})
