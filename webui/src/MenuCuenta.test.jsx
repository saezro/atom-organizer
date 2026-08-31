import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import MenuCuenta from './MenuCuenta.jsx'

describe('MenuCuenta', () => {
  it('muestra la inicial y el email de la cuenta al abrir el menú', () => {
    render(<MenuCuenta cuenta={{ email: 'ana@aerotools.es', nombre: 'Ana' }} invitado={false} onAjustes={() => {}} onSalir={() => {}} />)
    const avatar = screen.getByTestId('cuenta-avatar')
    expect(avatar).toHaveTextContent('A')
    expect(avatar).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(avatar)
    expect(avatar).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('ana@aerotools.es')).toBeInTheDocument()
  })

  it('el invitado ve «Sin cuenta» como cabecera', () => {
    render(<MenuCuenta cuenta={null} invitado onAjustes={() => {}} onSalir={() => {}} />)
    fireEvent.click(screen.getByTestId('cuenta-avatar'))
    expect(screen.getByText('Sin cuenta')).toBeInTheDocument()
  })

  it('«Ajustes» y «Cerrar sesión» avisan hacia arriba y cierran el menú', () => {
    const onAjustes = vi.fn()
    const onSalir = vi.fn()
    render(<MenuCuenta cuenta={{ email: 'a@b.es' }} invitado={false} onAjustes={onAjustes} onSalir={onSalir} />)

    fireEvent.click(screen.getByTestId('cuenta-avatar'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Ajustes' }))
    expect(onAjustes).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('cuenta-menu')).toBeNull()

    fireEvent.click(screen.getByTestId('cuenta-avatar'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Cerrar sesión' }))
    expect(onSalir).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('cuenta-menu')).toBeNull()
  })

  it('se cierra con Escape y con clic fuera', () => {
    render(<MenuCuenta cuenta={{ email: 'a@b.es' }} invitado={false} onAjustes={() => {}} onSalir={() => {}} />)
    const avatar = screen.getByTestId('cuenta-avatar')

    fireEvent.click(avatar)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('cuenta-menu')).toBeNull()

    fireEvent.click(avatar)
    expect(screen.getByTestId('cuenta-menu')).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByTestId('cuenta-menu')).toBeNull()
  })
})
