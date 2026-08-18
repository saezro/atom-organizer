import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
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

// En modo servidor la fila se activa manteniendo el dedo, no al soltar: el
// panel resistivo es un raton y el click al soltar era justo el evento
// ambiguo. Se espera en tiempo real (--pulsacion no esta definida en jsdom, asi
// que msDePulsacion cae al fallback de 700 ms).
const MS = 700
async function mantener(el, ms = MS + 150) {
  fireEvent.pointerDown(el, { clientY: 100 })
  await act(async () => { await new Promise((r) => setTimeout(r, ms)) })
  fireEvent.pointerUp(el, { clientY: 100 })
}

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

  it('en modo fichero mantener el dedo devuelve la ruta', async () => {
    const onPick = vi.fn()
    render(<FolderPicker mode="file" startPath={null} onPick={onPick} onCancel={() => {}} />)
    await mantener(await screen.findByText('estadillo.xlsx'))
    expect(onPick).toHaveBeenCalledWith('/home/rebeca/estadillo.xlsx')
  })

  it('mantener el dedo sobre una carpeta la lista', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await mantener(await screen.findByText('VUELOS'))
    await waitFor(() => expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS'))
  })

  it('un toque corto NO abre la carpeta', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    await mantener(fila, 150) // suelta mucho antes de completarse
    fireEvent.click(fila)     // el click sintetico del raton tampoco debe abrir
    expect(api.listDir).toHaveBeenCalledTimes(1) // solo la carga inicial
  })

  it('moverse durante la pulsacion la cancela (era un scroll)', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    fireEvent.pointerDown(fila, { clientY: 100 })
    fireEvent.pointerMove(fila, { clientY: 40 })
    await act(async () => { await new Promise((r) => setTimeout(r, MS + 150)) })
    fireEvent.pointerUp(fila, { clientY: 40 })
    expect(api.listDir).toHaveBeenCalledTimes(1)
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
  // y NO navegar a la carpeta sobre la que empezo el gesto.
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
})
