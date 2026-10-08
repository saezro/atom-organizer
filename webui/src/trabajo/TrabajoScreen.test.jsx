import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
    estadillosDetectar: vi.fn().mockResolvedValue({ rutas: [] }),
  },
  onCloud: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:cloud', w)
    return () => window.removeEventListener('atom:cloud', w)
  },
  onAnalisis: () => () => {},
  onResultado: () => () => {},
  isServerMode: () => false,
}))
import { act } from '@testing-library/react'
import TrabajoScreen from './TrabajoScreen'

const emitir = (canal, detail) =>
  act(() => { window.dispatchEvent(new CustomEvent(canal, { detail })) })

beforeEach(() => vi.clearAllMocks())

describe('TrabajoScreen', () => {
  it('muestra los destinos pero deshabilitados hasta que hay carpeta', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    const destino = screen.getByText(/Organizar en este ordenador/i).closest('button')
    expect(destino).toBeTruthy()
    expect(destino.disabled).toBe(true)
    expect(screen.getByText(/Subir sin organizar/i).closest('button').disabled).toBe(true)
  })

  it('ofrece los dos destinos al elegir carpeta y la casilla de nube solo al subir', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    expect(await screen.findByText(/Organizar en este ordenador/i)).toBeTruthy()
    expect(screen.getByText(/Subir sin organizar/i)).toBeTruthy()
    expect(screen.queryByText(/Subir y organizar en la nube/i)).toBeNull()
    expect(screen.queryByLabelText(/Organizar después en la nube/i)).toBeNull()
    fireEvent.click(screen.getByText(/Subir sin organizar/i))
    const casilla = screen.getByLabelText(/Organizar después en la nube/i)
    expect(casilla.checked).toBe(false)
    fireEvent.click(casilla)
    expect(casilla.checked).toBe(true)
  })

  it('la carpeta elegida sobrevive al cambio de destino', async () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir sin organizar/i))
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
    fireEvent.click(screen.getByText(/Organizar en este ordenador/i))
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
  })

  it('la carpeta se pide una sola vez, no una por destino', async () => {
    const { api } = await import('../bridge')
    render(<TrabajoScreen ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await screen.findByDisplayValue('/datos/vuelo')
    fireEvent.click(screen.getByText(/Subir sin organizar/i))
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
    fireEvent.click(screen.getByText(/Subir sin organizar/i))
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
    fireEvent.click(screen.getByText(/Subir sin organizar/i))
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
    fireEvent.click(screen.getByText(/Subir sin organizar/i))
    await waitFor(() => expect(api.cloudStatus).toHaveBeenCalled())
    await waitFor(() =>
      expect(onCloudStatusChange).toHaveBeenCalledWith(
        expect.objectContaining({ logged_in: true })
      )
    )
  })

  it('sin módulo organizer no ofrece destinos', () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={{ organizer: false, estadillos: true }} />)
    expect(screen.queryByText(/Organizar en este ordenador/i)).toBeNull()
    expect(screen.queryByText(/Subir sin organizar/i)).toBeNull()
  })

  it('sin módulo estadillos no pinta el paso de estadillo pero mantiene los destinos', () => {
    render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={{ organizer: true, estadillos: false }} />)
    expect(screen.queryByText(/Subir sin estadillo/i)).toBeNull()
    expect(screen.getByText(/Organizar en este ordenador/i)).toBeTruthy()
  })

  describe('sin módulo estadillos (acceso.estadillos === false)', () => {
    const acceso = { organizer: true, estadillos: false }
    // Elige origen y destino (ambos con pickFolder) y deja listo «Organizar aquí».
    async function prepararOrganizar(onRun) {
      const { api } = await import('../bridge')
      api.pickFolder.mockResolvedValue('/datos/vuelo')
      render(<TrabajoScreen ready running={false} onRun={onRun} acceso={acceso} />)
      fireEvent.click(screen.getAllByText(/Elegir/i)[0])
      await screen.findByDisplayValue('/datos/vuelo')
      fireEvent.click(screen.getByText(/Organizar en este ordenador/i))
      api.pickFolder.mockResolvedValue('/datos/final')
      await waitFor(() => expect(screen.getAllByText(/Elegir/i).length).toBeGreaterThan(1))
      fireEvent.click(screen.getAllByText(/Elegir/i)[1])
      await screen.findByDisplayValue('/datos/final')
      return api
    }

    it('autodetecta el estadillo del origen y lo pasa a onRun', async () => {
      const { api } = await import('../bridge')
      api.estadillosDetectar.mockResolvedValue({ rutas: ['/o/e.csv'] })
      const onRun = vi.fn()
      await prepararOrganizar(onRun)
      expect(await screen.findByText(/Estadillo: e\.csv/)).toBeTruthy()
      expect(api.estadillosDetectar).toHaveBeenCalledWith('/datos/vuelo', true)
      const btn = screen.getByText(/Ejecutar/i)
      await waitFor(() => expect(btn.disabled).toBe(false))
      fireEvent.click(btn)
      expect(onRun).toHaveBeenCalledWith(
        'split_images',
        expect.objectContaining({ estadillo: ['/o/e.csv'] }),
        expect.anything(),
      )
    })

    it('sin estadillo en la carpeta: error visible y botón deshabilitado', async () => {
      const { api } = await import('../bridge')
      api.estadillosDetectar.mockResolvedValue({ rutas: [] })
      await prepararOrganizar(vi.fn())
      expect(await screen.findByText(/No hay estadillo en la carpeta de origen/)).toBeTruthy()
      expect(screen.getByText(/Ejecutar/i).disabled).toBe(true)
    })

    // Origen elegido, sin elegir destino: basta para ver el estado de la detección.
    async function soloOrigen() {
      const { api } = await import('../bridge')
      api.pickFolder.mockResolvedValue('/datos/vuelo')
      return api
    }
    const elegirOrigen = async () => {
      fireEvent.click(screen.getAllByText(/Elegir/i)[0])
      await screen.findByDisplayValue('/datos/vuelo')
    }

    // Con temporizadores falsos desde el principio (los del hook y de conPlazo
    // se crean tras elegir el origen).
    async function origenConTimersFalsos() {
      vi.useFakeTimers()
      fireEvent.click(screen.getAllByText(/Elegir/i)[0])
      await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    }
    const avanzar = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
    afterEach(() => vi.useRealTimers())

    it('muestra «Buscando estadillo…» mientras la detección está en vuelo', async () => {
      const api = await soloOrigen()
      api.estadillosDetectar.mockReturnValue(new Promise(() => {}))
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      await elegirOrigen()
      expect(await screen.findByText(/Buscando estadillo…/)).toBeTruthy()
    })

    it('respuesta {error}: se ve el mensaje', async () => {
      const api = await soloOrigen()
      api.estadillosDetectar.mockResolvedValue({ error: 'OSError: sin permisos' })
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      await elegirOrigen()
      expect(await screen.findByText(/OSError: sin permisos/)).toBeTruthy()
    })

    it('promesa rechazada: se ve el mensaje', async () => {
      const api = await soloOrigen()
      api.estadillosDetectar.mockRejectedValue(new Error('puente caído'))
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      await elegirOrigen()
      expect(await screen.findByText(/puente caído/)).toBeTruthy()
    })

    it('no_existe se recupera al reintentar', async () => {
      const api = await soloOrigen()
      api.estadillosDetectar
        .mockResolvedValueOnce({ no_existe: true })
        .mockResolvedValue({ rutas: ['/o/e.csv'] })
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      await origenConTimersFalsos()
      expect(screen.getByText(/aún no está disponible, reintentando/)).toBeTruthy()
      await avanzar(3100)
      expect(screen.getByText(/Estadillo: e\.csv/)).toBeTruthy()
    })

    it('no_existe agota los reintentos y pasa a error', async () => {
      const api = await soloOrigen()
      api.estadillosDetectar.mockResolvedValue({ no_existe: true })
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      await origenConTimersFalsos()
      expect(screen.getByText(/reintentando/)).toBeTruthy()
      await avanzar(125000)
      expect(screen.getByText(/sigue sin estar disponible/)).toBeTruthy()
    })

    it('plazo vencido: error visible', async () => {
      const api = await soloOrigen()
      api.estadillosDetectar.mockReturnValue(new Promise(() => {}))
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      await origenConTimersFalsos()
      expect(screen.getByText(/Buscando estadillo…/)).toBeTruthy()
      await avanzar(15500)
      expect(screen.getByText(/ha tardado demasiado/)).toBeTruthy()
    })

    it('descarta la respuesta obsoleta si cambia el origen', async () => {
      const { api } = await import('../bridge')
      let resolverViejo
      api.estadillosDetectar.mockImplementation((c) =>
        c === '/datos/vuelo'
          ? new Promise((res) => { resolverViejo = res })
          : Promise.resolve({ rutas: ['/n/nuevo.csv'] }))
      api.pickFolder.mockResolvedValue('/datos/vuelo')
      render(<TrabajoScreen ready running={false} onRun={() => {}} acceso={acceso} />)
      fireEvent.click(screen.getAllByText(/Elegir/i)[0])
      await screen.findByDisplayValue('/datos/vuelo')
      api.pickFolder.mockResolvedValue('/datos/otro')
      fireEvent.click(screen.getAllByText(/Elegir/i)[0])
      await screen.findByDisplayValue('/datos/otro')
      expect(await screen.findByText(/Estadillo: nuevo\.csv/)).toBeTruthy()
      await act(async () => { resolverViejo({ rutas: ['/o/viejo.csv'] }) })
      expect(screen.queryByText(/viejo\.csv/)).toBeNull()
      expect(screen.getByText(/Estadillo: nuevo\.csv/)).toBeTruthy()
    })
  })
})
