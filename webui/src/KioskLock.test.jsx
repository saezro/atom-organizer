import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const pinVerificar = vi.fn()
const pinFijar = vi.fn()
const pinCambiar = vi.fn()

const puente = {
  isServerMode: () => false,
  api: { pinVerificar, pinFijar, pinCambiar },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskLock } = await import('./KioskLock.jsx')

function teclear(digitos) {
  for (const d of digitos) {
    screen.getByRole('button', { name: d }).click()
  }
}

describe('KioskLock', () => {
  beforeEach(() => {
    pinVerificar.mockReset().mockResolvedValue({ ok: true })
    pinFijar.mockReset().mockResolvedValue({ ok: true })
    pinCambiar.mockReset().mockResolvedValue({ ok: true })
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
    teclear('1234')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('no llama al backend con menos de cuatro digitos', () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('123')
    expect(pinVerificar).not.toHaveBeenCalled()
  })

  it('borrar quita el ultimo digito', async () => {
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('123')
    screen.getByRole('button', { name: /borrar/i }).click()
    teclear('45')
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('1245'))
  })

  it('un PIN incorrecto muestra el error y no avisa al padre', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
    const onOk = vi.fn()
    render(<KioskLock modo="verificar" onOk={onOk} />)
    teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/pin incorrecto/i)).toBeTruthy())
    expect(onOk).not.toHaveBeenCalled()
  })

  it('el error de PIN no pinta texto visible que desplace el teclado: solo el marco rojo', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'PIN incorrecto.', espera_segundos: 0 })
    const { container } = render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('9999')
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
    for (const d of '3456') {
      fireEvent.click(screen.getByRole('button', { name: d }))
    }
    await vi.waitFor(() => expect(pinVerificar).toHaveBeenCalledWith('3456'))
  })

  it('en modo fijar pide repetir el PIN antes de guardarlo', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="fijar" onOk={onOk} />)
    teclear('1234')
    await vi.waitFor(() => expect(screen.getByText(/repite/i)).toBeTruthy())
    expect(pinFijar).not.toHaveBeenCalled()
    teclear('1234')
    await vi.waitFor(() => expect(pinFijar).toHaveBeenCalledWith('1234'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('en modo fijar, si la repeticion no coincide avisa y no guarda', async () => {
    render(<KioskLock modo="fijar" onOk={() => {}} />)
    teclear('1234')
    await vi.waitFor(() => expect(screen.getByText(/repite/i)).toBeTruthy())
    teclear('5678')
    await vi.waitFor(() => expect(screen.getByText(/no coinciden/i)).toBeTruthy())
    expect(pinFijar).not.toHaveBeenCalled()
  })

  it('en modo cambiar pide el actual, luego el nuevo y su repeticion', async () => {
    const onOk = vi.fn()
    render(<KioskLock modo="cambiar" onOk={onOk} onCancelar={() => {}} />)
    teclear('1111')
    teclear('2222')
    teclear('2222')
    await vi.waitFor(() => expect(pinCambiar).toHaveBeenCalledWith('1111', '2222'))
    await vi.waitFor(() => expect(onOk).toHaveBeenCalled())
  })

  it('cuando el backend manda espera, deshabilita el pad', async () => {
    pinVerificar.mockResolvedValue({ ok: false, error: 'Demasiados intentos.', espera_segundos: 30 })
    render(<KioskLock modo="verificar" onOk={() => {}} />)
    teclear('9999')
    await vi.waitFor(() => expect(screen.getByText(/30/)).toBeTruthy())
    expect(screen.getByRole('button', { name: '1' }).disabled).toBe(true)
  })
})
