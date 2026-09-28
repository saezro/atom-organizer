import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// isServerMode se mockea por caso: la mayoria de los tests aqui necesitan
// modo servidor (activacion por toque), el ultimo prueba explicitamente
// escritorio.
const isServerModeMock = vi.fn(() => true)
vi.mock('./bridge.js', () => ({
  isServerMode: () => isServerModeMock(),
  api: {},
}))

import KioskScreen from './KioskScreen.jsx'

function baseProps(overrides = {}) {
  return {
    status: { email: 'rebeca@aerotools.es', picture: null },
    carpeta: '/home/pi/vuelo/PLANTA',
    onPickCarpeta: vi.fn(),
    inspecciones: [],
    inspeccion: null,
    onSelectInspeccion: vi.fn(),
    estadillo: '',
    onEstadillo: vi.fn(),
    onOrganizar: vi.fn(),
    onSubirCrudo: vi.fn(),
    busy: false,
    progreso: null,
    ...overrides,
  }
}

describe('KioskScreen — activacion por toque en modo servidor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isServerModeMock.mockReturnValue(true)
  })

  it('en modo servidor, "Organizar" se activa al soltar, sin esperas', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    // Pulsar todavia no activa: la accion salta al soltar, no al tocar, para
    // que un roce accidental no dispare nada.
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()

    fireEvent.pointerUp(boton, { clientY: 100 })
    // Sin timers de por medio: si esto necesitase esperar, seria un bug.
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })

  it('en modo servidor, el temblor del resistivo NO cancela en los botones del kiosco', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    // Este boton no lleva `cancelarAlMover` (no hay scroll que desambiguar):
    // el temblor tipico del resistivo se tolera y solo se descarta un
    // arrastre real, mucho mayor.
    fireEvent.pointerMove(boton, { clientY: 92 })
    fireEvent.pointerUp(boton, { clientY: 92 })

    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })

  it('en modo servidor, un arrastre real SI cancela en los botones del kiosco', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    fireEvent.pointerMove(boton, { clientY: 10 })
    fireEvent.pointerUp(boton, { clientY: 10 })

    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
  })

  it('en escritorio, un click normal sobre "Organizar" dispara la accion de inmediato', async () => {
    isServerModeMock.mockReturnValue(false)
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })
})
