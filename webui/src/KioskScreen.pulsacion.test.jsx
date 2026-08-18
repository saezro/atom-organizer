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
    onAbrirCompleta: vi.fn(),
    ...overrides,
  }
}

describe('KioskScreen — activacion por toque en modo servidor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isServerModeMock.mockReturnValue(true)
  })

  it('en modo servidor, "Organizar" se activa al soltar, sin esperas', () => {
    render(<KioskScreen {...baseProps()} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    // Pulsar todavia no activa: la accion salta al soltar, no al tocar, para
    // que un roce accidental no dispare nada.
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()

    fireEvent.pointerUp(boton, { clientY: 100 })
    // Sin timers de por medio: si esto necesitase esperar, seria un bug.
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })

  it('en modo servidor, mover el dedo NO cancela en los botones del kiosco', () => {
    render(<KioskScreen {...baseProps()} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    fireEvent.pointerMove(boton, { clientY: 10 }) // en el selector esto seria un scroll
    fireEvent.pointerUp(boton, { clientY: 10 })

    // Aqui no hay scroll que desambiguar, asi que el temblor del dedo sobre el
    // panel resistivo no puede dejar el boton sin responder.
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })

  it('en escritorio, un click normal sobre "Organizar" dispara la accion de inmediato', async () => {
    isServerModeMock.mockReturnValue(false)
    render(<KioskScreen {...baseProps()} />)
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })
})
