import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// isServerMode se mockea por caso: la mayoria de los tests aqui necesitan
// modo servidor (pulsacion larga), el ultimo prueba explicitamente escritorio.
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

// --pulsacion no esta definida en jsdom, asi que msDePulsacion cae al
// fallback de 700 ms (ver pulsacion.jsx).
const MS = 700

describe('KioskScreen — pulsacion larga en modo servidor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isServerModeMock.mockReturnValue(true)
  })

  it('en modo servidor, pointerDown sobre "Organizar" no dispara de inmediato y si al completarse el temporizador', async () => {
    render(<KioskScreen {...baseProps()} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    // Todavia no ha pasado el tiempo: sigue en el menu (paso 1).
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()

    await act(async () => { await new Promise((r) => setTimeout(r, MS + 150)) })
    fireEvent.pointerUp(boton, { clientY: 100 })

    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })

  it('en modo servidor, mover el puntero durante la pulsacion NO la cancela', async () => {
    render(<KioskScreen {...baseProps()} />)
    const boton = screen.getByRole('button', { name: /^organizar$/i })

    fireEvent.pointerDown(boton, { clientY: 100 })
    fireEvent.pointerMove(boton, { clientY: 10 }) // muy lejos, cancelaria en FolderPicker
    await act(async () => { await new Promise((r) => setTimeout(r, MS + 150)) })
    fireEvent.pointerUp(boton, { clientY: 10 })

    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })

  it('en escritorio, un click normal sobre "Organizar" dispara la accion de inmediato', async () => {
    isServerModeMock.mockReturnValue(false)
    render(<KioskScreen {...baseProps()} />)
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
  })
})
