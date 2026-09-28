import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EstadilloField from './EstadilloField.jsx'

// Sin selector de fichero local ni campo de texto editable (pedido de
// Rodrigo, 2026-09-22): el estadillo solo llega por `value` (autodetección
// o recepción por red desde fuera), nunca por tecleo. Estos tests solo
// comprueban qué pinta el campo según lo que traiga `value`.
describe('EstadilloField: sin estadillo', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sin ficheros no pinta nada (el "sin estadillo" lo dice el indicador de cada pantalla)', () => {
    const onChange = vi.fn()
    const { container } = render(<EstadilloField value={[]} onChange={onChange} tactil />)
    expect(container).toBeEmptyDOMElement()
  })

  it('no hay ningún botón de selección local (sin diálogo de fichero, pedido de Rodrigo)', () => {
    const onChange = vi.fn()
    render(<EstadilloField value={[]} onChange={onChange} />)
    expect(screen.queryByRole('button', { name: /elegir/i })).toBeNull()
  })
})

describe('EstadilloField: un estadillo (detectado o recibido)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('muestra el nombre de solo lectura, sin campo de texto editable', () => {
    const onChange = vi.fn()
    render(<EstadilloField value={['/vuelo/estadillo.xlsx']} onChange={onChange} tactil />)
    const actual = screen.getByTestId('estadillo-actual')
    expect(actual).toHaveTextContent('estadillo.xlsx')
    expect(actual.tagName).not.toBe('INPUT')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('en escritorio (tactil falsy) sigue siendo de solo lectura', () => {
    const onChange = vi.fn()
    render(<EstadilloField value={['/home/saez/estadillo.csv']} onChange={onChange} />)
    expect(screen.getByTestId('estadillo-actual')).toHaveTextContent('estadillo.csv')
    expect(screen.queryByRole('textbox')).toBeNull()
  })
})

// Estadillo recibido por red (`EsperaEstadillo`, `onRecibido` con segundo
// argumento): tras «OK, seguir» ya no debe verse un input con solo el
// nombre del CSV, sino la tarjeta de solo lectura con el resumen.
describe('EstadilloField: recibido (infoRecibido)', () => {
  beforeEach(() => vi.clearAllMocks())

  const info = {
    origen: 'recibido',
    planta: 'GRIJOTA_V',
    fechas: ['2026-09-23'],
    pilotos: ['Pepe'],
    drones: ['DJI M300'],
    numVuelos: 2,
    vuelos: [
      { n: '1', inicio: '09:00', fin: '09:40', duracionMin: 40 },
      { n: '2', inicio: '10:00', fin: '10:35', duracionMin: 35 },
    ],
    tiempoTotalMin: 75,
    avisos: [],
  }

  it('pinta la tarjeta de solo lectura, no el input de nombre', () => {
    const onChange = vi.fn()
    // Escritorio (sin `tactil`): el cuerpo no se pagina, todo va de una vez
    // (ver test de kiosco más abajo para la paginación).
    render(<EstadilloField value={['/recibidos/estadillo_2026.csv']} onChange={onChange} infoRecibido={info} />)
    expect(screen.getByTestId('estadillo-recibido-card')).toHaveTextContent('Estadillo recibido')
    expect(screen.getByTestId('estadillo-recibido-card')).toHaveTextContent('GRIJOTA_V')
    expect(screen.getByTestId('estadillo-recibido-card')).toHaveTextContent('Pepe')
    expect(screen.getByTestId('estadillo-recibido-card')).toHaveTextContent('DJI M300')
    expect(screen.queryByTestId('estadillo-actual')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('lista los vuelos con hora y duración, y el tiempo de vuelo total', () => {
    const onChange = vi.fn()
    render(<EstadilloField value={['/recibidos/estadillo_2026.csv']} onChange={onChange} infoRecibido={info} />)
    const vuelos = screen.getByTestId('estadillo-recibido-vuelos')
    expect(vuelos).toHaveTextContent('09:00–09:40')
    expect(vuelos).toHaveTextContent('40 min')
    expect(vuelos).toHaveTextContent('10:00–10:35')
    expect(vuelos).toHaveTextContent('35 min')
    expect(screen.getByTestId('estadillo-recibido-total')).toHaveTextContent('1h 15min')
  })

  it('sin infoRecibido vuelve al input de solo lectura de siempre', () => {
    const onChange = vi.fn()
    render(<EstadilloField value={['/vuelo/estadillo.xlsx']} onChange={onChange} />)
    expect(screen.queryByTestId('estadillo-recibido-card')).toBeNull()
    expect(screen.getByTestId('estadillo-actual')).toHaveTextContent('estadillo.xlsx')
  })

  it('«Quitar» llama a onChange con la lista vacía (limpia rutas y, aguas arriba, el resumen)', () => {
    const onChange = vi.fn()
    render(<EstadilloField value={['/recibidos/estadillo_2026.csv']} onChange={onChange} infoRecibido={info} />)
    screen.getByTestId('estad-recibido-quitar').click()
    expect(onChange).toHaveBeenCalledWith([])
  })

  // Bug real: en el kiosco (480x320, panel resistivo sin gesto de arrastre)
  // con 3 vuelos la tarjeta salía cortada y sin forma de llegar al resto.
  // En vez de depender de un scroll que la Pi no puede disparar, el cuerpo
  // se pagina — el total debe quedar alcanzable con los botones ▲/▼.
  it('en kiosco con varios vuelos pagina el cuerpo en vez de depender de scroll', () => {
    const onChange = vi.fn()
    const info3 = {
      ...info,
      numVuelos: 3,
      vuelos: [
        ...info.vuelos,
        { n: '3', inicio: '11:00', fin: '11:20', duracionMin: 20 },
      ],
      tiempoTotalMin: 95,
    }
    render(
      <EstadilloField value={['/recibidos/estadillo_2026.csv']} onChange={onChange} infoRecibido={info3} tactil />
    )
    // Página 1: no cabe todo de una vez, el total NO está aún.
    expect(screen.getByTestId('estadillo-recibido-card')).toHaveTextContent('Estadillo recibido')
    expect(screen.queryByTestId('estadillo-recibido-total')).toBeNull()

    // El total (última sección) se alcanza a base de pulsar ▼, sin
    // arrastrar nada — «Quitar» sigue disponible en cada página.
    const abajo = screen.getByTestId('estadillo-recibido-pag-abajo')
    let vueltas = 0
    while (screen.queryByTestId('estadillo-recibido-total') === null && vueltas < 20) {
      expect(screen.getByTestId('estad-recibido-quitar')).toBeVisible()
      fireEvent.pointerDown(abajo, { pointerId: 1, clientX: 10, clientY: 10 })
      fireEvent.pointerUp(abajo, { pointerId: 1, clientX: 10, clientY: 10 })
      vueltas += 1
    }
    expect(screen.getByTestId('estadillo-recibido-total')).toHaveTextContent('1h 35min')
  })
})

describe('EstadilloField: varios estadillos', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lista los nombres de solo lectura y permite quitar/reordenar', async () => {
    const onChange = vi.fn()
    render(
      <EstadilloField value={['/a/uno.xlsx', '/a/dos.xlsx']} onChange={onChange} />
    )
    expect(screen.getByTestId('estad-nombre-0')).toHaveTextContent('uno.xlsx')
    expect(screen.getByTestId('estad-nombre-1')).toHaveTextContent('dos.xlsx')
    expect(screen.queryByRole('textbox')).toBeNull()

    screen.getByTestId('estad-quitar-0').click()
    expect(onChange).toHaveBeenCalledWith(['/a/dos.xlsx'])
  })
})
