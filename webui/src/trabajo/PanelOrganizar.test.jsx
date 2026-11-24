import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    pickFolder: vi.fn(),
    pickFile: vi.fn(),
    folderIsEmpty: vi.fn().mockResolvedValue({ empty: true }),
    detectSuffixesStart: vi.fn().mockResolvedValue({ started: true }),
    analisisReset: vi.fn().mockResolvedValue({ ok: true }),
    analisisCancel: vi.fn().mockResolvedValue({ ok: true }),
    resultadoSubir: vi.fn().mockResolvedValue({ started: true }),
    resultadoCancelar: vi.fn().mockResolvedValue({ ok: true }),
    resultadoCredencial: vi.fn().mockResolvedValue({ ok: true }),
    resultadoPrechequeo: vi.fn().mockResolvedValue({ ok: true, checks: [] }),
    resultadoInspeccionarDestino: vi.fn().mockResolvedValue({ existe: false }),
    colaEncolar: vi.fn().mockResolvedValue({ ok: true, job: { id: 'j1' }, avisos: [] }),
  },
  onResultado: () => () => {},
  onAnalisis: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:analisis', w)
    return () => window.removeEventListener('atom:analisis', w)
  },
}))
import { api } from '../bridge'
import PanelOrganizar from './PanelOrganizar'

function emitirAnalisis(detail) {
  act(() => { window.dispatchEvent(new CustomEvent('atom:analisis', { detail })) })
}

beforeEach(() => vi.clearAllMocks())

describe('PanelOrganizar', () => {
  it('arranca la autodetección de sufijos cuando llega el origen', async () => {
    const { rerender } = render(
      <PanelOrganizar origen="" estadillos={[]} ready running={false} onRun={() => {}} />)
    rerender(
      <PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    await waitFor(() => expect(api.detectSuffixesStart).toHaveBeenCalledWith('/datos/vuelo'))
  })

  it('llama a analisisReset antes que a detectSuffixesStart', async () => {
    const orden = []
    api.analisisReset.mockImplementation(() => { orden.push('reset'); return Promise.resolve({ ok: true }) })
    api.detectSuffixesStart.mockImplementation(() => { orden.push('start'); return Promise.resolve({ started: true }) })
    const { rerender } = render(
      <PanelOrganizar origen="" estadillos={[]} ready running={false} onRun={() => {}} />)
    rerender(
      <PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    await waitFor(() => expect(api.detectSuffixesStart).toHaveBeenCalled())
    expect(orden).toEqual(['reset', 'start'])
  })

  it('rellena los sufijos con el resultado del evento done', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'done', scope: 'suffixes', data: { ok: true, thermal: '_T', rgb: '_W' } })
    await waitFor(() => expect(screen.getByDisplayValue('_T')).toBeTruthy())
    expect(screen.getByDisplayValue('_W')).toBeTruthy()
  })

  it('muestra el avance mientras escanea y permite cancelar', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'scan', scope: 'suffixes', done: 500 })
    expect(await screen.findByText(/500/)).toBeTruthy()
    fireEvent.click(screen.getByText(/Cancelar/i))
    expect(api.analisisCancel).toHaveBeenCalled()
  })

  it('el botón Ejecutar está desactivado sin carpeta final', () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    expect(screen.getByText(/Ejecutar/i).disabled).toBe(true)
  })

  it('ejecuta split_images con los parámetros correctos', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    const onRun = vi.fn()
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx']} ready running={false} onRun={onRun} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    fireEvent.click(screen.getByText(/Ejecutar/i))
    expect(onRun).toHaveBeenCalledWith(
      'split_images',
      expect.objectContaining({ origen: '/datos/vuelo', destino: '/datos/final', estadillo: ['/e.xlsx'] }),
      expect.anything(),
    )
  })

  it('manda el nombre de la inspección ya elegida en los params', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    const onRun = vi.fn()
    const inspeccion = { id: 42, prefijo: 'KL19', etiqueta: 'KL19 - Inspección térmica agosto' }
    render(
      <PanelOrganizar
        origen="/datos/vuelo"
        estadillos={['/e.xlsx']}
        inspeccion={inspeccion}
        ready
        running={false}
        onRun={onRun}
      />,
    )
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    fireEvent.click(screen.getByText(/Ejecutar/i))
    expect(onRun).toHaveBeenCalledWith(
      'split_images',
      expect.objectContaining({ inspeccion: 'KL19 - Inspección térmica agosto' }),
      expect.anything(),
    )
  })

  it('con fallidas del mismo origen ofrece Reorganizar N fallidas y lanza solo_fallidas', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    api.folderIsEmpty.mockResolvedValue({ empty: false, count: 9, organizado: true, fallidas: 689 })
    const onRun = vi.fn()
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx']} ready running={false} onRun={onRun} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    const boton = await screen.findByText(/Reorganizar 689 fallidas/)
    fireEvent.click(boton)
    expect(onRun).toHaveBeenCalledWith(
      'split_images',
      expect.objectContaining({ origen: '/datos/vuelo', destino: '/datos/final', solo_fallidas: true }),
      expect.anything(),
    )
  })

  it('sin fallidas no muestra el botón de reintento', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    api.folderIsEmpty.mockResolvedValue({ empty: false, count: 9, organizado: true, fallidas: 0 })
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    expect(screen.queryByText(/Reorganizar/)).toBeNull()
  })

  it('sin estadillo el botón Ejecutar queda desactivado aunque haya carpeta final', async () => {
    api.pickFolder.mockResolvedValue('/datos/final')
    api.folderIsEmpty.mockResolvedValue({ empty: true })
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    expect(screen.getByText(/Ejecutar/i).disabled).toBe(true)
  })

  it('explica por qué Ejecutar está deshabilitado (Falta: ...)', () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    expect(screen.getByTestId('falta-ejecutar').textContent).toBe('Falta: carpeta final / estadillo')
  })

  it('selector de cámara RGB solo con _Z y se envía a onRun', async () => {
    const onRun = vi.fn()
    const props = { estadillos: ['e.xlsx'], ready: true, running: false, onRun }
    const { rerender } = render(<PanelOrganizar origen="" {...props} />)
    rerender(<PanelOrganizar origen="/datos/vuelo" {...props} />)
    await waitFor(() => expect(api.detectSuffixesStart).toHaveBeenCalled())
    emitirAnalisis({ scope: 'suffixes', kind: 'done', data: { ok: true, thermal: '_T', rgb: '', tokens: { _T: 3, _W: 3 }, total: 6 } })
    expect(screen.queryByTestId('selector-camara-rgb')).toBeNull()
    emitirAnalisis({ scope: 'suffixes', kind: 'done', data: { ok: true, thermal: '_T', rgb: '', tokens: { _T: 3, _W: 3, _Z: 3 }, total: 9 } })
    expect(screen.getByTestId('selector-camara-rgb')).toBeTruthy()
    expect(screen.getByText(/Zoom: se crea copia _CROP/)).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'Wide (_W)' }))
    expect(screen.getByRole('radio', { name: 'Wide (_W)' }).getAttribute('aria-checked')).toBe('true')
  })
})

