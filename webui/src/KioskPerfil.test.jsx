import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

const puente = {
  isServerMode: () => false,
  api: {
    cloudLogout: vi.fn().mockResolvedValue({}),
    cloudStatus: vi.fn().mockResolvedValue({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
    pinEstado: vi.fn().mockResolvedValue({ ok: true, hay_pin: true }),
    pinVerificar: vi.fn().mockResolvedValue({ ok: true }),
    pinFijar: vi.fn().mockResolvedValue({ ok: true }),
    pinCambiar: vi.fn().mockResolvedValue({ ok: true }),
  },
}
vi.mock('./bridge.js', () => puente)
vi.mock('./bridge', () => puente)

const { default: KioskScreen } = await import('./KioskScreen.jsx')

const STATUS = {
  logged_in: true,
  email: 'rebeca@aerotools.es',
  picture: 'https://ejemplo/foto.jpg',
  validada_en: 1756000000,
  pendientes: 3,
}

function pintar(status = STATUS) {
  return render(
    <KioskScreen
      status={status}
      inspecciones={[]}
      busy={false}
      accionInicial="cuenta"
      onRefreshStatus={() => {}}
    />
  )
}

describe('pantalla de perfil del kiosco', () => {
  it('muestra la foto grande y el email', () => {
    pintar()
    const foto = screen.getByTestId('kiosk-perfil-foto')
    expect(foto.getAttribute('src')).toBe('https://ejemplo/foto.jpg')
    expect(screen.getByText('rebeca@aerotools.es')).toBeTruthy()
  })

  it('sin foto cae a la inicial del email', () => {
    pintar({ ...STATUS, picture: null })
    expect(screen.queryByTestId('kiosk-perfil-foto')).toBeNull()
    expect(screen.getByText('R')).toBeTruthy()
  })

  it('muestra las subidas pendientes', () => {
    pintar()
    expect(screen.getByText('3')).toBeTruthy()  // exacto: /3/ tambien casa con el '3' de la hora de ultimo acceso
  })

  it('sin ultimo acceso registrado lo dice', () => {
    pintar({ ...STATUS, validada_en: null })
    expect(screen.getByText(/sin registrar/i)).toBeTruthy()
  })

  it('tiene los botones de cambiar PIN y cerrar sesion', () => {
    pintar()
    expect(screen.getByRole('button', { name: /cambiar pin/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /cerrar sesión/i })).toBeTruthy()
  })

  it('cambiar PIN abre el pad pidiendo el PIN actual', async () => {
    pintar()
    screen.getByRole('button', { name: /cambiar pin/i }).click()
    await vi.waitFor(() => expect(screen.getByText(/pin actual/i)).toBeTruthy())
  })

  it('muestra el nombre cuando status.nombre viene informado', () => {
    pintar({ ...STATUS, nombre: 'Rebeca García' })
    expect(screen.getByTestId('kiosk-perfil-nombre').textContent).toBe('Rebeca García')
    expect(screen.getByText('rebeca@aerotools.es')).toBeTruthy()
  })

  it('sin nombre no pinta el elemento y el email se sigue viendo', () => {
    pintar({ ...STATUS, nombre: '' })
    expect(screen.queryByTestId('kiosk-perfil-nombre')).toBeNull()
    expect(screen.getByText('rebeca@aerotools.es')).toBeTruthy()
  })

  it('con nombre ausente tampoco pinta el elemento', () => {
    pintar(STATUS)
    expect(screen.queryByTestId('kiosk-perfil-nombre')).toBeNull()
  })
})
