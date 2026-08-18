import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Esta suite prueba la estructura/flujo del componente con click normal
// (modo escritorio). El comportamiento de pulsacion larga en modo servidor
// se cubre aparte en KioskScreen.pulsacion.test.jsx.
vi.mock('./bridge.js', () => ({
  isServerMode: () => false,
  api: {},
}))

import KioskScreen, { derivarDestino } from './KioskScreen.jsx'

const inspecciones = [
  { id: 1, nombre: 'ACME 2026' },
  { id: 2, nombre: 'BETA 2025' },
]

function baseProps(overrides = {}) {
  return {
    status: { email: 'rebeca@aerotools.es', picture: null },
    carpeta: '/home/pi/vuelo/PLANTA',
    onPickCarpeta: vi.fn(),
    inspecciones,
    inspeccion: null,
    onSelectInspeccion: vi.fn(),
    estadillo: '',
    onEstadillo: vi.fn(),
    onOrganizar: vi.fn(),
    onSubirCrudo: vi.fn(),
    busy: false,
    progreso: null,
    onAbrirCompleta: vi.fn(),
    ...overrides,
  }
}

describe('derivarDestino', () => {
  it('añade sufijo _ORGANIZADO', () => {
    expect(derivarDestino('/home/pi/vuelo/PLANTA')).toBe('/home/pi/vuelo/PLANTA_ORGANIZADO')
  })

  it('quita la barra final antes de derivar', () => {
    expect(derivarDestino('/home/pi/vuelo/PLANTA/')).toBe('/home/pi/vuelo/PLANTA_ORGANIZADO')
  })

  it('cadena vacía devuelve vacío', () => {
    expect(derivarDestino('')).toBe('')
  })

  it('null devuelve vacío', () => {
    expect(derivarDestino(null)).toBe('')
  })

  it('undefined devuelve vacío', () => {
    expect(derivarDestino(undefined)).toBe('')
  })

  it('soporta rutas Windows con backslash', () => {
    expect(derivarDestino('C:\\vuelo\\PLANTA')).toBe('C:\\vuelo\\PLANTA_ORGANIZADO')
  })

  it('soporta rutas Windows con backslash final', () => {
    expect(derivarDestino('C:\\vuelo\\PLANTA\\')).toBe('C:\\vuelo\\PLANTA_ORGANIZADO')
  })
})

describe('KioskScreen — paso 1 (menú)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('muestra los botones "Organizar" y "Subir en crudo", sin campos del paso 2', () => {
    render(<KioskScreen {...baseProps()} />)
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeInTheDocument()
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByText(/estadillo/i)).not.toBeInTheDocument()
  })

  it('muestra "Sin sesión" cuando status es null', () => {
    render(<KioskScreen {...baseProps({ status: null })} />)
    expect(screen.getByText(/sin sesión/i)).toBeInTheDocument()
  })

  it('renderiza avatar con picture si existe', () => {
    render(
      <KioskScreen
        {...baseProps({ status: { email: 'rebeca@aerotools.es', picture: 'http://x/foto.png' } })}
      />
    )
    const img = screen.getByRole('img', { name: /rebeca@aerotools.es/i })
    expect(img).toHaveAttribute('src', 'http://x/foto.png')
  })

  it('sin picture pero con email muestra avatar de respaldo con la inicial, sin <img>', () => {
    render(<KioskScreen {...baseProps({ status: { email: 'rebeca@aerotools.es', picture: null } })} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('R')).toBeInTheDocument()
  })

  it('pulsar el avatar llama a onAbrirCompleta', async () => {
    const onAbrirCompleta = vi.fn()
    render(<KioskScreen {...baseProps({ onAbrirCompleta })} />)
    await userEvent.click(screen.getByTestId('kiosk-avatar'))
    expect(onAbrirCompleta).toHaveBeenCalled()
  })

  it('con busy=true, los dos botones del menú están deshabilitados', () => {
    render(<KioskScreen {...baseProps({ busy: true })} />)
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeDisabled()
  })

  it('sin progreso no muestra barra', () => {
    render(<KioskScreen {...baseProps({ progreso: null })} />)
    expect(screen.queryByTestId('kiosk-progreso')).not.toBeInTheDocument()
  })

  it('con progreso muestra fase y pct', () => {
    render(<KioskScreen {...baseProps({ progreso: { fase: 'Copiando', pct: 42 } })} />)
    expect(screen.getByText(/copiando/i)).toBeInTheDocument()
    expect(screen.getByText(/42/)).toBeInTheDocument()
  })

  it('pulsar "Organizar" lleva al paso 2 de organizar (sin selector de inspección)', async () => {
    render(<KioskScreen {...baseProps()} />)
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
    expect(screen.getByText(/estadillo \(opcional\)/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /← atrás/i })).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('pulsar "Subir en crudo" lleva al paso 2 de subir (sin estadillo)', async () => {
    render(<KioskScreen {...baseProps()} />)
    await userEvent.click(screen.getByRole('button', { name: /subir en crudo/i }))
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /← atrás/i })).toBeInTheDocument()
    expect(screen.queryByText(/estadillo/i)).not.toBeInTheDocument()
  })
})

