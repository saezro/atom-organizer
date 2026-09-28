import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Selector confinado a discos externos del kiosco Linux (`_listado_raiz_discos_pi`
// / `_list_dir_confinado_pi`, app_webview.py:679-762): el shape nuevo trae
// `is_root`, `disk_name`, `rel_parts` (y `libre_gb`/`total_gb` en los discos de
// la raiz). Estos tests van en escritorio (isServerMode:false) a proposito:
// solo se prueba el render del shape nuevo, no el gesto tactil (ya cubierto en
// FolderPicker.test.jsx).
const RAIZ = {
  ok: true, path: null, parent: null,
  dirs: [
    { name: 'USB_INSPECCIONES', path: '/media/pi/USB_INSPECCIONES', libre_gb: 120.4, total_gb: 500.0 },
    { name: 'USB_BACKUP', path: '/media/pi/USB_BACKUP', libre_gb: null, total_gb: null },
  ],
  files: [],
  is_root: true, disk_name: null, rel_parts: [],
}
const DENTRO = {
  ok: true, path: '/media/pi/USB_INSPECCIONES/VUELOS', parent: '/media/pi/USB_INSPECCIONES',
  dirs: [{ name: 'CALAMOCHA', path: '/media/pi/USB_INSPECCIONES/VUELOS/CALAMOCHA' }],
  files: [],
  is_root: false, disk_name: 'USB_INSPECCIONES', rel_parts: ['VUELOS'],
}

function mockBridge(listDirImpl) {
  vi.doMock('./bridge.js', () => ({
    isServerMode: () => false,
    api: { listDir: vi.fn(listDirImpl), defaultDir: vi.fn(async () => ({ ok: false })) },
  }))
}

describe('FolderPicker - selector confinado a discos (Pi)', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
  })

  it('en la raiz muestra la lista de discos con su espacio, sin ".. subir"', async () => {
    mockBridge(async () => RAIZ)
    const { default: FolderPicker } = await import('./FolderPicker.jsx')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)

    expect(await screen.findByText('USB_INSPECCIONES')).toBeInTheDocument()
    expect(screen.getByText('120.4 GB libres de 500.0 GB')).toBeInTheDocument()
    expect(screen.getByText('USB_BACKUP')).toBeInTheDocument()
    expect(screen.queryByText('.. subir')).toBeNull()
    expect(screen.getByText('Discos externos')).toBeInTheDocument()
  })

  it('dentro de un disco pinta el breadcrumb relativo DISCO › carpeta', async () => {
    const listDir = vi.fn(async (path) => (path ? DENTRO : RAIZ))
    mockBridge(listDir)
    const { default: FolderPicker } = await import('./FolderPicker.jsx')
    render(<FolderPicker mode="folder" startPath="/media/pi/USB_INSPECCIONES/VUELOS" onPick={() => {}} onCancel={() => {}} />)

    await screen.findByText('CALAMOCHA')
    expect(screen.getByText('Discos')).toBeInTheDocument()
    expect(screen.getByText('USB_INSPECCIONES')).toBeInTheDocument()
    expect(screen.getByText('VUELOS')).toBeInTheDocument()
    // La ruta plana antigua no se pinta cuando hay breadcrumb.
    expect(screen.queryByText('/media/pi/USB_INSPECCIONES/VUELOS')).toBeNull()
  })

  it('el segmento "Discos" del breadcrumb vuelve a la lista de discos', async () => {
    const listDir = vi.fn(async (path) => (path ? DENTRO : RAIZ))
    mockBridge(listDir)
    const { default: FolderPicker } = await import('./FolderPicker.jsx')
    render(<FolderPicker mode="folder" startPath="/media/pi/USB_INSPECCIONES/VUELOS" onPick={() => {}} onCancel={() => {}} />)

    await screen.findByText('CALAMOCHA')
    await userEvent.click(screen.getByText('Discos'))
    await waitFor(() => expect(listDir).toHaveBeenCalledWith(null))
    expect(await screen.findByText('Discos externos')).toBeInTheDocument()
  })

  it('el primer segmento del disco vuelve a su raiz (ruta reconstruida sin pedirla al backend)', async () => {
    const listDir = vi.fn(async (path) => (path ? DENTRO : RAIZ))
    mockBridge(listDir)
    const { default: FolderPicker } = await import('./FolderPicker.jsx')
    render(<FolderPicker mode="folder" startPath="/media/pi/USB_INSPECCIONES/VUELOS" onPick={() => {}} onCancel={() => {}} />)

    await screen.findByText('CALAMOCHA')
    await userEvent.click(screen.getByText('USB_INSPECCIONES'))
    await waitFor(() => expect(listDir).toHaveBeenCalledWith('/media/pi/USB_INSPECCIONES'))
  })

  it('shape antiguo (sin is_root/disk_name/rel_parts) sigue mostrando la ruta plana de siempre', async () => {
    mockBridge(async () => ({
      ok: true, path: '/home/rebeca', parent: '/home',
      dirs: [{ name: 'VUELOS', path: '/home/rebeca/VUELOS' }],
      files: [],
    }))
    const { default: FolderPicker } = await import('./FolderPicker.jsx')
    render(<FolderPicker mode="folder" startPath={null} onPick={() => {}} onCancel={() => {}} />)

    await screen.findByText('VUELOS')
    expect(screen.getByText('/home/rebeca')).toBeInTheDocument()
    expect(screen.queryByText('Discos')).toBeNull()
  })
})
