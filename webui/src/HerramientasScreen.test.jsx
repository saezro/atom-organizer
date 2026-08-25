import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('./bridge', () => ({
  api: {
    pickFile: vi.fn(),
    pickFolder: vi.fn(),
  },
}))

import HerramientasScreen from './HerramientasScreen'
import { SECTIONS } from './schema'

describe('HerramientasScreen', () => {
  it('agrupa AEROTOOLS y OTROS EQUIPOS con su propia cabecera', () => {
    render(<HerramientasScreen running={false} onRun={() => {}} />)

    expect(screen.getByText(SECTIONS.aerotools.label)).toBeTruthy()
    expect(screen.getByText(SECTIONS.otros.label)).toBeTruthy()
  })

  it('renderiza todos los bloques de tarea de ambas secciones', () => {
    render(<HerramientasScreen running={false} onRun={() => {}} />)

    const total = SECTIONS.aerotools.blocks.length + SECTIONS.otros.blocks.length
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(total)
  })

  it('pasa running/onRun a cada bloque: Ejecutar llama a onRun con el task', () => {
    const onRun = vi.fn()
    render(<HerramientasScreen running={false} onRun={onRun} />)

    const primerBloque = SECTIONS.aerotools.blocks[0]
    const botones = screen.getAllByRole('button', { name: 'Ejecutar' })
    botones[0].click()

    expect(onRun).toHaveBeenCalledWith(primerBloque.task, expect.any(Object))
  })
})
