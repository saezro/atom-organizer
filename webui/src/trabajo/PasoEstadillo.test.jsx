import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    estadilloValidar: vi.fn(),
    estadilloSubir: vi.fn(),
    estadilloExistente: vi.fn(),
    estadillosDetectar: vi.fn(),
    estadilloBajarNube: vi.fn(),
  },
  isServerMode: vi.fn(() => false),
  onCloud: (h) => {
    const w = (e) => h(e.detail)
    window.addEventListener('atom:cloud', w)
    return () => window.removeEventListener('atom:cloud', w)
  },
}))
import { api } from '../bridge'
import PasoEstadillo from './PasoEstadillo'

function emitirCloud(detail) {
  act(() => { window.dispatchEvent(new CustomEvent('atom:cloud', { detail })) })
}

// Sin campo de texto editable (pedido de Rodrigo, 2026-09-22): en
// `PasoEstadillo` el estadillo llega por autodetección en la carpeta del
// vuelo (`estadillosDetectar`, mockeada aquí para devolver `ruta`; también
// mira la carpeta de recibidos por LAN, `incluirRecibidos: true`) o eligiendo
// a mano con el botón «Elegir…» (`permitirElegir`, solo aquí, nunca en el
// kiosco). Los tests que necesitan poblar la selección pasan `carpeta` al
// render y esperan a que el campo (de solo lectura) la pinte.
async function elegirEstadillo(ruta = '/home/saez/Descargas/estadillo.xlsx') {
  api.estadillosDetectar.mockResolvedValue({ rutas: [ruta] })
  await screen.findByTestId('estadillo-actual')
}

beforeEach(() => {
  vi.clearAllMocks()
  api.estadilloExistente.mockResolvedValue({ existe: false })
  api.estadillosDetectar.mockResolvedValue({ rutas: [] })
})

