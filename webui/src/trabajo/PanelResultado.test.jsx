import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    resultadoSubir: vi.fn().mockResolvedValue({ started: true }),
    resultadoCancelar: vi.fn().mockResolvedValue({ ok: true }),
    resultadoEncadenar: vi.fn().mockResolvedValue({ ok: true }),
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
import PanelResultado, { textoProgreso, textoVelocidad, textoEta, resumenMetricas } from './PanelResultado'

const emitir = (detail) => act(() => { window.dispatchEvent(new CustomEvent('atom:resultado', { detail })) })

beforeEach(() => {
  vi.clearAllMocks()
  api.resultadoCredencial.mockResolvedValue({ ok: true })
  api.resultadoPrechequeo.mockResolvedValue({ ok: true, checks: [] })
  api.resultadoInspeccionarDestino.mockResolvedValue({ existe: false, n_objetos: 0, bytes: 0, sobrescribe: [] })
})

// Publicar ahora pasa por el aviso pre-subida: pulsar y confirmar.
async function pulsarPublicar() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar urgentes' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: 'Publicar urgentes' }))
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
      .toBe('10,0 MB/s · 2,5 img/s · ETA ~02:05')
  })
  it('sin ETA (primeros segundos) no la pinta; ETA larga en h:mm', () => {
    expect(textoVelocidad({ fase: 'urgentes', mbps: 8, files_per_s: 1, eta: null })).toBe('1,0 MB/s · 1,0 img/s')
    expect(textoEta(3 * 3600 + 5 * 60)).toBe('3:05')
  })
  it('nada mientras lista o comprueba', () => {
    expect(textoVelocidad({ fase: 'listando', mbps: null })).toBe('')
    expect(textoVelocidad({ fase: 'urgentes', mbps: 8, comprobando: { hechos: 1, total: 2 } })).toBe('')
  })
})

describe('PanelResultado', () => {
  it('con Urgencia por defecto, el botón lanza resultadoSubir(carpeta, id, urgencia, seguir=true con la casilla por defecto)', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await publicar()
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', true))
  })

  it('«Subir todo» pasa por el aviso y manda «normal»', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Subir todo' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Subir todo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar y subir' }))
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'normal', false))
  })

  it('sin carpeta o sin inspección el botón está deshabilitado y lo explica', () => {
    render(<PanelResultado carpeta="" inspeccionId={null} ready />)
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
    expect(screen.getByText(/Elige la inspección/)).toBeInTheDocument()
  })

  it('pinta el progreso y deshabilita el botón mientras sube', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'estado', fase: 'urgentes', urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 25 } })
    expect(await screen.findByText('urgentes 3/10 · resto 0/25')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument()
  })

  it('enseña el destino real desde el primer evento y la velocidad', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'estado', fase: 'listando', prefix: 'CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2/', urgentes: { hechos: 0, total: 10 }, resto: { hechos: 0, total: 0 } })
    expect(await screen.findByText('Destino: gs://plantas_pv_nl/CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2/')).toBeInTheDocument()
    expect(screen.getByText('Leyendo el bucket…')).toBeInTheDocument()
    emitir({ kind: 'estado', fase: 'urgentes', prefix: 'CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2/', mbps: 16, files_per_s: 3, eta: 60, urgentes: { hechos: 3, total: 10 }, resto: { hechos: 0, total: 0 } })
    expect(await screen.findByText('3,0 img/s')).toBeInTheDocument()
    expect(screen.getByText('~01:00')).toBeInTheDocument()
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
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', true))
  })

  it('un done con continua:true no cierra el estado «subiendo»', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'done', ok: true, modo: 'urgencia', continua: true,
      urgentes: { hechos: 5, total: 5 }, resto: { hechos: 0, total: 3 }, conflictos: [], fallidas: [], avisos: [] })
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
    emitir({ kind: 'done', ok: true, modo: 'normal', continua: false,
      urgentes: { hechos: 5, total: 5 }, resto: { hechos: 3, total: 3 }, conflictos: [], fallidas: [], avisos: [] })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar urgentes' })).not.toBeDisabled())
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
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
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
    expect(api.resultadoInspeccionarDestino).toHaveBeenCalledWith('/salida', 7, expect.any(AbortSignal))
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
    expect(await screen.findByText(/Destino completo: 120 objetos \/ 2,5 GB/)).toBeInTheDocument()
    expect(screen.getByText(/2 ficheros pequeños \(CSV, estadillos, índice\) que se sobrescriben — ver lista/)).toBeInTheDocument()
    expect(screen.getByText('TERMICA, RGB')).toBeInTheDocument()
  })

  it('con 0 pendientes los botones quedan desactivados con «Todo subido»', async () => {
    const g = { total: 5, en_bucket: 5, pendientes: 0, distintos: 0, bytes_pendientes: 0, bytes_total: 3e9 }
    api.resultadoInspeccionarDestino.mockResolvedValue({ existe: true, n_objetos: 10, bytes: 1e9, sobrescribe: [],
      detalle: '', desglose: { urgentes: g, resto: g } })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    const b = await screen.findByRole('button', { name: 'Publicar urgentes' })
    await waitFor(() => expect(b).toHaveAttribute('title', 'Todo subido'))
    expect(b).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Subir todo' })).toBeDisabled()
    expect(screen.getAllByText(/3(,0)? GB del grupo/).length).toBeGreaterThan(0)
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
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
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
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar urgentes' })).not.toBeDisabled())
    api.resultadoCredencial.mockResolvedValue({ ok: false, reason: 'Sesión caducada' })
    fireEvent.click(screen.getByRole('button', { name: 'Publicar urgentes' }))
    expect(await screen.findByText('Sesión caducada')).toBeInTheDocument()
    expect(api.resultadoPrechequeo).not.toHaveBeenCalled()
  })

  it('recomprueba la credencial al volver el foco a la ventana', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publicar urgentes' })).not.toBeDisabled())
    api.resultadoCredencial.mockResolvedValue({ ok: false, reason: 'Login perdido' })
    act(() => { window.dispatchEvent(new Event('focus')) })
    expect(await screen.findByText('Login perdido')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
  })
})

