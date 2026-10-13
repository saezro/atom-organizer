import { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: { cloudInspecciones: vi.fn() },
}))
import { api } from '../bridge'
import PasoInspeccion from './PasoInspeccion'

beforeEach(() => vi.clearAllMocks())

describe('PasoInspeccion', () => {
  it('carga el catálogo al montar', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' })
    render(<PasoInspeccion ready prefijo="" onChange={() => {}} />)
    await waitFor(() => expect(api.cloudInspecciones).toHaveBeenCalled())
  })

  it('avisa cuando el catálogo falla', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: false, error: 'sin conexión' })
    render(<PasoInspeccion ready prefijo="" onChange={() => {}} />)
    expect(await screen.findByText(/sin conexión/)).toBeTruthy()
  })

  it('una vez elegida muestra el prefijo y deja cambiarlo', async () => {
    api.cloudInspecciones.mockResolvedValue({ ok: true, inspecciones: [], origen: 'bucket' })
    const onChange = vi.fn()
    render(<PasoInspeccion ready prefijo="ACME--PLANTA--2026--TERMO" onChange={onChange} />)
    expect(await screen.findByDisplayValue('ACME--PLANTA--2026--TERMO')).toBeTruthy()
    fireEvent.click(screen.getByText(/Cambiar/i))
    expect(onChange).toHaveBeenCalledWith('', null)
  })
})

describe('PasoInspeccion: sin bucle con el estadillo', () => {
  it('si al elegir la inspección cambia el estadillo, no re-elige en bucle', async () => {
    api.cloudInspecciones.mockResolvedValue({
      ok: true, origen: 'api',
      inspecciones: [{ prefijo: 'ACME--PLANTA--2026--TERMO', etiqueta: 'ACME planta' }],
    })
    api.inspeccionSugerir = vi.fn().mockResolvedValue({
      estado: 'unica', prefijo: 'ACME--PLANTA--2026--TERMO', candidatos: ['ACME--PLANTA--2026--TERMO'],
    })
    const onChange = vi.fn()
    // Padre que imita TrabajoScreen + PasoEstadillo: al cambiar el prefijo el
    // estadillo se vacía y se vuelve a autodetectar (nuevas rutas).
    function Padre() {
      const [prefijo, setPrefijo] = useState('')
      const [rutas, setRutas] = useState([])
      return (
        <PasoInspeccion
          ready carpeta="/v/2026_01_01_PLANTA" prefijo={prefijo} estadilloRutas={rutas}
          onChange={(p, i) => {
            onChange(p, i)
            setPrefijo(p)
            setRutas(p ? ['/e/estadillo.xlsx'] : [])
          }}
        />
      )
    }
    render(<Padre />)
    expect(await screen.findByDisplayValue(/ACME/)).toBeTruthy()
    await new Promise((r) => setTimeout(r, 200))
    expect(api.inspeccionSugerir.mock.calls.length).toBeLessThanOrEqual(2)
    expect(onChange.mock.calls.length).toBeLessThanOrEqual(2)
    expect(onChange).not.toHaveBeenCalledWith('', null)
    expect(screen.getByDisplayValue(/ACME/)).toBeTruthy()
  })
})

describe('PasoInspeccion: cambio de carpeta y elección manual', () => {
  const catalogo = {
    ok: true, origen: 'api',
    inspecciones: [
      { prefijo: 'A--UNO--2026--TERMO', etiqueta: 'Uno' },
      { prefijo: 'B--DOS--2026--TERMO', etiqueta: 'Dos' },
    ],
  }
  const original = api.inspeccionSugerir
  afterEach(() => { api.inspeccionSugerir = original })

  function Padre({ carpeta, rutas = [], onChange }) {
    const [prefijo, setPrefijo] = useState('')
    return (
      <PasoInspeccion
        ready carpeta={carpeta} prefijo={prefijo} estadilloRutas={rutas}
        onChange={(p, i) => { onChange?.(p, i); setPrefijo(p) }}
      />
    )
  }

  it('al cambiar de carpeta tras elección automática, consulta y aplica la nueva', async () => {
    api.cloudInspecciones.mockResolvedValue(catalogo)
    api.inspeccionSugerir = vi.fn(async (carpeta) => {
      const prefijo = carpeta.includes('UNO') ? 'A--UNO--2026--TERMO' : 'B--DOS--2026--TERMO'
      return { estado: 'unica', prefijo, candidatos: [prefijo] }
    })
    const { rerender } = render(<Padre carpeta="/v/2026_01_01_UNO" />)
    expect(await screen.findByDisplayValue('Uno')).toBeTruthy()
    rerender(<Padre carpeta="/v/2026_02_02_DOS" />)
    expect(await screen.findByDisplayValue('Dos')).toBeTruthy()
    expect(api.inspeccionSugerir).toHaveBeenCalledTimes(2)
  })

  it('una elección manual no se pisa al cambiar estadillo ni carpeta', async () => {
    api.cloudInspecciones.mockResolvedValue(catalogo)
    api.inspeccionSugerir = vi.fn().mockResolvedValue({
      estado: 'unica', prefijo: 'B--DOS--2026--TERMO', candidatos: ['B--DOS--2026--TERMO'],
    })
    const onChange = vi.fn()
    const { rerender } = render(
      <PasoInspeccion ready carpeta="/v/x" prefijo="A--UNO--2026--TERMO" estadilloRutas={[]} onChange={onChange} />,
    )
    expect(await screen.findByDisplayValue('Uno')).toBeTruthy()
    rerender(
      <PasoInspeccion ready carpeta="/v/x" prefijo="A--UNO--2026--TERMO" estadilloRutas={['/e/a.xlsx']} onChange={onChange} />,
    )
    rerender(
      <PasoInspeccion ready carpeta="/v/y" prefijo="A--UNO--2026--TERMO" estadilloRutas={['/e/b.xlsx']} onChange={onChange} />,
    )
    await new Promise((r) => setTimeout(r, 100))
    expect(onChange).not.toHaveBeenCalled()
    expect(api.inspeccionSugerir).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue('Uno')).toBeTruthy()
  })
})
