import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// En escritorio (la UI de siempre, la que usa Rebeca) el selector se maneja con
// raton: click normal para abrir y scroll nativo. Nada del gesto tactil del
// kiosco debe engancharse aqui.
vi.mock('./bridge.js', () => ({
  isServerMode: () => false,
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
    defaultDir: vi.fn(async () => ({ ok: true, path: '/media/pi/USB' })),
  },
}))

import FolderPicker from './FolderPicker.jsx'
import { api } from './bridge.js'

describe('FolderPicker en escritorio', () => {
  beforeEach(() => vi.clearAllMocks())

  it('un click normal abre la carpeta (sin mantener el dedo)', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await userEvent.click(await screen.findByText('VUELOS'))
    await waitFor(() => expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS'))
  })

  it('un temblor del raton entre pulsar y soltar no se come el click', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    // Micro-arrastre: mas que el umbral del gesto tactil, menos que una
    // intencion de scrollear. En el kiosco esto cancela; aqui no debe.
    fireEvent.pointerDown(fila, { clientY: 100 })
    fireEvent.pointerMove(fila, { clientY: 130 })
    fireEvent.pointerUp(fila, { clientY: 130 })
    fireEvent.click(fila)
    await waitFor(() => expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS'))
  })

  it('no muestra la ayuda de pulsacion larga', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    expect(screen.queryByText(/Mantén el dedo/i)).toBeNull()
  })

  it('en escritorio no llama a defaultDir: arranca en el home de siempre', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    expect(api.defaultDir).not.toHaveBeenCalled()
    expect(api.listDir).toHaveBeenCalledWith(null)
  })
})