const med = (o = {}) => ({ disco_s: 0, red_s: 0, sesion_s: 0, espera_s: 0, token_s: 0, reintentos: { '429': 0, '5xx': 0, auth: 0, http: 0, red: 0 }, ...o })
const estadoBase = { kind: 'estado', fase: 'urgentes', modo: 'normal', prefix: 'P/', urgentes: { hechos: 5, total: 10 }, resto: { hechos: 0, total: 30 },
  bytes_done: 3e9, bytes_total: 12e9, hilos_activos: 12, hilos_max: 16, eta: 90 }

describe('PanelResultado: progreso, gráfica y cuello de botella', () => {
  it('pinta el % grande, los KPI y la gráfica con la serie en vivo', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'normal' })
    emitir({ ...estadoBase, t: 1, bytes_enviados: 0, medidas: med() })
    emitir({ ...estadoBase, t: 11, bytes_enviados: 200e6, medidas: med({ red_s: 100 }) })
    expect(await screen.findByText('25 %')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Progreso de la subida' })).toHaveAttribute('aria-valuenow', '25')
    expect(screen.getByText('3,0 GB / 12,0 GB')).toBeInTheDocument()
    expect(screen.getByText('5 / 40')).toBeInTheDocument()
    expect(screen.getByText('~01:30')).toBeInTheDocument()
    expect(screen.getByText('12/16 hilos activos')).toBeInTheDocument()
    expect(screen.getAllByText('20,0 MB/s', { selector: '.pu-chip-val' })).toHaveLength(2)  // ahora y media: 200 MB en 10 s
    expect(screen.getByTestId('grafica-subida')).toBeInTheDocument()
  })

  it('enseña el veredicto del cuello solo con lo medido, y «Midiendo…» antes de tener muestra', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'normal' })
    emitir({ ...estadoBase, t: 2, bytes_enviados: 1e6, medidas: med({ red_s: 1 }) })
    expect(await screen.findByText('Midiendo…')).toBeInTheDocument()
    emitir({ ...estadoBase, t: 60, bytes_enviados: 900e6, mbps: 120, medidas: med({ red_s: 90, disco_s: 5, sesion_s: 5 }) })
    expect(await screen.findByText('Limitado por red (120 Mbps)')).toBeInTheDocument()
  })

  it('un backend sin medición no inventa veredicto ni gráfica', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ kind: 'estado', fase: 'urgentes', urgentes: { hechos: 1, total: 4 }, resto: { hechos: 0, total: 0 } })
    expect(screen.queryByText(/Limitado por/)).toBeNull()
    expect(screen.getByTestId('grafica-vacia')).toBeInTheDocument()
  })

  it('al terminar deja el resumen con la tarjeta, el desglose por tipo y vuelo y la gráfica', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'normal' })
    emitir({ ...estadoBase, t: 1, bytes_enviados: 0, medidas: med() })
    emitir({ ...estadoBase, t: 30, bytes_enviados: 600e6, medidas: med({ red_s: 100 }) })
    const g = { omitidos: 0, pendientes: 2, bytes_omitidos: 0, por_tipo: {}, vuelos: 2 }
    emitir({ kind: 'done', ok: true, modo: 'normal', continua: false,
      urgentes: { hechos: 2, total: 2 }, resto: { hechos: 0, total: 0 }, conflictos: [], fallidas: [], avisos: [],
      metricas: {
        duracion_s: 1052, bytes_enviados: 23.7e9, hilos_max: 16, conflictos_n: 0,
        medidas: med({ red_s: 900, disco_s: 20, sesion_s: 30 }),
        urgentes: { ...g, subidos: 12122, bytes_subidos: 23.7e9,
          subidos_por_tipo: { tiff_termica: { ficheros: 12000, bytes: 23e9 }, csv: { ficheros: 122, bytes: 7e8 } },
          subidos_por_vuelo: { PB1_V1: { ficheros: 6000, bytes: 12e9 }, PB1_V2: { ficheros: 6122, bytes: 11.7e9 } } },
      } })
    expect(await screen.findByText('12.122 ficheros · 23,7 GB · 17 min 32 s · 22,5 MB/s · 2 vuelos')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Subido por tipo' })).toBeInTheDocument()
    expect(screen.getByText('TIFF térmica')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Subido por vuelo' })).toBeInTheDocument()
    expect(screen.getByText('PB1_V2')).toBeInTheDocument()
    expect(screen.getByTestId('grafica-subida')).toBeInTheDocument()   // la gráfica se queda
    expect(screen.getByText(/Limitado por red/)).toBeInTheDocument()   // veredicto final con métricas finales
    expect(screen.getByText('Subida completa')).toBeInTheDocument()
  })

  it('«Subir el resto» solo se habilita con pendientes y no pide aviso previo', async () => {
    api.resultadoInspeccionarDestino.mockResolvedValue({ ok: true, existe: true, n_objetos: 10, bytes: 1e9, sobrescribe: [],
      desglose: { urgentes: { total: 10, en_bucket: 10, pendientes: 0, distintos: 0, bytes_pendientes: 0 },
        resto: { total: 50, en_bucket: 20, pendientes: 30, distintos: 0, bytes_pendientes: 3e9 } } })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText('20', { selector: 'strong' })).toBeInTheDocument()   // «20 / 50 en bucket»
    const btn = await screen.findByRole('button', { name: /Subir el resto \(30 · 3,0 GB\)/ })
    expect(btn).not.toBeDisabled()
    fireEvent.click(btn)
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'normal', false))
  })

  it('avisa del estado de actividad a la pestaña', async () => {
    const onActividad = vi.fn()
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready onActividad={onActividad} />)
    emitir({ kind: 'start', modo: 'urgencia' })
    emitir({ ...estadoBase, modo: 'urgencia', t: 5, bytes_enviados: 1e6, medidas: med() })
    await waitFor(() => expect(onActividad).toHaveBeenLastCalledWith({ subiendo: true, pct: 25, error: false }))
    emitir({ kind: 'error', text: 'boom' })
    await waitFor(() => expect(onActividad).toHaveBeenLastCalledWith(expect.objectContaining({ subiendo: false, error: true })))
  })
})

