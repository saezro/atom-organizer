import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    pickFolder: vi.fn().mockResolvedValue('/datos/vuelo'),
    folderIsEmpty: vi.fn().mockResolvedValue({ empty: true }),
    cloudInspecciones: vi.fn().mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' }),
    cloudStatus: vi.fn().mockResolvedValue({ configured: true, logged_in: true, email: 'a@b.c' }),
    cloudVerify: vi.fn().mockResolvedValue({ ok: true }),
    estadilloExistente: vi.fn().mockResolvedValue({ existe: false }),
    detectSuffixesStart: vi.fn().mockResolvedValue({ started: true }),
    cloudPrepareStart: vi.fn().mockResolvedValue({ started: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
  },
  onCloud: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:cloud', w)
    return () => window.removeEventListener('atom:cloud', w)
  },
  onAnalisis: () => () => {},
}))
import { act } from '@testing-library/react'
import TrabajoScreen from './TrabajoScreen'

const emitir = (canal, detail) =>
  act(() => { window.dispatchEvent(new CustomEvent(canal, { detail })) })

beforeEach(() => vi.clearAllMocks())

describe('TrabajoScreen', () => {
  it('no ofrece destino hasta que hay carpeta', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    expect(screen.queryByText(/Organizar aquí/i)).toBeNull()
  })

  it('ofrece los tres destinos al elegir carpeta', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    expect(await screen.findByText(/Organizar aquí/i)).toBeTruthy()
    expect(screen.getByText(/Subir al bucket/i)).toBeTruthy()
    expect(screen.getByText(/Subir y organizar en la nube/i)).toBeTruthy()
  })

  it('la carpeta elegida sobrevive al cambio de destino', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
    fireEvent.click(screen.getByText(/Organizar aquí/i))
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
  })

  it('la carpeta se pide una sola vez, no una por destino', async () => {
    const { api } = await import('../bridge')
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(api.cloudPrepareStart).not.toHaveBeenCalled()) // aún sin inspección
    expect(api.pickFolder).toHaveBeenCalledTimes(1)
  })

  // F4: el original (`BucketScreen`) deshabilitaba TODO el formulario
  // mientras había una subida en curso (`busy || uploading || estadSubiendo`).
  // Aquí `PanelSubida` reporta su propio ocupado hacia arriba y el padre lo
  // suma al `disabled` de los demás pasos.
  it('deshabilita carpeta y estadillo mientras hay una subida en curso', async () => {
    const { api } = await import('../bridge')
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(api.cloudStatus).toHaveBeenCalled())

    const checkbox = () => screen.getByRole('checkbox', { name: /Subir sin estadillo/i })
    expect(checkbox().disabled).toBe(false)

    emitir('atom:cloud', { kind: 'start', files: 1, bytes: 1, prefix: 'x' })
    await waitFor(() => expect(checkbox().disabled).toBe(true))

    // La carpeta ya no se puede volver a elegir mientras dura la subida.
    api.pickFolder.mockClear()
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    expect(api.pickFolder).not.toHaveBeenCalled()

    emitir('atom:cloud', { kind: 'done', ok: true, uploaded: 1, cancelled: false })
    await waitFor(() => expect(checkbox().disabled).toBe(false))
  })

  // F5: el original recargaba el catálogo de inspecciones justo al iniciar
  // sesión (`case 'login'` → `cargarInspecciones()`), sin esperar a que el
  // operario pulse «Actualizar» a mano.
  it('recarga el catálogo de inspecciones tras iniciar sesión', async () => {
    const { api } = await import('../bridge')
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(api.cloudInspecciones).toHaveBeenCalledTimes(1))

    emitir('atom:cloud', { kind: 'login', ok: true })
    await waitFor(() => expect(api.cloudInspecciones).toHaveBeenCalledTimes(2))
  })

  it('reenvía onCloudStatusChange a PanelSubida', async () => {
    const onCloudStatusChange = vi.fn()
    const { api } = await import('../bridge')
    render(
      <TrabajoScreen ready running={false} onRun={() => {}} onCloudStatusChange={onCloudStatusChange} />
    )
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir al bucket/i))
    await waitFor(() => expect(api.cloudStatus).toHaveBeenCalled())
    await waitFor(() =>
      expect(onCloudStatusChange).toHaveBeenCalledWith(
        expect.objectContaining({ logged_in: true })
      )
    )
  })
})
