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
      if (path === '/media/pi/USB') {
        return {
          ok: true, path: '/media/pi/USB', parent: '/media/pi',
          dirs: [{ name: 'INSPECCION_01', path: '/media/pi/USB/INSPECCION_01' }],
          files: [],
        }
      }
      return { ok: true, path, parent: '/home/rebeca', dirs: [], files: [] }
    }),
    // Por defecto no hay disco USB (mismo comportamiento que antes de esta
    // funcionalidad): los tests existentes de este fichero siguen arrancando
    // en el home vía listDir(null). El test dedicado abajo sobreescribe esto
    // con mockResolvedValueOnce para probar el camino con disco.
    defaultDir: vi.fn(async () => ({ ok: false, error: 'sin disco' })),
  },
}))

import FolderPicker from './FolderPicker.jsx'

// En modo servidor la fila se activa al SOLTAR, al instante. Lo que distingue
// un toque de un scroll es el recorrido del dedo, no el tiempo: el panel
// resistivo llega como raton y el click sintetico no basta para desambiguar.
// Sin esperas en ninguno de estos tests: si alguna vez vuelve a hacer falta un
// setTimeout aqui, es que la activacion ha vuelto a depender del reloj.
function tocar(el, y = 100) {
  fireEvent.pointerDown(el, { clientY: y })
  fireEvent.pointerUp(el, { clientY: y })
}

describe('FolderPicker', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lista las carpetas de la ruta inicial', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    expect(await screen.findByText('VUELOS')).toBeInTheDocument()
  })

  it('en modo carpeta se ven los ficheros pero no son elegibles', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    const fichero = screen.getByText('estadillo.xlsx')
    expect(fichero).toBeInTheDocument()
    // No es un boton (no es pulsable): esta en un <li> inerte, no envuelto
    // por BotonToque como las carpetas.
    expect(fichero.closest('button')).toBeNull()
    expect(fichero.closest('li')).toHaveAttribute('aria-disabled', 'true')
  })

  it('en modo fichero un toque devuelve la ruta', async () => {
    const onPick = vi.fn()
    render(<FolderPicker mode="file" startPath={null} onPick={onPick} onCancel={() => {}} />)
    tocar(await screen.findByText('estadillo.xlsx'))
    expect(onPick).toHaveBeenCalledWith('/home/rebeca/estadillo.xlsx')
  })

  it('un toque sobre una carpeta la abre, sin esperar nada', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    tocar(await screen.findByText('VUELOS'))
    // Sin waitFor ni timers: al soltar ya tiene que estar pedida la carpeta.
    expect(api.listDir).toHaveBeenCalledWith('/home/rebeca/VUELOS')
  })

  it('el click sintetico posterior al toque no la abre dos veces', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    tocar(fila)
    fireEvent.click(fila)  // el raton sintetiza un click tras soltar
    // La inicial + una sola apertura: el click no cuenta como segunda.
    await waitFor(() => expect(api.listDir).toHaveBeenCalledTimes(2))
  })

  it('arrastrar sobre una fila NO la abre (era un scroll)', async () => {
    const { api } = await import('./bridge.js')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = await screen.findByText('VUELOS')
    fireEvent.pointerDown(fila, { clientY: 100 })
    fireEvent.pointerMove(fila, { clientY: 40 })  // 60px, muy por encima del umbral
    fireEvent.pointerUp(fila, { clientY: 40 })
    expect(api.listDir).toHaveBeenCalledTimes(1)  // solo la carga inicial
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

  it('mientras carga muestra el placeholder DENTRO de la lista', async () => {
    const { api } = await import('./bridge.js')
    let resolver
    api.listDir.mockReturnValueOnce(new Promise((r) => { resolver = r }))
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const lista = document.querySelector('.picker-lista')
    expect(lista.querySelector('.picker-cargando')).not.toBeNull()
    resolver({
      ok: true, path: '/home/rebeca', parent: '/home',
      dirs: [{ name: 'VUELOS', path: '/home/rebeca/VUELOS' }],
      files: [],
    })
    await screen.findByText('VUELOS')
    expect(lista.querySelector('.picker-cargando')).toBeNull()
  })

  it('un toque marca la fila al instante, antes de que listDir resuelva', async () => {
    const { api } = await import('./bridge.js')
    let resolver
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    const fila = (await screen.findByText('VUELOS')).closest('button')
    api.listDir.mockReturnValueOnce(new Promise((r) => { resolver = r }))
    tocar(fila)
    expect(document.querySelector('.picker-fila-cargando')).not.toBeNull()
    resolver({ ok: true, path: '/home/rebeca/VUELOS', parent: '/home/rebeca', dirs: [], files: [] })
    await waitFor(() => expect(document.querySelector('.picker-fila-cargando')).toBeNull())
  })

  it('la lista lleva botones de paginado en tactil', async () => {
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    await screen.findByText('VUELOS')
    expect(screen.getByLabelText('Subir en la lista')).toBeInTheDocument()
    expect(screen.getByLabelText('Bajar en la lista')).toBeInTheDocument()
  })

  it('en modo servidor arranca en el disco USB si hay uno (api.defaultDir)', async () => {
    const { api } = await import('./bridge.js')
    // defaultDir ya trae el listado completo (mismo shape que listDir): una
    // sola llamada HTTP, sin encadenar un listDir despues.
    api.defaultDir.mockResolvedValueOnce({
      ok: true, path: '/media/pi/USB', parent: '/media/pi',
      dirs: [{ name: 'INSPECCION_01', path: '/media/pi/USB/INSPECCION_01' }],
      files: [],
    })
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)
    expect(await screen.findByText('INSPECCION_01')).toBeInTheDocument()
    expect(api.defaultDir).toHaveBeenCalledTimes(1)
    expect(api.listDir).not.toHaveBeenCalled()
  })
})