describe('resumenMetricas', () => {
  it('sin desglose de lo subido (backend antiguo) usa el máximo de vuelos de las selecciones', () => {
    const r = resumenMetricas({ duracion_s: 100, urgentes: { subidos: 10, bytes_subidos: 1e9, vuelos: 3 }, resto: { subidos: 5, bytes_subidos: 1e9, vuelos: 7 } })
    expect(r.titulo).toBe('15 ficheros · 2,0 GB · 1 min 40 s · 20,0 MB/s · 7 vuelos')
  })

  it('sin clasificar: lista ruta+motivo, aviso visible y nunca «Todo subido»/«Nada pendiente»', async () => {
    const g = { total: 5, en_bucket: 5, pendientes: 0, distintos: 0, bytes_pendientes: 0, bytes_total: 3e9 }
    api.resultadoInspeccionarDestino.mockResolvedValue({ existe: true, n_objetos: 10, bytes: 1e9, sobrescribe: [], detalle: '',
      desglose: { urgentes: g, resto: g }, sin_clasificar: 2,
      sin_clasificar_lista: [{ ruta: 'CSVs/otro.csv', motivo: 'CSV fuera de CSVs' }, { ruta: 'X/y.bin', motivo: 'carpeta X/ no reconocida' }] })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText('2 fichero(s) sin clasificar, no se subirán')).toBeInTheDocument()
    expect(screen.getByText(/CSVs\/otro\.csv — CSV fuera de CSVs/)).toBeInTheDocument()
    expect(screen.getByText(/X\/y\.bin — carpeta X\/ no reconocida/)).toBeInTheDocument()
    expect(screen.queryByText('Nada pendiente')).toBeNull()
    expect(screen.getByRole('button', { name: 'Subir todo' })).not.toHaveAttribute('title', 'Todo subido')
    emitir({ kind: 'done', ok: true, modo: 'normal', continua: false, urgentes: { hechos: 5, total: 5 }, resto: { hechos: 5, total: 5 },
      conflictos: [], fallidas: [], avisos: [], sin_clasificar: 2, sin_clasificar_lista: [{ ruta: 'a', motivo: 'm' }] })
    expect(screen.queryByText('Subida completa')).toBeNull()
    expect(await screen.findByText(/Subida incompleta: hay ficheros sin clasificar/)).toBeInTheDocument()
  })

  it('casilla «Al terminar, subir el resto»: marcada, se envía y se puede quitar en curso', async () => {
    const g = { total: 5, en_bucket: 0, pendientes: 5, distintos: 0, bytes_pendientes: 10, bytes_total: 10 }
    api.resultadoInspeccionarDestino.mockResolvedValue({ existe: false, n_objetos: 0, bytes: 0, sobrescribe: [], detalle: '',
      desglose: { urgentes: g, resto: g } })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    const cb = await screen.findByRole('checkbox', { name: 'Al terminar, subir el resto' })
    expect(cb).toBeChecked()
    await publicar()
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', true))
    emitir({ kind: 'start', modo: 'urgencia', resto: true })
    expect(await screen.findByText('Resto en cola')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Al terminar, subir el resto' }))
    expect(api.resultadoEncadenar).toHaveBeenCalledWith(false)
    await waitFor(() => expect(screen.queryByText('Resto en cola')).toBeNull())
  })

  it('casilla desactivada si el resto no tiene pendientes', async () => {
    const u = { total: 5, en_bucket: 0, pendientes: 5, distintos: 0, bytes_pendientes: 10, bytes_total: 10 }
    const r0 = { total: 5, en_bucket: 5, pendientes: 0, distintos: 0, bytes_pendientes: 0, bytes_total: 10 }
    api.resultadoInspeccionarDestino.mockResolvedValue({ existe: false, n_objetos: 0, bytes: 0, sobrescribe: [], detalle: '',
      desglose: { urgentes: u, resto: r0 } })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Al terminar, subir el resto' })).toBeDisabled())
    await publicar()
    await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', false))
  })

  it('«Resto en cola» solo si el backend confirma fase de resto', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    emitir({ kind: 'start', modo: 'urgencia', resto: false })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument())
    expect(screen.queryByText('Resto en cola')).toBeNull()
  })

  it('casilla desmarcada antes de arrancar: al recibir start se reenvía al backend', async () => {
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    const cb = await screen.findByRole('checkbox', { name: 'Al terminar, subir el resto' })
    fireEvent.click(cb)
    expect(api.resultadoEncadenar).not.toHaveBeenCalled()
    emitir({ kind: 'start', modo: 'urgencia', resto: true })
    await waitFor(() => expect(api.resultadoEncadenar).toHaveBeenCalledWith(false))
    expect(screen.queryByText('Resto en cola')).toBeNull()
  })
})


