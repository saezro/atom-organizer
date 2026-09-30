import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const listarPerfilesMock = vi.fn()
const activarPerfilMock = vi.fn()
const borrarPerfilMock = vi.fn()

vi.mock('../bridge.js', () => ({
  api: {
    listarPerfiles: (...args) => listarPerfilesMock(...args),
    activarPerfil: (...args) => activarPerfilMock(...args),
    borrarPerfil: (...args) => borrarPerfilMock(...args),
  },
}))

import PantallaEntrada from './PantallaEntrada.jsx'

const props = () => ({
  onGoogle: vi.fn(),
  onInvitado: vi.fn(),
  cargando: false,
  error: null,
})

beforeEach(() => {
  listarPerfilesMock.mockReset()
  activarPerfilMock.mockReset()
  borrarPerfilMock.mockReset()
  listarPerfilesMock.mockResolvedValue([])
})

describe('PantallaEntrada', () => {
  it('sin perfiles pinta los dos botones de siempre', async () => {
    render(<PantallaEntrada {...props()} />)
    await waitFor(() => expect(listarPerfilesMock).toHaveBeenCalled())
    expect(screen.getByText('Entrar con Google')).toBeTruthy()
    expect(screen.getByText('Entrar sin cuenta')).toBeTruthy()
  })

  it('con perfiles pinta sus nombres y el tile de añadir cuenta', async () => {
    listarPerfilesMock.mockResolvedValue([
      { email: 'a@x.com', nombre: 'Ana', picture: '', modo: 'google', tiene_credencial: true },
      { email: 'b@x.com', nombre: 'Bruno', picture: '', modo: 'google', tiene_credencial: true },
    ])
    render(<PantallaEntrada {...props()} />)
    expect(await screen.findByText('Ana')).toBeTruthy()
    expect(screen.getByText('Bruno')).toBeTruthy()
    expect(screen.getByLabelText('Añadir cuenta')).toBeTruthy()
    expect(screen.queryByText('Entrar con Google')).toBeNull()
  })

  it('click en perfil con activarPerfil ok:true llama a activarPerfil con ese email', async () => {
    listarPerfilesMock.mockResolvedValue([
      { email: 'a@x.com', nombre: 'Ana', picture: '', modo: 'google', tiene_credencial: true },
    ])
    activarPerfilMock.mockResolvedValue({ ok: true })
    const p = props()
    const onPerfilActivado = vi.fn()
    render(<PantallaEntrada {...p} onPerfilActivado={onPerfilActivado} />)
    const tile = await screen.findByLabelText('Entrar como Ana')
    await userEvent.click(tile)
    await waitFor(() => expect(activarPerfilMock).toHaveBeenCalledWith('a@x.com'))
    await waitFor(() => expect(onPerfilActivado).toHaveBeenCalledTimes(1))
    expect(p.onGoogle).not.toHaveBeenCalled()
  })

  it('click en perfil con activarPerfil ok:false hace relogin silencioso (onGoogle) sin pintar error', async () => {
    listarPerfilesMock.mockResolvedValue([
      { email: 'a@x.com', nombre: 'Ana', picture: '', modo: 'google', tiene_credencial: true },
    ])
    activarPerfilMock.mockResolvedValue({ ok: false })
    const p = props()
    render(<PantallaEntrada {...p} />)
    const tile = await screen.findByLabelText('Entrar como Ana')
    await userEvent.click(tile)
    await waitFor(() => expect(p.onGoogle).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/error/i)).toBeNull()
  })

  it('Administrar muestra las X y la X llama a borrarPerfil', async () => {
    listarPerfilesMock.mockResolvedValue([
      { email: 'a@x.com', nombre: 'Ana', picture: '', modo: 'google', tiene_credencial: true },
    ])
    render(<PantallaEntrada {...props()} />)
    await screen.findByText('Ana')
    expect(screen.queryByLabelText('Quitar Ana')).toBeNull()

    await userEvent.click(screen.getByText('Administrar'))
    const quitar = await screen.findByLabelText('Quitar Ana')
    await userEvent.click(quitar)
    await waitFor(() => expect(borrarPerfilMock).toHaveBeenCalledWith('a@x.com'))
  })

  it('perfil modo invitado llama a onInvitado al hacer click', async () => {
    listarPerfilesMock.mockResolvedValue([
      { email: '', nombre: '', picture: '', modo: 'invitado', tiene_credencial: false },
    ])
    const p = props()
    render(<PantallaEntrada {...p} />)
    const tile = await screen.findByLabelText('Entrar como Invitado')
    await userEvent.click(tile)
    expect(p.onInvitado).toHaveBeenCalledTimes(1)
    expect(activarPerfilMock).not.toHaveBeenCalled()
  })

  it('picture vacía pinta la inicial', async () => {
    listarPerfilesMock.mockResolvedValue([
      { email: 'ana@x.com', nombre: 'Ana', picture: '', modo: 'google', tiene_credencial: true },
    ])
    render(<PantallaEntrada {...props()} />)
    const tile = await screen.findByLabelText('Entrar como Ana')
    expect(tile.querySelector('img')).toBeNull()
    expect(tile.textContent).toContain('A')
  })
})

describe('PantallaEntrada login con contraseña', () => {
  it('pinta el formulario y Google sigue presente', async () => {
    render(<PantallaEntrada {...props()} onPassword={vi.fn()} />)
    await waitFor(() => expect(listarPerfilesMock).toHaveBeenCalled())
    expect(screen.getByLabelText('Usuario o correo')).toBeTruthy()
    expect(screen.getByLabelText('Contraseña')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Entrar' })).toBeTruthy()
    expect(screen.getByText('Entrar con Google')).toBeTruthy()
  })

  it('envía usuario y contraseña con click y con Enter', async () => {
    const onPassword = vi.fn().mockResolvedValue(true)
    render(<PantallaEntrada {...props()} onPassword={onPassword} />)
    await userEvent.type(screen.getByLabelText('Usuario o correo'), 'ana')
    await userEvent.type(screen.getByLabelText('Contraseña'), 'secreto')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    await waitFor(() => expect(onPassword).toHaveBeenCalledWith('ana', 'secreto'))
    await userEvent.type(screen.getByLabelText('Contraseña'), '{Enter}')
    await waitFor(() => expect(onPassword).toHaveBeenCalledTimes(2))
  })

  it('muestra "Entrando…" y deshabilita mientras envía', async () => {
    let resolver
    const onPassword = vi.fn(() => new Promise((r) => { resolver = r }))
    render(<PantallaEntrada {...props()} onPassword={onPassword} />)
    await userEvent.type(screen.getByLabelText('Usuario o correo'), 'ana')
    await userEvent.type(screen.getByLabelText('Contraseña'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('Entrando…')).toBeTruthy()
    resolver(false)
    await waitFor(() => expect(screen.queryByText('Entrando…')).toBeNull())
  })

  it('muestra el error recibido y no envía con campos vacíos', async () => {
    const onPassword = vi.fn()
    render(<PantallaEntrada {...props()} error="Credenciales inválidas" onPassword={onPassword} />)
    expect(screen.getByText('Credenciales inválidas')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Entrar' }).disabled).toBe(true)
    expect(onPassword).not.toHaveBeenCalled()
  })
})