describe('PasoEstadillo', () => {
  it('valida el estadillo al elegirlo y reporta listo', async () => {
    api.estadillosDetectar.mockResolvedValue({ rutas: ['/vuelo/estadillo.xlsx'] })
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 3 })
    const onEstado = vi.fn()
    const { rerender } = render(
      <PasoEstadillo prefijo="ACME--P--2026--T" carpeta="/vuelo" onEstado={onEstado} />)
    // la selección llega sola por autodetección de la carpeta del vuelo.
    await elegirEstadillo()
    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.listo).toBe(true)
    })
    await waitFor(() => expect(onEstado).toHaveBeenCalled())
    rerender(<PasoEstadillo prefijo="ACME--P--2026--T" carpeta="/vuelo" onEstado={onEstado} />)
  })

  it('marca listo si se omite el estadillo', async () => {
    const onEstado = vi.fn()
    // Sin estadillo previo, marcar el checkbox pide confirmación
    // (window.confirm, igual que el resto de confirmaciones de la pantalla
    // en App.jsx): se acepta para poder seguir el flujo.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
    // Espera a que se asiente el efecto de `estadilloExistente` (auto-marcado
    // de «omitir») antes de tocar el checkbox a mano: si el click llega antes
    // de que esa respuesta async resuelva, el auto-marcado (que solo se
    // aplica una vez por inspección) pisaría el click con `existe: false`.
    await waitFor(() => expect(api.estadilloExistente).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    // El checkbox replicado tal cual de App.jsx no lleva la palabra "omitir"
    // en su etiqueta visible («Subir sin estadillo» / «Ya subí el estadillo
    // de esta inspección»); se localiza por ese texto real en vez de
    // inventar una etiqueta que cambiaría el UI replicado.
    fireEvent.click(await screen.findByLabelText(/subir sin estadillo/i))
    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.listo).toBe(true)
    })
  })

  it('subir() resuelve cuando llega el evento done del estadillo', async () => {
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    api.estadilloSubir.mockResolvedValue({ started: true })
    let estado = null
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    // sin ficheros elegidos, subir() resuelve solo
    await expect(estado.subir()).resolves.toBeUndefined()
  })

  it('subir() rechaza si el backend dice que no arrancó', async () => {
    api.estadillosDetectar.mockResolvedValue({ rutas: ['/vuelo/estadillo.xlsx'] })
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    api.estadilloSubir.mockResolvedValue({ started: false, reason: 'ya hay una subida' })
    let estado = null
    render(
      <PasoEstadillo prefijo="ACME--P--2026--T" carpeta="/vuelo" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    // este caso requiere ficheros elegidos: llegan solos por autodetección
    // de la carpeta del vuelo (mockeada arriba).
    await elegirEstadillo()
    await waitFor(() => expect(estado.rutas.length).toBe(1))

    await expect(estado.subir()).rejects.toThrow('ya hay una subida')
  })

  it('vacía la selección de estadillo al cambiar de inspección', async () => {
    api.estadillosDetectar.mockResolvedValue({ rutas: ['/vuelo/estadillo.xlsx'] })
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    const onEstado = vi.fn()
    const { rerender } = render(
      <PasoEstadillo prefijo="ACME--P--2026--T" carpeta="/vuelo" onEstado={onEstado} />)
    await elegirEstadillo()
    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.rutas.length).toBe(1)
    })

    onEstado.mockClear()
    // Sin `carpeta` en el rerender para no relanzar la autodetección y poder
    // aislar el reseteo por cambio de inspección (comportamiento ya
    // verificado en los tests de autodetección más abajo).
    rerender(<PasoEstadillo prefijo="OTRA--P--2026--T" onEstado={onEstado} />)

    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.rutas).toEqual([])
    })
  })

  it('no hereda «omitir estadillo» al cambiar a una inspección sin estadillo previo', async () => {
    // Regresión: `estadPrevio` (respuesta de `api.estadilloExistente`) se
    // quedaba con el valor de la inspección A hasta que resolvía el fetch de
    // la B, y el efecto de auto-marcado corría en ese mismo flush con el
    // valor viejo, dejando `omitirEstadillo = true` heredado en una
    // inspección que NO tiene estadillo subido.
    api.estadilloExistente.mockImplementation((prefijo) =>
      Promise.resolve({ existe: prefijo === 'ACME--P--2026--T' }))
    const onEstado = vi.fn()
    const { rerender } = render(
      <PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)

    await waitFor(() => expect(screen.getByLabelText(/estadillo de esta inspección/i).checked).toBe(true))

    rerender(<PasoEstadillo prefijo="OTRA--P--2026--T" onEstado={onEstado} />)

    await waitFor(() => expect(screen.getByLabelText(/subir sin estadillo/i).checked).toBe(false))
  })

  it('un evento error del estadillo rehabilita el paso', async () => {
    let estado = null
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    emitirCloud({ scope: 'estadillo', kind: 'error', error: 'formato inválido' })
    expect(await screen.findByText(/formato inválido/)).toBeTruthy()
    await waitFor(() => expect(estado.subiendo).toBe(false))
  })

  it('autodetecta el estadillo de la carpeta del vuelo y rellena el selector', async () => {
    api.estadillosDetectar.mockResolvedValue({ rutas: ['/vuelo/estadillo.xlsx'], n_estadillos: 1 })
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    let estado = null
    render(
      <PasoEstadillo
        prefijo="ACME--P--2026--T"
        carpeta="/vuelo"
        onEstado={(e) => { estado = e }}
      />)
    // `true` = `incluirRecibidos`: en escritorio (`PasoEstadillo`) la
    // autodetección también mira la carpeta de estadillos recibidos por LAN.
    await waitFor(() => expect(api.estadillosDetectar).toHaveBeenCalledWith('/vuelo', true))
    expect(await screen.findByText(/estadillo detectado en la carpeta del vuelo/i)).toBeTruthy()
    await waitFor(() => expect(estado.rutas).toEqual(['/vuelo/estadillo.xlsx']))
  })

  it('sin estadillo detectado muestra el aviso de «no encontrado» y deja el selector vacío', async () => {
    api.estadillosDetectar.mockResolvedValue({ rutas: [] })
    let estado = null
    render(
      <PasoEstadillo
        prefijo="ACME--P--2026--T"
        carpeta="/vuelo"
        onEstado={(e) => { estado = e }}
      />)
    expect(
      await screen.findByText(/no se ha encontrado ningún estadillo en la carpeta del vuelo/i)
    ).toBeTruthy()
    await waitFor(() => expect(estado.rutas).toEqual([]))
  })

  it('si estadillosDetectar rechaza, no explota y muestra el aviso de «no encontrado»', async () => {
    api.estadillosDetectar.mockRejectedValue(new Error('fallo de red'))
    render(
      <PasoEstadillo prefijo="ACME--P--2026--T" carpeta="/vuelo" onEstado={vi.fn()} />)
    expect(
      await screen.findByText(/no se ha encontrado ningún estadillo en la carpeta del vuelo/i)
    ).toBeTruthy()
  })

  it('si estadillosDetectar se queda colgado (plazo vencido), sale de "buscando" con boton reintentar', async () => {
    vi.useFakeTimers()
    try {
      api.estadillosDetectar.mockReturnValue(new Promise(() => {})) // nunca resuelve
      render(
        <PasoEstadillo prefijo="ACME--P--2026--T" carpeta="/vuelo" onEstado={vi.fn()} />)
      expect(screen.getByText(/buscando el estadillo/i)).toBeTruthy()
      await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
      expect(screen.getByRole('alert')).toBeTruthy()
      const reintentar = screen.getByTestId('autodeteccion-reintentar')
      expect(reintentar).toBeTruthy()

      // Reintentar vuelve a llamar al backend: si esta vez responde, sale
      // del estado de error.
      api.estadillosDetectar.mockResolvedValue({ rutas: [] })
      fireEvent.click(reintentar)
      await act(async () => { await Promise.resolve() })
      expect(api.estadillosDetectar).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('ofrece el botón «Elegir…» para volver a elegir el estadillo a mano', async () => {
    // `permitirElegir` (decisión de Rodrigo, 2026-09-22): SOLO en escritorio,
    // recupera la posibilidad de elegir el fichero a mano cuando la
    // autodetección no acierta.
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={vi.fn()} />)
    expect(await screen.findByRole('button', { name: /elegir/i })).toBeTruthy()
  })

  it('sin prop carpeta no llama a estadillosDetectar', async () => {
    let estado = null
    render(
      <PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    await act(async () => { await Promise.resolve() })
    expect(api.estadillosDetectar).not.toHaveBeenCalled()
  })

  it('«Bajar de la nube» solo aparece cuando ya hay estadillo subido para la inspección', async () => {
    api.estadilloExistente.mockResolvedValue({ existe: false })
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={vi.fn()} />)
    await waitFor(() => expect(api.estadilloExistente).toHaveBeenCalled())
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByRole('button', { name: /bajar de la nube/i })).toBeNull()
  })

  it('«Bajar de la nube» descarga y añade la ruta a la selección', async () => {
    api.estadilloExistente.mockResolvedValue({ existe: true })
    api.estadilloBajarNube.mockResolvedValue({
      ok: true, error: null, rutas: [{ ruta: '/home/op/estadillos_recibidos/nube/01__abc.csv', nombre: '01__abc.csv' }],
    })
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 2 })
    let estado = null
    render(
      <PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)

    const boton = await screen.findByRole('button', { name: /bajar de la nube/i })
    fireEvent.click(boton)

    await waitFor(() => expect(api.estadilloBajarNube).toHaveBeenCalledWith('ACME--P--2026--T'))
    await waitFor(() => expect(estado.rutas).toEqual(['/home/op/estadillos_recibidos/nube/01__abc.csv']))
  })

  it('«Bajar de la nube» muestra el error en una línea si falla', async () => {
    api.estadilloExistente.mockResolvedValue({ existe: true })
    api.estadilloBajarNube.mockResolvedValue({ ok: false, error: 'sin red', rutas: [] })
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={vi.fn()} />)

    const boton = await screen.findByRole('button', { name: /bajar de la nube/i })
    fireEvent.click(boton)

    expect(await screen.findByText('sin red')).toBeTruthy()
  })
})
