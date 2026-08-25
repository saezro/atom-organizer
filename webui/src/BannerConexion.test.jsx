import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import BannerConexion from './BannerConexion.jsx'

describe('BannerConexion', () => {
  it('con sin-conexion pinta el banner avisando que se sigue reintentando', () => {
    render(<BannerConexion estado="sin-conexion" />)
    const banner = screen.getByTestId('kiosk-banner-conexion')
    expect(banner).toHaveTextContent('Sin conexión — reintentando')
  })

  it('con estado ok no pinta nada: hay red, no hay nada que avisar', () => {
    render(<BannerConexion estado="ok" />)
    expect(screen.queryByTestId('kiosk-banner-conexion')).toBeNull()
  })

  it('con sin-credencial no pinta nada: es otra cosa, hay que emparejar, no falta red', () => {
    render(<BannerConexion estado="sin-credencial" />)
    expect(screen.queryByTestId('kiosk-banner-conexion')).toBeNull()
  })

  it('con pendientes muestra el recuento de la cola', () => {
    render(<BannerConexion estado="sin-conexion" pendientes={3} />)
    expect(screen.getByText('3 en cola')).toBeInTheDocument()
  })

  it('sin pendientes no muestra el recuento', () => {
    render(<BannerConexion estado="sin-conexion" pendientes={0} />)
    expect(screen.queryByText(/en cola/)).toBeNull()
  })
})
