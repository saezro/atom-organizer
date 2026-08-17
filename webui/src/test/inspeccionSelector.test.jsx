/**
 * InspeccionSelector: buscador + chips de fase + toggle de terminadas +
 * lista agrupada por año (desc) y ordenada por fase dentro de cada año.
 *
 * Tests directos sobre el componente (sin pasar por <App>): las props que
 * necesita son solo `inspecciones`, `onElegir`, `onNueva`, `ocupado` y
 * `onActualizar`.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import InspeccionSelector from '../InspeccionSelector'

function inspeccion(over) {
  return {
    empresa: 'ACME',
    planta: 'PLANTA1',
    anio: 2026,
    tipo: 'PV',
    id: '1',
    fase: 'Vuelo',
    prefijo: 'PREFIJO1',
    etiqueta: 'PREFIJO1',
    ...over,
  }
}

function montar(inspecciones) {
  render(
    <InspeccionSelector
      inspecciones={inspecciones}
      onElegir={vi.fn()}
      onNueva={vi.fn()}
      ocupado={false}
      onActualizar={vi.fn()}
    />,
  )
}

describe('InspeccionSelector', () => {
  it('ordena las inspecciones de fase Vuelo antes que las de Preparacion', () => {
    montar([
      inspeccion({ prefijo: 'PREP', etiqueta: 'PREP', fase: 'Preparacion' }),
      inspeccion({ prefijo: 'VUELO', etiqueta: 'VUELO', fase: 'Vuelo' }),
    ])

    const botones = screen.getAllByRole('button').map((b) => b.textContent)
    const idxVuelo = botones.indexOf('VUELO')
    const idxPrep = botones.indexOf('PREP')

    expect(idxVuelo).toBeLessThan(idxPrep)
  })

  it('agrupa por año en orden descendente', () => {
    montar([
      inspeccion({ prefijo: 'A2025', etiqueta: 'A2025', anio: 2025 }),
      inspeccion({ prefijo: 'A2026', etiqueta: 'A2026', anio: 2026 }),
    ])

    const anios = screen.getAllByText(/^(2025|2026)$/).map((n) => n.textContent)
    expect(anios).toEqual(['2026', '2025'])
  })

  it('el buscador filtra por año y por varias palabras a la vez', async () => {
    const user = userEvent.setup()
    montar([
      inspeccion({ prefijo: 'ACME2025', etiqueta: 'ACME2025', empresa: 'ACME', anio: 2025 }),
      inspeccion({ prefijo: 'ACME2026', etiqueta: 'ACME2026', empresa: 'ACME', anio: 2026 }),
      inspeccion({ prefijo: 'OTRA2025', etiqueta: 'OTRA2025', empresa: 'OTRA', anio: 2025 }),
    ])

    const input = screen.getByPlaceholderText(/Escribe para buscar/i)

    await user.type(input, '2025')
    expect(screen.getByRole('button', { name: 'ACME2025' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'OTRA2025' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'ACME2026' })).not.toBeInTheDocument()

    await user.clear(input)
    await user.type(input, 'acme 2025')
    expect(screen.getByRole('button', { name: 'ACME2025' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'OTRA2025' })).not.toBeInTheDocument()
  })

  it('no renderiza por defecto las inspecciones Terminado ni Cancelada', () => {
    montar([
      inspeccion({ prefijo: 'VIVA', etiqueta: 'VIVA', fase: 'Vuelo' }),
      inspeccion({ prefijo: 'FIN', etiqueta: 'FIN', fase: 'Terminado' }),
      inspeccion({ prefijo: 'CANC', etiqueta: 'CANC', fase: 'Cancelada' }),
    ])

    expect(screen.getByRole('button', { name: 'VIVA' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'FIN' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'CANC' })).not.toBeInTheDocument()
  })

  it('muestra el aviso de terminadas ocultas con el recuento', () => {
    montar([
      inspeccion({ prefijo: 'VIVA', etiqueta: 'VIVA', fase: 'Vuelo' }),
      inspeccion({ prefijo: 'FIN', etiqueta: 'FIN', fase: 'Terminado' }),
    ])

    const aviso = screen.getByText(/terminada oculta/i)
    expect(aviso).toHaveClass('hint-warn')
    expect(aviso.textContent).toMatch(/^1 terminada oculta/)
  })

  it('activar «Mostrar terminadas y canceladas» hace aparecer las terminadas', async () => {
    const user = userEvent.setup()
    montar([
      inspeccion({ prefijo: 'VIVA', etiqueta: 'VIVA', fase: 'Vuelo' }),
      inspeccion({ prefijo: 'FIN', etiqueta: 'FIN', fase: 'Terminado' }),
    ])

    expect(screen.queryByRole('button', { name: 'FIN' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: /Mostrar terminadas y canceladas/i }))

    expect(screen.getByRole('button', { name: 'FIN' })).toBeInTheDocument()
  })

  it('los chips de fase filtran en multi-selección', async () => {
    const user = userEvent.setup()
    montar([
      inspeccion({ prefijo: 'A', etiqueta: 'A', fase: 'Vuelo' }),
      inspeccion({ prefijo: 'B', etiqueta: 'B', fase: 'Preparacion' }),
      inspeccion({ prefijo: 'C', etiqueta: 'C', fase: 'Analisis' }),
    ])

    await user.click(screen.getByRole('button', { name: /^Vuelo/ }))
    expect(screen.getByRole('button', { name: 'A' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'B' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'C' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Preparacion/ }))
    expect(screen.getByRole('button', { name: 'A' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'B' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'C' })).not.toBeInTheDocument()
  })

  it('corta a 50 resultados y muestra el hint con el total real', () => {
    const inspecciones = Array.from({ length: 60 }, (_, n) =>
      inspeccion({ prefijo: `I${n}`, etiqueta: `I${n}`, fase: 'Vuelo' }),
    )
    montar(inspecciones)

    const items = screen
      .getAllByRole('button')
      .filter((b) => b.classList.contains('insp-item') && !b.classList.contains('insp-nueva'))
    expect(items).toHaveLength(50)

    expect(screen.getByText(/60 coinciden; se muestran las 50 primeras/)).toBeInTheDocument()
  })

  it('el corte a 50 respeta la prioridad de fase: las de Vuelo minoritarias entran', () => {
    const analisis = Array.from({ length: 55 }, (_, n) =>
      inspeccion({ prefijo: `AN${n}`, etiqueta: `AN${n}`, fase: 'Analisis' }),
    )
    const vuelo = [
      inspeccion({ prefijo: 'V1', etiqueta: 'V1', fase: 'Vuelo' }),
      inspeccion({ prefijo: 'V2', etiqueta: 'V2', fase: 'Vuelo' }),
    ]
    montar([...analisis, ...vuelo])

    expect(screen.getByRole('button', { name: 'V1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'V2' })).toBeInTheDocument()
  })

  it('un chip activo que deja de casar con el texto no deja la lista vacía', async () => {
    const user = userEvent.setup()
    montar([
      inspeccion({ prefijo: 'ACMEV', etiqueta: 'ACMEV', empresa: 'ACME', fase: 'Vuelo', anio: 2023 }),
      inspeccion({ prefijo: 'ACME24', etiqueta: 'ACME24', empresa: 'ACME', fase: 'Analisis', anio: 2024 }),
    ])

    const input = screen.getByPlaceholderText(/Escribe para buscar/i)

    await user.type(input, 'acme')
    await user.click(screen.getByRole('button', { name: /^Vuelo/ }))

    await user.clear(input)
    await user.type(input, 'acme 2024')

    expect(screen.getByRole('button', { name: 'ACME24' })).toBeInTheDocument()
  })

  it('un año no numérico se ordena al final, nunca por delante de un año válido', () => {
    montar([
      inspeccion({ prefijo: 'A2026', etiqueta: 'A2026', anio: 2026 }),
      inspeccion({ prefijo: 'A2025', etiqueta: 'A2025', anio: 2025 }),
      inspeccion({ prefijo: 'ANA', etiqueta: 'ANA', anio: 'N/A' }),
    ])

    const anios = screen.getAllByText(/^(2025|2026|N\/A)$/).map((n) => n.textContent)
    expect(anios).toEqual(['2026', '2025', 'N/A'])
  })
})
