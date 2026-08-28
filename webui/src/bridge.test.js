import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

describe('bridge en modo servidor (marca del kiosco)', () => {
  beforeEach(() => {
    vi.resetModules()
    delete window.pywebview
    // El servidor de la Pi inyecta esta marca en el `index.html` que sirve
    // (`atom_core/webserver.py`). Es la UNICA senal del modo servidor: la
    // ausencia de `window.pywebview` ya no vale, porque en Windows Qt la
    // inyecta tarde y la pagina tambien se carga por http://127.0.0.1.
    window.__ATOM_SERVIDOR__ = true
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

  afterEach(() => {
    vi.restoreAllMocks()
    delete window.__ATOM_SERVIDOR__
  })

  it('isServerMode es true con la marca del servidor', async () => {
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

  // Regresion: `whenBridgeReady` creaba una promesa NUEVA en cada llamada, y
  // con ella su propio setTimeout(ESPERA_PYWEBVIEW_MS). El unico cortocircuito
  // de entrada miraba `window.pywebview`, que en modo servidor no aparece
  // jamas, asi que TODA llamada pagaba el plazo integro antes del fetch: abrir
  // el selector encadenaba `pickFolder` + `defaultDir` = ~3 s de espera con el
  // backend contestando en 4 ms.
  it('el modo se determina una sola vez: las llamadas siguientes no repiten el plazo', async () => {
    const { api, whenBridgeReady } = await import('./bridge.js')
    await whenBridgeReady()  // aqui si se paga el plazo, una unica vez
    // Con el reloj congelado, cualquier espera por temporizador se quedaria
    // colgada para siempre: si esto resuelve, es que no la hay.
    vi.useFakeTimers()
    try {
      await expect(api.ping('rebeca')).resolves.toBeTruthy()
      await expect(api.listDir('/media/usb')).resolves.toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
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

// Regresion de Windows (v3.4.54): desde pywebview 6 el shell de escritorio
// arranca su propio servidor HTTP en cuanto la URL es local, asi que la pagina
// NO se carga por `file://` sino por `http://127.0.0.1:PORT` — y la inyeccion
// de Qt puede tardar MAS que `ESPERA_PYWEBVIEW_MS` (arranque en frio del
// onefile de PyInstaller, antivirus, disco lento). Con la regla vieja
// (`no file:// y sin pywebview` ⇒ servidor) Windows se veia como el kiosco de
// la Pi ya en el primer render, y `App.jsx` lo montaba: una pantalla tactil
// sin salida. Sin la marca del servidor, esto es escritorio y punto.
describe('bridge en el shell de escritorio con pywebview lento (Windows)', () => {
  let locOriginal

  beforeEach(() => {
    vi.resetModules()
    vi.useFakeTimers()
    delete window.pywebview
    locOriginal = Object.getOwnPropertyDescriptor(window, 'location')
    Object.defineProperty(window, 'location', {
      value: {
        protocol: 'http:',
        hostname: '127.0.0.1',
        href: 'http://127.0.0.1:41337/index.html',
        search: '',
      },
      configurable: true,
      writable: true,
    })
    class FakeEventSource {
      constructor(url) { this.url = url; this.listeners = {}; FakeEventSource.ultima = this }
      addEventListener(tipo, fn) { this.listeners[tipo] = fn }
      close() { this.cerrada = true }
    }
    window.EventSource = FakeEventSource
    globalThis.fetch = vi.fn()
  })

  afterEach(() => {
    vi.useRealTimers()
    if (locOriginal) Object.defineProperty(window, 'location', locOriginal)
    vi.restoreAllMocks()
  })

  it('sin la marca del servidor isServerMode es false aunque pywebview aun no exista', async () => {
    const { isServerMode } = await import('./bridge.js')
    expect(isServerMode()).toBe(false)
  })

  it('no se rinde al vencer el plazo: espera a Qt y nunca abre SSE ni fetch', async () => {
    const { whenBridgeReady, isServerMode, api } = await import('./bridge.js')
    const listo = whenBridgeReady()
    // El plazo vence de sobra y aun no hay bridge: antes esto fijaba modo
    // servidor de forma irreversible.
    await vi.advanceTimersByTimeAsync(5000)
    expect(isServerMode()).toBe(false)
    expect(window.EventSource.ultima).toBeUndefined()
    // Qt inyecta tarde; el polling debe seguir vivo y resolver.
    const ping = vi.fn(async () => ({ ok: true, msg: 'pong qt' }))
    window.pywebview = { api: { ping, pick_folder: vi.fn(async () => 'C:/plantas') } }
    await vi.advanceTimersByTimeAsync(200)
    await listo
    expect(isServerMode()).toBe(false)
    expect(await api.ping('rebeca')).toEqual({ ok: true, msg: 'pong qt' })
    expect(globalThis.fetch).not.toHaveBeenCalled()
    expect(window.EventSource.ultima).toBeUndefined()
  })

  it('con picker registrado, pickFolder usa el dialogo NATIVO, no el explorador', async () => {
    const { api, registerPicker, whenBridgeReady } = await import('./bridge.js')
    // `App.jsx` registra el picker al montar, sin saber el modo todavia.
    const pickerUI = vi.fn()
    registerPicker(pickerUI)
    const pedido = api.pickFolder()
    const pick_folder = vi.fn(async () => 'C:/plantas')
    window.pywebview = { api: { pick_folder } }
    await vi.advanceTimersByTimeAsync(200)
    await whenBridgeReady()
    expect(await pedido).toBe('C:/plantas')
    expect(pickerUI).not.toHaveBeenCalled()
    expect(pick_folder).toHaveBeenCalled()
  })
})
