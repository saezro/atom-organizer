import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AvisoSesion from './AvisoSesion.jsx'

describe('AvisoSesion', () => {
  it('no pinta nada cuando la credencial es válida', () => {
    const { container } = render(<AvisoSesion estado="ok" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('avisa de sesión cerrada y ofrece emparejar', () => {
    const onEmparejar = vi.fn()
    render(<AvisoSesion estado="sin-credencial" onEmparejar={onEmparejar} />)
    expect(screen.getByText(/SESIÓN CERRADA/i)).toBeTruthy()
    screen.getByRole('button', { name: /emparejar/i }).click()
    expect(onEmparejar).toHaveBeenCalled()
  })

  it('distingue el fallo de red y no ofrece emparejar', () => {
    render(<AvisoSesion estado="sin-conexion" onEmparejar={() => {}} />)
    expect(screen.getByText(/SIN CONEXIÓN/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /emparejar/i })).toBeNull()
  })

  it('deja cerrar el aviso para seguir trabajando', () => {
    const onCerrar = vi.fn()
    render(<AvisoSesion estado="sin-credencial" onCerrar={onCerrar} />)
    screen.getByRole('button', { name: /seguir/i }).click()
    expect(onCerrar).toHaveBeenCalled()
  })

  it('dice cuántas subidas quedan en cola', () => {
    render(<AvisoSesion estado="sin-credencial" pendientes={3} />)
    expect(screen.getByText(/3/)).toBeTruthy()
  })
})