describe('KioskScreen — paso 2 (organizar)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('muestra el destino derivado cuando hay carpeta', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/home/pi/vuelo/PLANTA' })} />)
    expect(screen.getByText('/home/pi/vuelo/PLANTA_ORGANIZADO')).toBeInTheDocument()
  })

  it('sin carpeta no muestra destino y el botón "Organizar" está deshabilitado', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '' })} />)
    expect(screen.queryByText(/_ORGANIZADO/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeDisabled()
  })

  it('botón "Organizar" llama a onOrganizar con origen/destino/estadillo', async () => {
    const onOrganizar = vi.fn()
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'organizar',
          carpeta: '/home/pi/vuelo/PLANTA',
          estadillo: '/home/pi/estadillo.xlsx',
          onOrganizar,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(onOrganizar).toHaveBeenCalledTimes(1)
    expect(onOrganizar).toHaveBeenCalledWith({
      origen: '/home/pi/vuelo/PLANTA',
      destino: '/home/pi/vuelo/PLANTA_ORGANIZADO',
      estadillo: '/home/pi/estadillo.xlsx',
    })
  })

  it('el campo de estadillo empieza vacío y no deshabilita "Organizar"', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', estadillo: '' })} />)
    expect(screen.getByPlaceholderText(/ruta del estadillo/i)).toHaveValue('')
    expect(screen.getByRole('button', { name: /^organizar$/i })).not.toBeDisabled()
  })

  it('con busy=true, "Elegir carpeta…" y "Organizar" están deshabilitados', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/x/y', busy: true })} />)
    expect(screen.getByRole('button', { name: /elegir carpeta/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeDisabled()
  })

  it('"← Atrás" vuelve al paso 1 y muestra de nuevo el menú', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar' })} />)
    await userEvent.click(screen.getByRole('button', { name: /← atrás/i }))
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeInTheDocument()
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
  })
})

describe('KioskScreen — paso 2 (subir en crudo)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('no muestra el destino derivado', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'subir', carpeta: '/home/pi/vuelo/PLANTA' })} />)
    expect(screen.queryByText(/_ORGANIZADO/)).not.toBeInTheDocument()
  })

  it('sin carpeta o sin inspección, el botón "Subir" está deshabilitado', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'subir', carpeta: '', inspeccion: null })} />)
    expect(screen.getByRole('button', { name: /^subir$/i })).toBeDisabled()
  })

  it('botón "Subir" llama a onSubirCrudo con carpeta e inspección', async () => {
    const onSubirCrudo = vi.fn()
    const inspeccion = { id: 1, nombre: 'ACME 2026' }
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion,
          onSubirCrudo,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^subir$/i }))
    expect(onSubirCrudo).toHaveBeenCalledTimes(1)
    expect(onSubirCrudo).toHaveBeenCalledWith({ carpeta: '/home/pi/vuelo/PLANTA', inspeccion })
  })

  it('con busy=true, "Elegir carpeta…" y "Subir" están deshabilitados', () => {
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/x/y',
          inspeccion: { id: 1, nombre: 'X' },
          busy: true,
        })}
      />
    )
    expect(screen.getByRole('button', { name: /elegir carpeta/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^subir$/i })).toBeDisabled()
  })
})
