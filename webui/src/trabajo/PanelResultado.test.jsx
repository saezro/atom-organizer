import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    resultadoSubir: vi.fn().mockResolvedValue({ started: true }),
    resultadoCancelar: vi.fn().mockResolvedValue({ ok: true }),
    resultadoCredencial: vi.fn().mockResolvedValue({ ok: true }),
    resultadoPrechequeo: vi.fn().mockResolvedValue({ ok: true, checks: [] }),
    resultadoInspeccionarDestino: vi.fn().mockResolvedValue({ existe: false, n_objetos: 0, bytes: 0, sobrescribe: [] }),
  },
  onResultado: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:resultado', w)
    return () => window.removeEventListener('atom:resultado', w)
  },
}))
import { api } from '../bridge'
import PanelResultado, { textoProgreso, textoVelocidad, textoEta } from './PanelResultado'

const emitir = (detail) => act(() => { window.dispatchEvent(new CustomEvent('atom:resultado', { detail })) })

beforeEach(() => {
  vi.clearAllMocks()
  api.resultadoCredencial.mockResolvedValue({ ok: true })
  api.resultadoPrechequeo.mockResolvedValue({ ok: true, checks: [] })
  api.resultadoInspeccionarDestino.mockResolvedValue({ existe: false, n_objetos: 0, bytes: 0, sobrescribe: [] })
})

// Publicar ahora pasa por el aviso pre-subida: pulsar y confirmar.
async function pulsarPublicar() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar resultado' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Publicar resultado' }))
}
async function publicar() {
  await pulsarPublicar()
  fireEvent.click(await screen.findByRole('button', { name: 'Confirmar y subir' }))
}

describe('textoProgreso', () => {
  it('formatea «urgentes X/Y · resto X/Y»', () => {
    expect(textoProgreso({ urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 25 } }))
      .toBe('urgentes 3/10 · resto 0/25')
  })
  it('vacío sin datos', () => { expect(textoProgreso(null)).toBe('') })
  it('fase listando: «Leyendo el bucket…»', () => {
    expect(textoProgreso({ fase: 'listando', urgentes: { hechos: 0, total: 1 }, resto: { hechos: 0, total: 1 } }))
      .toBe('Leyendo el bucket…')
  })
  it('fase comprobando: «Comprobando ficheros X/Y…» en vez del contador', () => {
    expect(textoProgreso({ comprobando: { hechos: 4, total: 9 }, urgentes: { hechos: 0, total: 10 }, resto: { hechos: 0, total: 25 } }))
      .toBe('Comprobando ficheros 4/9…')
  })
})

describe('textoVelocidad / textoEta', () => {
  it('convierte megabits a MB/s y añade img/s y ETA', () => {
    expect(textoVelocidad({ fase: 'urgentes', mbps: 80, files_per_s: 2.5, eta: 125 }))
      .toBe('10.0 MB/s · 2.5 img/s · ETA ~02:05')
  })
  it('sin ETA (primeros segundos) no la pinta; ETA larga en h:mm', () => {
    expect(textoVelocidad({ fase: 'urgentes', mbps: 8, files_per_s: 1, eta: null })).toBe('1.0 MB/s · 1.0 img/s')
    expect(textoEta(3 * 3600 + 5 * 60)).toBe('3:05')
  })
  it('nada mientras lista o comprueba', () => {
    expect(textoVelocidad({ fase: 'listando', mbps: null })).toBe('')
    expect(textoVelocidad({ fase: 'urgentes', mbps: 8, comprobando: { hechos: 1, total: 2 } })).toBe('')
  })
})

