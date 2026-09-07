import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// `HistorialRuns.jsx` importa `../bridge` (sin extensión): se mockea igual
// que en `EstadilloField.test.jsx`/`PasoEstadillo.test.jsx` para controlar
// logsListar/logsLeer/logsCarpeta sin un backend real.
vi.mock('../bridge', () => ({
  api: {
    logsListar: vi.fn(),
    logsLeer: vi.fn(),
    logsCarpeta: vi.fn(),
  },
}))

import { api } from '../bridge'
import HistorialRuns from './HistorialRuns.jsx'

describe('HistorialRuns', () => {
  beforeEach(() => vi.clearAllMocks())

  it('estado vacío: sin runs, avisa de que no hay procesos registrados', async () => {
    api.logsListar.mockResolvedValue({ ok: true, runs: [] })
    render(<HistorialRuns />)

    expect(await screen.findByText(/todavía no hay procesos registrados/i)).toBeInTheDocument()
  })

  it('estado de error: logsListar falla, muestra el mensaje sin dejar la pantalla en blanco', async () => {
    api.logsListar.mockRejectedValue(new Error('caído'))
    render(<HistorialRuns />)

    expect(await screen.findByText((_, el) => el?.textContent?.includes('caído') && !el.querySelector('*'))).toBeInTheDocument()
  })

  it('agrupa por planta (los sin planta van a "Sin identificar", al final)', async () => {
    api.logsListar.mockResolvedValue({
      ok: true,
      runs: [
        {
          nombre: 'atom-organizer-run_20260101_120000_pid1.log',
          fecha: '2026-01-01T12:00:00',
          planta: 'PLANTA_A',
          task: 'split_images',
          estado: 'ok',
          errores: 0,
          duracion: 12.3,
        },
        {
          nombre: 'atom-organizer-run_20260102_120000_pid2.log',
          fecha: '2026-01-02T12:00:00',
          planta: '',
          task: 'gen_struct_folder',
          estado: 'error',
          errores: 3,
          duracion: null,
        },
      ],
    })
    render(<HistorialRuns />)

    await screen.findByText('PLANTA_A')
    expect(screen.getByText('Sin identificar')).toBeInTheDocument()
    const titulos = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(titulos).toEqual(['PLANTA_A', 'Sin identificar'])
  })

  it('al pulsar una fila carga y muestra el log, con botón para volver', async () => {
    api.logsListar.mockResolvedValue({
      ok: true,
      runs: [
        {
          nombre: 'atom-organizer-run_20260101_120000_pid1.log',
          fecha: '2026-01-01T12:00:00',
          planta: 'PLANTA_A',
          task: 'split_images',
          estado: 'ok',
          errores: 0,
          duracion: 12.3,
        },
      ],
    })
    api.logsLeer.mockResolvedValue({ ok: true, texto: '[log] contenido del run', truncado: false, bytes: 42 })

    render(<HistorialRuns />)
    const fila = await screen.findByTitle('atom-organizer-run_20260101_120000_pid1.log')
    fireEvent.click(fila)

    expect(await screen.findByText('[log] contenido del run')).toBeInTheDocument()
    expect(api.logsLeer).toHaveBeenCalledWith('atom-organizer-run_20260101_120000_pid1.log')

    fireEvent.click(screen.getByRole('button', { name: /volver al historial/i }))
    await waitFor(() => expect(screen.queryByText('[log] contenido del run')).toBeNull())
    expect(screen.getByTitle('atom-organizer-run_20260101_120000_pid1.log')).toBeInTheDocument()
  })

  it('avisa si el log viene truncado', async () => {
    api.logsListar.mockResolvedValue({
      ok: true,
      runs: [
        {
          nombre: 'atom-organizer-run_20260101_120000_pid1.log',
          fecha: '2026-01-01T12:00:00',
          planta: 'PLANTA_A',
          task: 'split_images',
          estado: 'ok',
          errores: 0,
          duracion: 1,
        },
      ],
    })
    api.logsLeer.mockResolvedValue({ ok: true, texto: 'cola', truncado: true, bytes: 5000000 })

    render(<HistorialRuns />)
    fireEvent.click(await screen.findByTitle('atom-organizer-run_20260101_120000_pid1.log'))

    expect(await screen.findByText(/se muestra solo la última parte/i)).toBeInTheDocument()
  })

  it('abre la carpeta de logs y enseña la ruta devuelta', async () => {
    api.logsListar.mockResolvedValue({ ok: true, runs: [] })
    api.logsCarpeta.mockResolvedValue({ ok: true, ruta: '/home/pi/.config/atom-organizer/Logs' })

    render(<HistorialRuns />)
    await screen.findByText(/todavía no hay procesos registrados/i)
    fireEvent.click(screen.getByRole('button', { name: /abrir carpeta de logs/i }))

    expect(await screen.findByText(/\/home\/pi\/\.config\/atom-organizer\/Logs/)).toBeInTheDocument()
  })

  it('toda llamada al bridge va envuelta en try/catch: un fallo no deja la pantalla en blanco', async () => {
    api.logsListar.mockResolvedValue({ ok: true, runs: [] })
    api.logsCarpeta.mockRejectedValue(new Error('sin permiso'))

    render(<HistorialRuns />)
    await screen.findByText(/todavía no hay procesos registrados/i)
    fireEvent.click(screen.getByRole('button', { name: /abrir carpeta de logs/i }))

    expect(await screen.findByText((_, el) => el?.textContent?.includes('sin permiso') && !el.querySelector('*'))).toBeInTheDocument()
  })
})