describe('PanelOrganizar rgb_tokens', () => {
  it('muestra los tokens RGB detectados cuando rgb va vacío', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'done', scope: 'suffixes', data: { ok: true, thermal: '_T', rgb: '', rgb_tokens: [{ token: '_W', n: 312 }], total: 600 } })
    await waitFor(() => expect(screen.getByTestId('rgb-detectado').textContent).toContain('_W ×312'))
  })
  it('avisa si no hay fotos RGB', async () => {
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={[]} ready running={false} onRun={() => {}} />)
    emitirAnalisis({ kind: 'done', scope: 'suffixes', data: { ok: true, thermal: '_T', rgb: '', rgb_tokens: [], total: 10 } })
    await waitFor(() => expect(screen.getByTestId('rgb-detectado').textContent).toContain('No se detectaron fotos RGB'))
  })

  describe('modo cola', () => {
    async function conDestino(props = {}) {
      api.pickFolder.mockResolvedValue('/datos/final')
      const onRun = vi.fn()
      render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx', '/f.xlsx']} ready running={false}
        onRun={onRun} {...props} />)
      fireEvent.click(screen.getAllByText(/Elegir/i)[0])
      await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
      return onRun
    }

    it('«Añadir a la cola» está desactivado sin carpeta final', () => {
      render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx']} ready running={false} onRun={() => {}} />)
      expect(screen.getByText(/Añadir a la cola/i).disabled).toBe(true)
    })

    it('con un run en marcha Ejecutar se bloquea pero «Añadir a la cola» sigue activo', async () => {
      await conDestino({ running: true })
      expect(screen.getByText(/Procesando/i).disabled).toBe(true)
      expect(screen.getByText(/Añadir a la cola/i).disabled).toBe(false)
    })

    it('encola con el estadillo empaquetado y sin pasar por onRun', async () => {
      const onRun = await conDestino({ running: true })
      fireEvent.click(screen.getByText(/Añadir a la cola/i))
      await waitFor(() => expect(api.colaEncolar).toHaveBeenCalledTimes(1))
      const [params, advanced] = api.colaEncolar.mock.calls[0]
      expect(params).toEqual(expect.objectContaining({
        origen: '/datos/vuelo', destino: '/datos/final', estadillo: '/e.xlsx\x1f/f.xlsx',
      }))
      expect(advanced).toBeTruthy()
      expect(onRun).not.toHaveBeenCalled()
      expect(await screen.findByTestId('cola-msg')).toBeTruthy()
    })

    it('«Subir a la nube al terminar» va marcada y se encola con subir_al_terminar', async () => {
      await conDestino({ running: true, inspeccion: { id: 7, prefijo: 'KL19' } })
      expect(screen.getByLabelText(/Subir a la nube al terminar/i).checked).toBe(true)
      fireEvent.click(screen.getByText(/Añadir a la cola/i))
      await waitFor(() => expect(api.colaEncolar).toHaveBeenCalledTimes(1))
      expect(api.colaEncolar.mock.calls[0][0].subir_al_terminar).toBe(true)
    })

    it('desmarcada, la cola manda subir_al_terminar=false', async () => {
      await conDestino({ running: true, inspeccion: { id: 7, prefijo: 'KL19' } })
      fireEvent.click(screen.getByLabelText(/Subir a la nube al terminar/i))
      fireEvent.click(screen.getByText(/Añadir a la cola/i))
      await waitFor(() => expect(api.colaEncolar).toHaveBeenCalledTimes(1))
      expect(api.colaEncolar.mock.calls[0][0].subir_al_terminar).toBe(false)
    })

    it('enseña los avisos (mismo destino) y los errores (origen ya en cola)', async () => {
      await conDestino()
      api.colaEncolar.mockResolvedValueOnce({ ok: true, job: { id: 'j2' }, avisos: ['El destino ya lo usa otro trabajo'] })
      fireEvent.click(screen.getByText(/Añadir a la cola/i))
      expect(await screen.findByTestId('cola-aviso')).toBeTruthy()
      api.colaEncolar.mockResolvedValueOnce({ ok: false, error: 'Esa carpeta ya está en la cola' })
      fireEvent.click(screen.getByText(/Añadir a la cola/i))
      await waitFor(() => expect(screen.getByTestId('cola-msg').textContent).toMatch(/ya está en la cola/))
      expect(screen.queryByTestId('cola-aviso')).toBeNull()
    })
  })
})

