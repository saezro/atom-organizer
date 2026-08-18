import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('./bridge.js', () => ({
  isServerMode: () => true,
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

describe('KioskScreen', () => {
  beforeEach(() => vi.clearAllMocks())

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

  it('muestra el destino derivado cuando hay carpeta', () => {
    render(<KioskScreen {...baseProps({ carpeta: '/home/pi/vuelo/PLANTA' })} />)
    expect(screen.getByText('/home/pi/vuelo/PLANTA_ORGANIZADO')).toBeInTheDocument()
  })

  it('boton Organizar llama a onOrganizar con origen/destino/estadillo', async () => {
    const onOrganizar = vi.fn()
    render(
      <KioskScreen
        {...baseProps({
          carpeta: '/home/pi/vuelo/PLANTA',
          estadillo: '/home/pi/estadillo.xlsx',
          onOrganizar,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /organizar/i }))
    expect(onOrganizar).toHaveBeenCalledTimes(1)
    expect(onOrganizar).toHaveBeenCalledWith({
      origen: '/home/pi/vuelo/PLANTA',
      destino: '/home/pi/vuelo/PLANTA_ORGANIZADO',
      estadillo: '/home/pi/estadillo.xlsx',
    })
  })

  it('boton Subir en crudo llama a onSubirCrudo con carpeta e inspeccion', async () => {
    const onSubirCrudo = vi.fn()
    const inspeccion = { id: 1, nombre: 'ACME 2026' }
    render(
      <KioskScreen
        {...baseProps({
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion,
          onSubirCrudo,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /subir en crudo/i }))
    expect(onSubirCrudo).toHaveBeenCalledWith({ carpeta: '/home/pi/vuelo/PLANTA', inspeccion })
  })

  it('sin carpeta, ambos botones están deshabilitados', () => {
    render(<KioskScreen {...baseProps({ carpeta: '', inspeccion: { id: 1, nombre: 'X' } })} />)
    expect(screen.getByRole('button', { name: /organizar/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeDisabled()
  })

  it('sin inspeccion, "Subir en crudo" deshabilitado pero "Organizar" no depende de eso', () => {
    render(<KioskScreen {...baseProps({ carpeta: '/x/y', inspeccion: null })} />)
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /organizar/i })).not.toBeDisabled()
  })

  it('con busy=true, ambos botones deshabilitados aunque haya carpeta e inspeccion', () => {
    render(
      <KioskScreen
        {...baseProps({ carpeta: '/x/y', inspeccion: { id: 1, nombre: 'X' }, busy: true })}
      />
    )
    expect(screen.getByRole('button', { name: /organizar/i })).toBeDisabled()
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

  it('el campo de estadillo existe, empieza vacío y se puede dejar vacío sin deshabilitar Organizar', () => {
    render(<KioskScreen {...baseProps({ estadillo: '' })} />)
    expect(screen.getByRole('button', { name: /organizar/i })).not.toBeDisabled()
  })
})
