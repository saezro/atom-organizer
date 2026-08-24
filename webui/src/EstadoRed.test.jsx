import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

const redConexion = vi.fn()

vi.mock('./bridge.js', () => ({
  api: {
    redConexion: (...args) => redConexion(...args),
  },
}))

import EstadoRed from './EstadoRed.jsx'

describe('EstadoRed', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('pinta el SSID y el porcentaje de senal en wifi', async () => {
    redConexion.mockResolvedValue({ ok: true, tipo: 'wifi', ssid: 'CASA-WIFI', senal: 80, ip: '192.168.1.5' })
    render(<EstadoRed />)
    const chip = await screen.findByTestId('kiosk-estado-red')
    expect(chip).toHaveTextContent('CASA-WIFI')
    expect(chip).toHaveTextContent('80%')
  })

  it('marca el nivel de senal en la clase para el semaforo de color', async () => {
    redConexion.mockResolvedValue({ ok: true, tipo: 'wifi', ssid: 'CASA-WIFI', senal: 20, ip: '192.168.1.5' })
    render(<EstadoRed />)
    const chip = await screen.findByTestId('kiosk-estado-red')
    expect(chip).toHaveClass('kiosk-estado-red-nivel-1')
  })

  it('pinta "Cable" con tipo cable', async () => {
    redConexion.mockResolvedValue({ ok: true, tipo: 'cable', ssid: '', senal: null, ip: '192.168.1.5' })
    render(<EstadoRed />)
    const chip = await screen.findByTestId('kiosk-estado-red')
    expect(chip).toHaveTextContent('Cable')
  })

  it('no pinta nada si la llamada falla', async () => {
    redConexion.mockResolvedValue({ ok: false, error: 'no disponible' })
    render(<EstadoRed />)
    await Promise.resolve()
    expect(screen.queryByTestId('kiosk-estado-red')).not.toBeInTheDocument()
  })

  it('pinta "Sin red" cuando no hay ninguna conexion', async () => {
    redConexion.mockResolvedValue({ ok: true, tipo: 'ninguna', ssid: '', senal: null, ip: '' })
    render(<EstadoRed />)
    const chip = await screen.findByTestId('kiosk-estado-red')
    expect(chip).toHaveTextContent('Sin red')
  })
})
