import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

describe('bridge en modo servidor (sin pywebview)', () => {
  beforeEach(() => {
    vi.resetModules()
    delete window.pywebview
    // EventSource no existe en jsdom: se simula.
    class FakeEventSource {
      constructor(url) {
        this.url = url
        this.listeners = {}
        FakeEventSource.ultima = this
      }
      addEventListener(tipo, fn) { this.listeners[tipo] = fn }
      close() { this.cerrada = true }
      emitir(tipo, detail) { this.listeners[tipo]?.({ data: JSON.stringify(detail) }) }
    }
    window.EventSource = FakeEventSource
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: { ok: true, msg: 'pong' } }),
    }))
  })

  afterEach(() => { vi.restoreAllMocks() })

  it('isServerMode es true si no hay pywebview', async () => {
    const { isServerMode } = await import('./bridge.js')
    expect(isServerMode()).toBe(true)
  })

  it('una llamada de la api va por POST /api/<metodo> con los argumentos', async () => {
    const { api } = await import('./bridge.js')
    const res = await api.ping('rebeca')
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/ping',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body)
    expect(body).toEqual({ args: ['rebeca'] })
    expect(res.msg).toBe('pong')
  })

  it('un error del backend se propaga como excepcion', async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      json: async () => ({ error: 'ValueError: ruta invalida' }),
    }))
    const { api } = await import('./bridge.js')
    await expect(api.pickFolder()).rejects.toThrow(/ruta invalida/)
  })

  it('los eventos SSE se reemiten como CustomEvent y onCloud los recibe', async () => {
    const { onCloud, whenBridgeReady } = await import('./bridge.js')
    const visto = []
    onCloud((d) => visto.push(d))
    // El SSE no se abre hasta que `whenBridgeReady` confirma modo servidor;
    // no hay conexion especulativa al importar (rompia Windows, ver el test
    // de abajo).
    await whenBridgeReady()
    window.EventSource.ultima.emitir('atom:cloud', { kind: 'done', uploaded: 3 })
    expect(visto).toEqual([{ kind: 'done', uploaded: 3 }])
  })

  it('con un picker registrado, pickFolder lo usa y no llama a fetch', async () => {
    const { api, registerPicker } = await import('./bridge.js')
    const fn = vi.fn(async (mode) => `/elegido/${mode}`)
    registerPicker(fn)
    const res = await api.pickFolder()
    expect(fn).toHaveBeenCalledWith('folder')
    expect(res).toBe('/elegido/folder')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('sin picker registrado, pickFolder va por fetch/POST', async () => {
    const { api } = await import('./bridge.js')
    await api.pickFolder()
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/pick_folder',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('no abre SSE si pywebview aparece despues (arranque asincrono de Qt)', async () => {
    const { whenBridgeReady, isServerMode } = await import('./bridge.js')
    // Este es el arranque REAL de Windows: al evaluar el modulo todavia no hay
    // `window.pywebview` porque Qt lo inyecta despues. Si el bridge abriera el
    // SSE aqui, en Windows apuntaria a `file:///events` (la pagina se carga por
    // file://) y EventSource se reconectaria en bucle para siempre.
    expect(window.EventSource.ultima).toBeUndefined()
    window.pywebview = { api: { ping: vi.fn() } }
    await whenBridgeReady()
    expect(isServerMode()).toBe(false)
    expect(window.EventSource.ultima).toBeUndefined()
  })
})

describe('bridge en modo pywebview (Windows)', () => {
  beforeEach(() => {
    vi.resetModules()
    window.pywebview = { api: { ping: vi.fn(async () => ({ ok: true, msg: 'pong qt' })) } }
    globalThis.fetch = vi.fn()
  })

  it('isServerMode es false y NO se usa fetch', async () => {
    const { api, isServerMode } = await import('./bridge.js')
    expect(isServerMode()).toBe(false)
    const res = await api.ping('rodrigo')
    expect(res.msg).toBe('pong qt')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})
