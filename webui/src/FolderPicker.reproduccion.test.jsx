import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'

// Modo "reproduccion" (control remoto del kiosco): el picker recorre SOLO
// los segmentos de `rutaObjetivo`, resaltando cada fila con
// `.kiosk-control-pulso` antes de entrar, y termina resaltando el boton
// "Usar esta carpeta" antes de avisar con `onFinReproduccion`. Nunca llama a
// `onPick` (el backend ya fijo la carpeta por control remoto).
const { listDir } = vi.hoisted(() => ({
  listDir: vi.fn(async (ruta) => {
    if (!ruta) return { ok: true, path: '/', parent: null, dirs: [{ name: 'media', path: '/media' }], files: [] }
    if (ruta === '/media') {
      return { ok: true, path: '/media', parent: '/', dirs: [{ name: 'usb', path: '/media/usb' }], files: [] }
    }
    if (ruta === '/media/usb') {
      return { ok: true, path: '/media/usb', parent: '/media', dirs: [], files: [] }
    }
    return { ok: true, path: ruta, parent: null, dirs: [], files: [] }
  }),
}))

vi.mock('./bridge.js', () => ({
  isServerMode: () => true,
  api: { listDir, defaultDir: vi.fn(async () => ({ ok: false, error: 'sin disco' })) },
}))

import FolderPicker from './FolderPicker.jsx'

// Timers falsos desde ANTES de montar: si se instalan a medias (tras un
// `findByText` con timers reales), el primer `setTimeout` del recorrido
// queda capturado por el reloj real y los avances de reloj falso de después
// nunca lo tocan. Para dejar que las promesas (sin timers) asienten se
// encadenan varios `Promise.resolve()`, que corren igual con reloj falso o
// real: es la cola de microtareas, no la de temporizadores.
async function flush(n = 15) {
  for (let i = 0; i < n; i++) {
    // eslint-disable-next-line no-await-in-loop
    await Promise.resolve()
  }
}

describe('FolderPicker en modo reproduccion', () => {
  beforeEach(() => {
    listDir.mockClear()
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('recorre los segmentos en orden, resaltando cada fila, y nunca llama a onPick', async () => {
    const onPick = vi.fn()
    const onFin = vi.fn()
    render(
      <FolderPicker
        mode="folder"
        reproduccion
        rutaObjetivo="/media/usb"
        onPick={onPick}
        onCancel={() => {}}
        onFinReproduccion={onFin}
      />,
    )
    await act(flush)
    expect(document.querySelector('.picker-reproduccion')).toBeTruthy()

    // Pausa inicial (1200ms): listado de la raiz ya cargado, nada resaltado.
    let filaMedia = [...document.querySelectorAll('.picker-dir')]
      .find((el) => el.textContent.includes('media'))
    expect(filaMedia).toBeTruthy()
    expect(filaMedia.classList.contains('kiosk-control-pulso')).toBe(false)

    // Tras la pausa inicial se resalta "media" antes de entrar.
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); await flush() })
    filaMedia = [...document.querySelectorAll('.picker-dir')]
      .find((el) => el.textContent.includes('media'))
    expect(filaMedia.classList.contains('kiosk-control-pulso')).toBe(true)
    expect(listDir).not.toHaveBeenCalledWith('/media')

    // Tras resaltar 700ms entra en /media.
    await act(async () => { await vi.advanceTimersByTimeAsync(700); await flush() })
    expect(listDir).toHaveBeenCalledWith('/media')

    // Espera 1100ms ya dentro de /media (listado nuevo en pantalla) antes de
    // resaltar el siguiente segmento ("usb").
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); await flush() })
    const filaUsb = [...document.querySelectorAll('.picker-dir')]
      .find((el) => el.textContent.includes('usb'))
    expect(filaUsb).toBeTruthy()
    expect(filaUsb.classList.contains('kiosk-control-pulso')).toBe(true)
    expect(listDir).not.toHaveBeenCalledWith('/media/usb')

    // Tras resaltar 700ms entra en /media/usb (ultimo nivel).
    await act(async () => { await vi.advanceTimersByTimeAsync(700); await flush() })
    expect(listDir).toHaveBeenCalledWith('/media/usb')

    // Espera 1100ms ya dentro del ultimo nivel antes de resaltar "Usar esta
    // carpeta".
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); await flush() })
    const botonConfirmar = document.querySelector('.btn-run')
    expect(botonConfirmar.classList.contains('kiosk-control-pulso')).toBe(true)
    expect(onFin).not.toHaveBeenCalled()

    // Tras 1500ms resaltando confirmar, avisa y nunca llamo a onPick.
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); await flush() })
    expect(onFin).toHaveBeenCalledTimes(1)
    expect(onPick).not.toHaveBeenCalled()

    const ordenSegmentos = listDir.mock.calls.map((c) => c[0]).filter((p) => p === '/media' || p === '/media/usb')
    expect(ordenSegmentos).toEqual(['/media', '/media/usb'])
  })

  it('ignora toques reales mientras reproduce (pointer-events desactivados)', async () => {
    render(
      <FolderPicker
        mode="folder"
        reproduccion
        rutaObjetivo="/media/usb"
        onPick={() => {}}
        onCancel={() => {}}
        onFinReproduccion={() => {}}
      />,
    )
    await act(flush)
    const overlay = document.querySelector('.pm-overlay')
    expect(overlay.className).toContain('picker-reproduccion')
  })
})
