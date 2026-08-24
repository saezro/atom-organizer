import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import AvisoSesion from './AvisoSesion.jsx'
import KioskScreen from './KioskScreen.jsx'

// El bloque de gating (más abajo) monta `KioskScreen`, que importa
// `./bridge.js` (y `EstadilloField.jsx` importa `./bridge` sin extensión):
// hay que mockear las dos rutas para que ambos imports vean el mismo mock,
// igual que hace `KioskScreen.test.jsx`.
vi.mock('./bridge.js', () => ({
  isServerMode: () => false,
  api: {
    cloudLogout: () => Promise.resolve({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
  },
}))
vi.mock('./bridge', () => ({
  isServerMode: () => false,
  api: {
    cloudLogout: () => Promise.resolve({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
  },
}))

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

// Gating parcial en el kiosco (Task 7): sin credencial se bloquea SOLO elegir
// planta (InspeccionSelector, que depende de ATOM Suite). "Organizar" y
// "Subir en crudo" son 100% locales/en cola y NUNCA deben deshabilitarse por
// falta de sesión.
describe('KioskScreen — gating parcial con credencialOk={false}', () => {
  it('bloquea elegir planta pero deja Organizar y Subir en crudo habilitados', () => {
    const inspecciones = [
      { id: 1, prefijo: 'ACME--PLANTA1--2026--PV', etiqueta: 'ACME PLANTA1 2026', anio: 2026, fase: 'Vuelo' },
    ]
    const baseProps = (overrides = {}) => ({
      status: { email: 'rebeca@aerotools.es', picture: null },
      carpeta: '/home/pi/vuelo/PLANTA',
      onPickCarpeta: vi.fn(),
      inspecciones,
      inspeccion: null,
      onSelectInspeccion: vi.fn(),
      onActualizarInspecciones: vi.fn(),
      estadillo: [],
      onEstadillo: vi.fn(),
      onOrganizar: vi.fn(),
      onSubirCrudo: vi.fn(),
      busy: false,
      progreso: null,
      credencialOk: false,
      ...overrides,
    })

    // (a) el selector de inspecciones queda bloqueado con el aviso.
    const { unmount } = render(
      <KioskScreen {...baseProps({ accionInicial: 'subir', inspeccion: null })} />
    )
    expect(screen.getByTestId('kiosk-sin-credencial')).toBeTruthy()
    expect(screen.getByText(/No se puede elegir planta/i)).toBeTruthy()
    expect(screen.queryByText('ACME PLANTA1 2026')).toBeNull()
    unmount()

    // (b) organizar es 100% local: Organizar y Subir en crudo NUNCA se
    // bloquean por falta de sesión.
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    expect(screen.getByText('Organizar').closest('button')).not.toBeDisabled()
    expect(screen.getByText('Subir en crudo').closest('button')).not.toBeDisabled()
  })
})
