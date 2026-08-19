import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Modo escritorio (tactil=false → BotonToque usa <button onClick>), igual
// que el resto de la suite del kiosco.
vi.mock('./bridge.js', () => ({
  api: {
    pickFolder: vi.fn(() => Promise.resolve('/home/pi/carpeta-elegida')),
    pickFile: vi.fn(() => Promise.resolve('/home/pi/estadillo.xlsx')),
  },
}))

import KioskTareas from './KioskTareas.jsx'
import { SECTIONS } from './schema.js'

function baseProps(overrides = {}) {
  return {
    tactil: false,
    busy: false,
    onEjecutar: vi.fn(),
    onVolver: vi.fn(),
    ...overrides,
  }
}

describe('KioskTareas — lista', () => {
  beforeEach(() => vi.clearAllMocks())

  it('pinta la primera página con tareas de SECTIONS, sin hardcodear la lista', () => {
    render(<KioskTareas {...baseProps()} />)
    // La primera sección del schema es AEROTOOLS: sus dos primeras tareas
    // deben verse en la página 1 (separador + 4 filas = página completa).
    const primerBlock = SECTIONS.aerotools.blocks[0]
    expect(screen.getByText(SECTIONS.aerotools.label)).toBeInTheDocument()
    expect(screen.getByTestId(`kiosk-tarea-${primerBlock.task}`)).toBeInTheDocument()
  })

  it('pagina: no caben las 11 tareas de golpe y el indicador avanza al tocar "abajo"', async () => {
    const user = userEvent.setup()
    render(<KioskTareas {...baseProps()} />)
    // Con 2 secciones + 11 tareas hay más de 4 filas por página → paginación visible.
    expect(screen.getByTestId('kiosk-tareas-pagina')).toHaveTextContent('1/')
    // Alguna tarea de OTROS (última sección) no está en la página 1.
    const ultimaTareaOtros = SECTIONS.otros.blocks[SECTIONS.otros.blocks.length - 1]
    expect(screen.queryByTestId(`kiosk-tarea-${ultimaTareaOtros.task}`)).not.toBeInTheDocument()

    // Avanza páginas hasta encontrarla (recorre TODO el listado, no una fija).
    let intentos = 0
    while (!screen.queryByTestId(`kiosk-tarea-${ultimaTareaOtros.task}`) && intentos < 10) {
      await user.click(screen.getByTestId('kiosk-tareas-abajo'))
      intentos += 1
    }
    expect(screen.getByTestId(`kiosk-tarea-${ultimaTareaOtros.task}`)).toBeInTheDocument()
  })

  it('el botón "Atrás" de la lista llama a onVolver', async () => {
    const user = userEvent.setup()
    const onVolver = vi.fn()
    render(<KioskTareas {...baseProps({ onVolver })} />)
    await user.click(screen.getByText('← Atrás'))
    expect(onVolver).toHaveBeenCalled()
  })
})

describe('KioskTareas — formulario', () => {
  beforeEach(() => vi.clearAllMocks())

  it('elegir una tarea abre su formulario con sus campos', async () => {
    const user = userEvent.setup()
    render(<KioskTareas {...baseProps()} />)
    const block = SECTIONS.aerotools.blocks[0] // do_extraction
    await user.click(screen.getByTestId(`kiosk-tarea-${block.task}`))

    expect(screen.getByText(block.title)).toBeInTheDocument()
    for (const f of block.fields) {
      expect(screen.getByText(f.label)).toBeInTheDocument()
    }
    expect(screen.getByTestId('kiosk-tarea-ejecutar')).toBeInTheDocument()
  })

  it('"Atrás" del formulario vuelve a la lista, no llama a onVolver', async () => {
    const user = userEvent.setup()
    const onVolver = vi.fn()
    render(<KioskTareas {...baseProps({ onVolver })} />)
    const block = SECTIONS.aerotools.blocks[0]
    await user.click(screen.getByTestId(`kiosk-tarea-${block.task}`))
    await user.click(screen.getByText('← Atrás'))

    expect(onVolver).not.toHaveBeenCalled()
    expect(screen.getByText('Tareas')).toBeInTheDocument()
    expect(screen.getByTestId(`kiosk-tarea-${block.task}`)).toBeInTheDocument()
  })

  it('el botón de carpeta llama al picker del bridge y refleja la ruta elegida', async () => {
    const user = userEvent.setup()
    const { api } = await import('./bridge.js')
    render(<KioskTareas {...baseProps()} />)
    const block = SECTIONS.aerotools.blocks[0] // do_extraction: folder es el primer campo
    await user.click(screen.getByTestId(`kiosk-tarea-${block.task}`))

    const campoFolder = block.fields.find((f) => f.type === 'folder')
    await user.click(screen.getByTestId(`kiosk-campo-${campoFolder.name}`))

    expect(api.pickFolder).toHaveBeenCalled()
    expect(await screen.findByText('/home/pi/carpeta-elegida')).toBeInTheDocument()
  })

  it('Ejecutar llama a onEjecutar con el task y los params por defecto del schema', async () => {
    const user = userEvent.setup()
    const onEjecutar = vi.fn()
    render(<KioskTareas {...baseProps({ onEjecutar })} />)
    const block = SECTIONS.aerotools.blocks[0] // do_extraction
    await user.click(screen.getByTestId(`kiosk-tarea-${block.task}`))
    await user.click(screen.getByTestId('kiosk-tarea-ejecutar'))

    expect(onEjecutar).toHaveBeenCalledTimes(1)
    const [task, params] = onEjecutar.mock.calls[0]
    expect(task).toBe(block.task)
    // Defaults del schema para do_extraction: los bool viajan tal cual y los
    // folder/file arrancan vacíos (nadie ha elegido ruta en este test).
    expect(params).toMatchObject({
      folder: '',
      output_folder: '',
      estad: '',
      auto: true,
      pre_extraction: true,
      extraction: true,
    })
  })

  it('Ejecutar queda deshabilitado si busy=true', async () => {
    const user = userEvent.setup()
    render(<KioskTareas {...baseProps({ busy: true })} />)
    const block = SECTIONS.aerotools.blocks[0]
    await user.click(screen.getByTestId(`kiosk-tarea-${block.task}`))
    expect(screen.getByTestId('kiosk-tarea-ejecutar')).toBeDisabled()
  })
})
