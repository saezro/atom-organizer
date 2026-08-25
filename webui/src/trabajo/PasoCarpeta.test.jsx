import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: { pickFolder: vi.fn(), folderIsEmpty: vi.fn() },
}))
import { api } from '../bridge'
import PasoCarpeta from './PasoCarpeta'

beforeEach(() => vi.clearAllMocks())

describe('PasoCarpeta', () => {
  it('propaga la carpeta elegida', async () => {
    api.pickFolder.mockResolvedValue('/datos/vuelo')
    api.folderIsEmpty.mockResolvedValue({ empty: true })
    const onChange = vi.fn()
    render(<PasoCarpeta label="Carpeta del vuelo" value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('/datos/vuelo'))
  })

  it('no llama a onChange si el operario cancela el diálogo', async () => {
    api.pickFolder.mockResolvedValue(null)
    const onChange = vi.fn()
    render(<PasoCarpeta label="X" value="" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(api.pickFolder).toHaveBeenCalled())
    expect(onChange).not.toHaveBeenCalled()
  })

  it('avisa si la carpeta no está vacía y se pidió el aviso', async () => {
    api.pickFolder.mockResolvedValue('/datos/llena')
    api.folderIsEmpty.mockResolvedValue({ empty: false, count: 12 })
    render(<PasoCarpeta label="Carpeta final" value="" onChange={() => {}} avisoNoVacia />)
    fireEvent.click(screen.getByRole('button'))
    expect(await screen.findByText(/12/)).toBeTruthy()
  })

  it('sin avisoNoVacia no consulta folderIsEmpty', async () => {
    api.pickFolder.mockResolvedValue('/datos/vuelo')
    render(<PasoCarpeta label="X" value="" onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(api.pickFolder).toHaveBeenCalled())
    expect(api.folderIsEmpty).not.toHaveBeenCalled()
  })

  it('permite escribir/pegar la ruta a mano (fallback del diálogo nativo)', () => {
    const onChange = vi.fn()
    render(<PasoCarpeta label="Carpeta del vuelo" value="" onChange={onChange} />)
    const input = screen.getByRole('textbox')
    expect(input).not.toHaveAttribute('readOnly')
    fireEvent.change(input, { target: { value: 'D:\\vuelos\\pegado' } })
    expect(onChange).toHaveBeenCalledWith('D:\\vuelos\\pegado')
  })

  it('disabled no permite teclear la ruta', () => {
    const onChange = vi.fn()
    render(<PasoCarpeta label="Carpeta del vuelo" value="" onChange={onChange} disabled />)
    const input = screen.getByRole('textbox')
    expect(input).toHaveAttribute('readOnly')
    fireEvent.change(input, { target: { value: 'no debería pasar' } })
    expect(onChange).not.toHaveBeenCalled()
  })
})
