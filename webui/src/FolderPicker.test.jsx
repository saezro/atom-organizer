import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('./bridge.js', () => ({
  isServerMode: () => true,
  api: {
    listDir: vi.fn(async (path) => {
      if (!path || path === '/home/rebeca') {
        return {
          ok: true, path: '/home/rebeca', parent: '/home',
          dirs: [{ name: 'VUELOS', path: '/home/rebeca/VUELOS' }],
          files: [{ name: 'estadillo.xlsx', path: '/home/rebeca/estadillo.xlsx', size: 12 }],
        }
      }
      return { ok: true, path, parent: '/home/rebeca', dirs: [], files: [] }
    }),
  },
}))

import FolderPicker from './FolderPicker.jsx'

describe('FolderPicker', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lista las carpetas de la ruta inicial', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    expect(await screen.findByText('VUELOS')).toBeInTheDocument()
  })

  it('en modo carpeta no ofrece ficheros como elegibles', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    expect(screen.queryByText('estadillo.xlsx')).not.toBeInTheDocument()
  })

  it('en modo fichero si los muestra y devuelve la ruta al tocarlo', async () => {
    const onPick = vi.fn()
    render(<FolderPicker mode="file" startPath={null} onPick={onPick} onCancel={() => {}} />)
    await userEvent.click(await screen.findByText('estadillo.xlsx'))
    expect(onPick).toHaveBeenCalledWith('/home/rebeca/estadillo.xlsx')
  })

  it('navegar dentro de una carpeta la lista', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await userEvent.click(await screen.findByText('VUELOS'))
    await waitFor(() => expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS'))
  })

  it('el boton de elegir devuelve la carpeta ACTUAL, no la seleccionada', async () => {
    const onPick = vi.fn()
    render(<FolderPicker mode="folder" startPath={null} onPick={onPick} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    await userEvent.click(screen.getByRole('button', { name: /usar esta carpeta/i }))
    expect(onPick).toHaveBeenCalledWith('/home/rebeca')
  })

  it('cancelar avisa al padre', async () => {
    const onCancel = vi.fn()
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={onCancel} />)
    await screen.findByText('VUELOS')
    await userEvent.click(screen.getByRole('button', { name: /cancelar/i }))
    expect(onCancel).toHaveBeenCalled()
  })
  // El panel resistivo llega como raton: deslizar sobre la lista debe scrollear
  // y NO navegar a la carpeta sobre la que se empezo el gesto.
  it('arrastrar la lista hace scroll y no abre la carpeta', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    const lista = document.querySelector('.picker-lista')
    lista.scrollTop = 0

    fireEvent.pointerDown(lista, { clientY: 200 })
    fireEvent.pointerMove(lista, { clientY: 120 })
    fireEvent.pointerUp(lista, { clientY: 120 })
    fireEvent.click(fila)

    expect(lista.scrollTop).toBe(80)
    expect(api.listDir).toHaveBeenCalledTimes(1) // solo la carga inicial
  })

  it('un toque sin desplazamiento si abre la carpeta', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    const lista = document.querySelector('.picker-lista')

    fireEvent.pointerDown(lista, { clientY: 200 })
    fireEvent.pointerMove(lista, { clientY: 197 }) // por debajo del umbral
    fireEvent.pointerUp(lista, { clientY: 197 })
    fireEvent.click(fila)

    await waitFor(() => expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS'))
  })
})