describe('PanelOrganizar subir a la nube al terminar', () => {
  async function conDestino(props = {}) {
    api.pickFolder.mockResolvedValue('/datos/final')
    const onRun = vi.fn()
    render(<PanelOrganizar origen="/datos/vuelo" estadillos={['/e.xlsx']} ready running={false} onRun={onRun} {...props} />)
    fireEvent.click(screen.getAllByText(/Elegir/i)[0])
    await waitFor(() => expect(screen.getByDisplayValue('/datos/final')).toBeTruthy())
    return onRun
  }

  it('la casilla está encima de Ejecutar, marcada por defecto, y Ejecutar manda subir_resultado=true', async () => {
    const onRun = await conDestino({ inspeccion: { id: 7, prefijo: 'KL19' } })
    const casilla = screen.getByLabelText(/Subir a la nube al terminar/i)
    expect(casilla.checked).toBe(true)
    const boton = screen.getByText(/Ejecutar/i)
    expect(casilla.compareDocumentPosition(boton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(boton)
    expect(onRun.mock.calls[0][1].subir_resultado).toBe(true)
  })

  it('desmarcada, Ejecutar manda subir_resultado=false', async () => {
    const onRun = await conDestino({ inspeccion: { id: 7, prefijo: 'KL19' } })
    fireEvent.click(screen.getByLabelText(/Subir a la nube al terminar/i))
    fireEvent.click(screen.getByText(/Ejecutar/i))
    expect(onRun.mock.calls[0][1].subir_resultado).toBe(false)
  })

  it('sin inspección y con la casilla marcada avisa visiblemente', async () => {
    await conDestino()
    expect(screen.getByTestId('aviso-sin-inspeccion')).toBeTruthy()
    fireEvent.click(screen.getByLabelText(/Subir a la nube al terminar/i))
    expect(screen.queryByTestId('aviso-sin-inspeccion')).toBeNull()
  })

  it('ya no hay enlace «Ir a Subir resultado»', async () => {
    await conDestino()
    expect(screen.queryByText(/Ir a «Subir resultado»/)).toBeNull()
  })
})