describe('PanelResultado: estado automático', () => {
  const dest = (n) => ({ ok: true, existe: true, n_objetos: 1, bytes: 1, sobrescribe: [],
    desglose: { urgentes: { total: n, en_bucket: 0, pendientes: n, distintos: 0, bytes_total: 10, bytes_pendientes: 10 },
                resto: { total: n, en_bucket: 0, pendientes: n, distintos: 0, bytes_total: 10, bytes_pendientes: 10 } } })
  const diferida = () => { let res, rej; const p = new Promise((a, b) => { res = a; rej = b }); return { p, res, rej } }

  it('calcula solo al montar, sin pulsar «Actualizar estado»', async () => {
    api.resultadoInspeccionarDestino.mockResolvedValue(dest(4))
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    await waitFor(() => expect(api.resultadoInspeccionarDestino).toHaveBeenCalledWith('/salida', 7, expect.any(AbortSignal)))
    expect((await screen.findAllByText(/4 pendientes/)).length).toBeGreaterThan(0)
  })

  it('al cambiar carpeta o inspección recalcula (con debounce: una sola llamada si cambian seguidas)', async () => {
    api.resultadoInspeccionarDestino.mockResolvedValue(dest(1))
    const { rerender } = render(<PanelResultado carpeta="/a" inspeccionId={7} ready />)
    await waitFor(() => expect(api.resultadoInspeccionarDestino).toHaveBeenCalledTimes(1))
    rerender(<PanelResultado carpeta="/b" inspeccionId={7} ready />)
    rerender(<PanelResultado carpeta="/b" inspeccionId={8} ready />)
    await waitFor(() => expect(api.resultadoInspeccionarDestino).toHaveBeenCalledWith('/b', 8, expect.any(AbortSignal)))
    expect(api.resultadoInspeccionarDestino).toHaveBeenCalledTimes(2)
  })

  it('mientras calcula: spinner, «Calculando estado…», botón y subidas desactivados', async () => {
    const d = diferida()
    api.resultadoInspeccionarDestino.mockReturnValue(d.p)
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText(/Calculando estado…/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Actualizar estado/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Subir todo' })).toBeDisabled()
    await act(async () => { d.res(dest(2)) })
    await waitFor(() => expect(screen.queryByText(/Calculando estado…/)).toBeNull())
    expect(screen.getByRole('button', { name: /Actualizar estado/ })).not.toBeDisabled()
    expect(screen.getByRole('button', { name: 'Publicar urgentes' })).not.toBeDisabled()
  })

  it('descarta la respuesta de una selección anterior', async () => {
    const a = diferida(); const b = diferida()
    api.resultadoInspeccionarDestino.mockReturnValueOnce(a.p).mockReturnValueOnce(b.p)
    const { rerender } = render(<PanelResultado carpeta="/a" inspeccionId={7} ready />)
    await waitFor(() => expect(api.resultadoInspeccionarDestino).toHaveBeenCalledTimes(1))
    rerender(<PanelResultado carpeta="/b" inspeccionId={7} ready />)
    await act(async () => { a.res(dest(111)) })
    expect(screen.queryByText(/111/)).toBeNull()
    expect(screen.getByText(/Calculando estado…/)).toBeInTheDocument()
    await waitFor(() => expect(api.resultadoInspeccionarDestino).toHaveBeenCalledTimes(2))
    await act(async () => { b.res(dest(222)) })
    expect(await screen.findAllByText(/222/)).not.toHaveLength(0)
    expect(screen.queryByText(/111/)).toBeNull()
  })

  it('si falla muestra el error y «Reintentar» vuelve a calcular', async () => {
    api.resultadoInspeccionarDestino.mockRejectedValueOnce(new Error('sin red')).mockResolvedValue(dest(3))
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText(/No se pudo calcular el estado: sin red/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    await waitFor(() => expect(screen.queryByText(/No se pudo calcular el estado/)).toBeNull())
    expect(api.resultadoInspeccionarDestino).toHaveBeenCalledTimes(2)
  })

  it('carpeta inexistente: el error del backend se ve en el panel y los KPIs no se quedan mudos', async () => {
    api.resultadoInspeccionarDestino.mockResolvedValue({ ok: false, error: 'La carpeta D:\\CASTRUM_ORG no existe o no es accesible' })
    render(<PanelResultado carpeta="D:\\CASTRUM_ORG" inspeccionId={7} ready />)
    expect(await screen.findByText(/La carpeta D:\\CASTRUM_ORG no existe o no es accesible/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument()
  })

  it('una excepción del bridge (método roto) no se traga: error visible', async () => {
    api.resultadoInspeccionarDestino.mockImplementation(() => { throw new Error('bridge roto') })
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText(/bridge roto/)).toBeInTheDocument()
  })

  it('timeout/rechazo del bridge: error visible y el panel no se queda calculando', async () => {
    api.resultadoInspeccionarDestino.mockRejectedValue(new Error('El backend no respondió a la inspección del destino en 75 s.'))
    render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
    expect(await screen.findByText(/no respondió a la inspección del destino en 75 s/)).toBeInTheDocument()
    expect(screen.queryByText(/Calculando estado…/)).toBeNull()
  })

  it('cambiar de carpeta cancela (abort) la petición anterior', async () => {
    const señales = []
    api.resultadoInspeccionarDestino.mockImplementation((_c, _i, signal) => { señales.push(signal); return new Promise(() => {}) })
    const { rerender } = render(<PanelResultado carpeta="/a" inspeccionId={7} ready />)
    await waitFor(() => expect(señales).toHaveLength(1))
    expect(señales[0].aborted).toBe(false)
    rerender(<PanelResultado carpeta="/b" inspeccionId={7} ready />)
    expect(señales[0].aborted).toBe(true)
    await waitFor(() => expect(señales).toHaveLength(2))
    expect(señales[1].aborted).toBe(false)
  })

  it('al desmontar el panel cancela la petición en curso', async () => {
    let señal
    api.resultadoInspeccionarDestino.mockImplementation((_c, _i, signal) => { señal = signal; return new Promise(() => {}) })
    const { unmount } = render(<PanelResultado carpeta="/a" inspeccionId={7} ready />)
    await waitFor(() => expect(señal).toBeDefined())
    unmount()
    expect(señal.aborted).toBe(true)
  })

  describe('Sobrescribir todo (forzar resubida)', () => {
    const grupo = (total, bytes) => ({ total, en_bucket: total - 1, pendientes: 1, distintos: 0, bytes_pendientes: 1, bytes_total: bytes })
    beforeEach(() => {
      api.resultadoInspeccionarDestino.mockResolvedValue({
        existe: true, n_objetos: 5, bytes: 10, sobrescribe: [],
        desglose: { urgentes: grupo(10, 2e9), resto: grupo(30, 3e9) },
      })
    })

    it('la casilla sale desmarcada y sin marcarla no se manda el flag', async () => {
      render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
      await pulsarPublicar()
      expect(await screen.findByLabelText('Sobrescribir todo (forzar resubida)')).not.toBeChecked()
      fireEvent.click(screen.getByRole('button', { name: 'Confirmar y subir' }))
      await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalled())
      expect(api.resultadoSubir.mock.calls[0]).toHaveLength(4)
    })

    it('marcada: pide confirmación con ficheros y GB, y al aceptar manda forzar=true', async () => {
      render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
      await pulsarPublicar()
      fireEvent.click(await screen.findByLabelText('Sobrescribir todo (forzar resubida)'))
      fireEvent.click(screen.getByRole('button', { name: 'Confirmar y subir' }))
      expect(await screen.findByText(/40 ficheros \(5,0 GB\)/)).toBeInTheDocument()
      expect(screen.getByText(/incluidos los que están en conflicto/)).toBeInTheDocument()
      expect(api.resultadoSubir).not.toHaveBeenCalled()
      fireEvent.click(screen.getByRole('button', { name: 'Sí' }))
      await waitFor(() => expect(api.resultadoSubir).toHaveBeenCalledWith('/salida', 7, 'urgencia', true, true))
    })

    it('marcada y rechazada la confirmación: no sube', async () => {
      render(<PanelResultado carpeta="/salida" inspeccionId={7} ready />)
      await pulsarPublicar()
      fireEvent.click(await screen.findByLabelText('Sobrescribir todo (forzar resubida)'))
      fireEvent.click(screen.getByRole('button', { name: 'Confirmar y subir' }))
      fireEvent.click(await screen.findByRole('button', { name: 'No' }))
      expect(api.resultadoSubir).not.toHaveBeenCalled()
    })
  })
})
