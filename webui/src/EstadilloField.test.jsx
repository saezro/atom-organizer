import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// `EstadilloField.jsx` importa `./bridge` (sin extensión); se mockea tal cual
// para poder controlar `api.pickFile()` (el único punto de selección en
// kiosco, vía el botón "Elegir…").
vi.mock('./bridge', () => ({
  api: { pickFile: vi.fn(async () => '/media/pi/USB/estadillo.xlsx') },
}))

import { api } from './bridge'
import EstadilloField from './EstadilloField.jsx'

describe('EstadilloField en kiosco (tactil)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('el input queda de solo lectura: no se puede teclear un nombre a mano', async () => {
    const onChange = vi.fn()
    render(<EstadilloField value={[]} onChange={onChange} tactil />)
    const input = screen.getByPlaceholderText(/si se indica, organiza por planta/i)
    expect(input).toHaveAttribute('readonly')
    await userEvent.type(input, 'inventado.xlsx')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('con varios estadillos, las filas también quedan de solo lectura', async () => {
    const onChange = vi.fn()
    render(
      <EstadilloField
        value={['/a/uno.xlsx', '/a/dos.xlsx']}
        onChange={onChange}
        tactil
      />
    )
    const inputs = screen.getAllByDisplayValue(/xlsx$/)
    for (const input of inputs) {
      expect(input).toHaveAttribute('readonly')
    }
    await userEvent.type(inputs[0], 'x')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('"Elegir…" sí funciona en kiosco: la selección se hace tocando, no tecleando', async () => {
    const onChange = vi.fn()
    render(<EstadilloField value={[]} onChange={onChange} tactil />)
    await userEvent.click(screen.getByRole('button', { name: /elegir/i }))
    expect(api.pickFile).toHaveBeenCalled()
    expect(onChange).toHaveBeenCalledWith(['/media/pi/USB/estadillo.xlsx'])
  })
})

describe('EstadilloField en escritorio (tactil falsy) — comportamiento intacto', () => {
  beforeEach(() => vi.clearAllMocks())

  it('se sigue pudiendo teclear un nombre a mano', async () => {
    const onChange = vi.fn()
    render(<EstadilloField value={[]} onChange={onChange} />)
    const input = screen.getByPlaceholderText(/si se indica, organiza por planta/i)
    expect(input).not.toHaveAttribute('readonly')
    await userEvent.type(input, 'x')
    expect(onChange).toHaveBeenCalledWith(['x'])
  })

  it('con varios estadillos, las filas siguen editables a mano', async () => {
    const onChange = vi.fn()
    render(<EstadilloField value={['/a/uno.xlsx', '/a/dos.xlsx']} onChange={onChange} />)
    const inputs = screen.getAllByDisplayValue(/xlsx$/)
    for (const input of inputs) {
      expect(input).not.toHaveAttribute('readonly')
    }
    await userEvent.type(inputs[0], 'x')
    expect(onChange).toHaveBeenCalled()
  })
})