describe('PanelResultado', () => {
  it('con Urgencia por defecto, el botón lanza resultadoSubir(carpeta, id, urgencia, false)', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(screen.getByRole('radio', { name: 'Urgencia' })).toBeChecked()
    await publicar()
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', false))
  })

  it('modo Normal manda «normal»', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    fireEvent.click(screen.getByRole('radio', { name: 'Normal' }))
    await publicar()
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'normal', false))
  })

  it('sin carpeta o sin inspección el botón está deshabilitado y lo explica', () => {
    render(<PanelResultado carpeta="" inspeccionId={null} ready />)
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
    expect(screen.getByText(/Elige la inspección/)).toBeInTheDocument()
  })

  it('pinta el progreso y deshabilita el botón mientras sube', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'estado', fase: 'urgentes', urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 25 } })
    expect(await screen.findByText('urgentes 3/10 · resto 0/25')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument()
  })

  it('enseña el destino real desde el primer evento y la velocidad', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'estado', fase: 'listando', prefix: 'CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2/', urgentes: { hechos: 0, total: 10 }, resto: { hechos: 0, total: 0 } })
    expect(await screen.findByText('Destino: gs://plantas_pv_nl/CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2/')).toBeInTheDocument()
    expect(screen.getByText('Leyendo el bucket…')).toBeInTheDocument()
    emitir({ kind: 'estado', fase: 'urgentes', prefix: 'CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2/', mbps: 16, files_per_s: 3, eta: 60, urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 0 } })
    expect(await screen.findByText('2.0 MB/s · 3.0 img/s · ETA ~01:00')).toBeInTheDocument()
  })

  it('muestra conflictos y fallidas y permite reintentar', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'done', ok: false, modo: 'urgencia', continua: false,
      urgentes: { hechos: 4, total: 5 }, resto: { hechos: 0, total: 3 },
      conflictos: [['P/TERMICA/A_T.tiff', 'ya existe con otro contenido']],
      fallidas: [['P/RGB/B_W_CROP.JPG', 'boom']], avisos: ['2 fichero(s) sin clasificar'], sin_clasificar: 2 })
    expect(await screen.findByText(/1 en conflicto/)).toBeInTheDocument()
    expect(screen.getByText(/1 fallida/)).toBeInTheDocument()
    expect(screen.getByText(/sin clasificar/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resubir fallidas' }))
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', false))
  })

  it('un done con continua:true no cierra el estado «subiendo»', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'done', ok: true, modo: 'urgencia', continua: true,
      urgentes: { hechos: 5, total: 5 }, resto: { hechos: 0, total: 3 }, conflictos: [], fallidas: [], avisos: [] })
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
    emitir({ kind: 'done', ok: true, modo: 'normal', continua: false,
      urgentes: { hechos: 5, total: 5 }, resto: { hechos: 3, total: 3 }, conflictos: [], fallidas: [], avisos: [] })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar resultado' })).not.toBeDisabled())
    expect(screen.getByText('Subida completa')).toBeInTheDocument()
  })

  it('muestra errores y avisos tal cual', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'error', text: 'La inspección no tiene planta o año asignados en ATOM Suite' })
    expect(await screen.findByText(/no tiene planta o año/)).toBeInTheDocument()
    emitir({ kind: 'aviso', text: 'No se sube el resultado al bucket: no hay inspección elegida.' })
    expect(await screen.findByText(/no hay inspección elegida/)).toBeInTheDocument()
  })

  it('ignora eventos de otros canales (no hay scope mezclado)', () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    act(() => { window.dispatchEvent(new CustomEvent('atom:cloud', { detail: { kind: 'done', ok: true } })) })
    expect(screen.queryByText('Subida completa')).toBeNull()
  })

  it('si resultadoSubir rechaza (kiosco sin el método) el error se ve', async () => {
    api.resultadoSubir.mockRejectedValueOnce(new Error('método no expuesto'))
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await publicar()
    expect(await screen.findByText(/método no expuesto/)).toBeInTheDocument()
  })

  it('sin credencial el botón queda deshabilitado y se ve el motivo', async () => {
    api.resultadoCredencial.mockResolvedValue({ ok: false, reason: 'Inicia sesión en la Suite' })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText('Inicia sesión en la Suite')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
  })

  it('Publicar enseña el aviso con los checks y no sube hasta confirmar', async () => {
    api.resultadoPrechequeo.mockResolvedValue({ ok: true, checks: [
      { id: 'a', titulo: 'Carpeta con imágenes', estado: 'ok', detalle: '' },
      { id: 'b', titulo: 'Metadatos', estado: 'aviso', detalle: 'faltan 2' },
    ] })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await pulsarPublicar()
    expect(await screen.findByText(/Carpeta con imágenes/)).toBeInTheDocument()
    expect(screen.getByText(/faltan 2/)).toBeInTheDocument()
    expect(api.resultadoPrechequeo).toHaveBeenCalledWith('/salida')
    expect(api.resultadoInspeccionarDestino).toHaveBeenCalledWith('/salida', 7)
    expect(api.resultadoSubir).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar y subir' }))
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalled())
  })

  it('con un check en error hace falta la casilla «subir igualmente»', async () => {
    api.resultadoPrechequeo.mockResolvedValue({ ok: false, checks: [{ id: 'x', titulo: 'Planta', estado: 'error', detalle: 'no coincide' }] })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await pulsarPublicar()
    const confirmar = await screen.findByRole('button', { name: 'Confirmar y subir' })
    expect(confirmar).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Subir igualmente' }))
    expect(confirmar).not.toBeDisabled()
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalled())
  })

  it('muestra la sobrescritura si el destino ya existe', async () => {
    api.resultadoInspeccionarDestino.mockResolvedValue({
      existe: true, n_objetos: 120, bytes: 2.5e9, sobrescribe: ['TERMICA', 'RGB'], detalle: '' })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await pulsarPublicar()
    expect(await screen.findByText(/Ya hay 120 objetos \/ 2\.5 GB en el bucket; se sobrescriben: TERMICA, RGB/)).toBeInTheDocument()
  })

  it('started:false muestra el reason', async () => {
    api.resultadoSubir.mockResolvedValueOnce({ started: false, reason: 'Ya hay una subida en curso' })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await publicar()
    expect(await screen.findByText('Ya hay una subida en curso')).toBeInTheDocument()
  })

  it('variante ya-organizado se indica en el panel', () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready variante="ya-organizado" />)
    expect(screen.getByText(/ya organizada, sin volver a organizar/)).toBeInTheDocument()
  })

  it('si resultadoCredencial lanza, no se asume ok: botón deshabilitado y motivo visible', async () => {
    api.resultadoCredencial.mockRejectedValue(new Error('sin red'))
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText('No se pudo comprobar el login: sin red')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
  })

  it('prechequeo con forma inesperada: error visible, sin aviso ni subida', async () => {
    api.resultadoPrechequeo.mockResolvedValue([])
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await pulsarPublicar()
    expect(await screen.findByText(/Respuesta inesperada del prechequeo/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirmar y subir' })).toBeNull()
  })

  it('inspeccionar destino con ok:false muestra el error y exige «subir igualmente»', async () => {
    api.resultadoInspeccionarDestino.mockResolvedValue({ ok: false, error: 'bucket inaccesible', existe: false })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await pulsarPublicar()
    expect(await screen.findByText('bucket inaccesible')).toBeInTheDocument()
    const confirmar = screen.getByRole('button', { name: 'Confirmar y subir' })
    expect(confirmar).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Subir igualmente' }))
    expect(confirmar).not.toBeDisabled()
  })

  it('recomprueba la credencial al pulsar Publicar y no continúa si caducó', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar resultado' })).not.toBeDisabled())
    api.resultadoCredencial.mockResolvedValue({ ok: false, reason: 'Sesión caducada' })
    fireEvent.click(screen.getByRole('button', { name: 'Publicar resultado' }))
    expect(await screen.findByText('Sesión caducada')).toBeInTheDocument()
    expect(api.resultadoPrechequeo).not.toHaveBeenCalled()
  })

  it('recomprueba la credencial al volver el foco a la ventana', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar resultado' })).not.toBeDisabled())
    api.resultadoCredencial.mockResolvedValue({ ok: false, reason: 'Login perdido' })
    act(() => { window.dispatchEvent(new Event('focus')) })
    expect(await screen.findByText('Login perdido')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar resultado' })).toBeDisabled()
  })
})
