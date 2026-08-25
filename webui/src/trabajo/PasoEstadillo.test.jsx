import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'

vi.mock('../bridge', () => ({
  api: {
    estadilloValidar: vi.fn(),
    estadilloSubir: vi.fn(),
    estadilloExistente: vi.fn(),
    pickFile: vi.fn(),
  },
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

// Mismo camino que `estadilloSubida.test.jsx` usa a nivel de `BucketScreen`:
// el campo Estadillo elige el fichero pulsando su botón «Elegir…», que llama
// a `api.pickFile()`. Aquí no hay más campos «Elegir…» en pantalla (a
// diferencia de `BucketScreen`, que también tiene el de «Carpeta a subir»),
// así que basta con el primero que aparezca.
async function elegirEstadillo(ruta = '/home/saez/Descargas/estadillo.xlsx') {
  api.pickFile.mockResolvedValueOnce(ruta)
  fireEvent.click(await screen.findByRole('button', { name: /elegir/i }))
  await waitFor(() => expect(api.pickFile).toHaveBeenCalled())
}

beforeEach(() => {
  vi.clearAllMocks()
  api.estadilloExistente.mockResolvedValue({ existe: false })
})

describe('PasoEstadillo', () => {
  it('valida el estadillo al elegirlo y reporta listo', async () => {
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 3 })
    const onEstado = vi.fn()
    const { rerender } = render(
      <PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
    // el componente expone su onChange vía EstadilloField; simula la elección
    // usando el mismo camino que estadilloSubida.test.jsx usa para BucketScreen.
    await elegirEstadillo()
    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.listo).toBe(true)
    })
    await waitFor(() => expect(onEstado).toHaveBeenCalled())
    rerender(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
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
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    api.estadilloSubir.mockResolvedValue({ started: false, reason: 'ya hay una subida' })
    let estado = null
    render(<PasoEstadillo prefijo="ACME--P--2026--T" onEstado={(e) => { estado = e }} />)
    await waitFor(() => expect(estado).toBeTruthy())
    // este caso requiere ficheros elegidos; usa el mismo helper que
    // estadilloSubida.test.jsx para poblarlos antes de llamar a subir().
    await elegirEstadillo()
    await waitFor(() => expect(estado.rutas.length).toBe(1))

    await expect(estado.subir()).rejects.toThrow('ya hay una subida')
  })

  it('vacía la selección de estadillo al cambiar de inspección', async () => {
    api.estadilloValidar.mockResolvedValue({ ok: true, vuelos_detectados: 1 })
    const onEstado = vi.fn()
    const { rerender } = render(
      <PasoEstadillo prefijo="ACME--P--2026--T" onEstado={onEstado} />)
    await elegirEstadillo()
    await waitFor(() => {
      const ultimo = onEstado.mock.calls.at(-1)[0]
      expect(ultimo.rutas.length).toBe(1)
    })

    onEstado.mockClear()
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
})
