import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act } from '@testing-library/react'

// Cubre la reproduccion visual de `accionRemota` (Task «control remoto del
// kiosco: se ve como se mueve a los sitios»): 'organizar'/'cancelar' resaltan
// un boton sin tocar el backend, 'carpeta' abre el `FolderPicker` en modo
// reproduccion. `estadilloEsperaEstado` se deja colgada (no resuelve) para
// que no interfiera con `esperandoEstadillo` en ninguno de estos tests.
vi.mock('./bridge.js', () => ({
  isServerMode: () => false,
  api: {
    pickFile: vi.fn(() => Promise.resolve('')),
    listDir: vi.fn(async (ruta) => ({ ok: true, path: ruta || '/', parent: ruta ? '/' : null, dirs: [], files: [] })),
    defaultDir: vi.fn(async () => ({ ok: false, error: 'sin disco' })),
    estadilloEsperaIniciar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaEstado: vi.fn(() => new Promise(() => {})),
    estadilloEsperaCancelar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaCarpeta: vi.fn(() => Promise.resolve({ ok: true })),
  },
}))
vi.mock('./bridge', () => ({
  isServerMode: () => false,
  api: {
    pickFile: vi.fn(() => Promise.resolve('')),
    listDir: vi.fn(async (ruta) => ({ ok: true, path: ruta || '/', parent: ruta ? '/' : null, dirs: [], files: [] })),
    defaultDir: vi.fn(async () => ({ ok: false, error: 'sin disco' })),
    estadilloEsperaIniciar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaEstado: vi.fn(() => new Promise(() => {})),
    estadilloEsperaCancelar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaCarpeta: vi.fn(() => Promise.resolve({ ok: true })),
  },
}))

import { api } from './bridge'
import KioskScreen from './KioskScreen.jsx'

const inspecciones = [
  { id: 1, prefijo: 'ACME--PLANTA1--2026--PV', etiqueta: 'ACME PLANTA1 2026', anio: 2026, fase: 'Vuelo' },
]

function baseProps(overrides = {}) {
  return {
    status: { email: 'rebeca@aerotools.es', picture: null },
    carpeta: '/home/pi/vuelo/PLANTA',
    onPickCarpeta: vi.fn(),
    inspecciones,
    inspeccion: null,
    onSelectInspeccion: vi.fn(),
    onActualizarInspecciones: vi.fn(),
    estadillo: [],
    onEstadillo: vi.fn(),
    onOrganizar: vi.fn(),
    onSubirCrudo: vi.fn(),
    busy: false,
    progreso: null,
    ...overrides,
  }
}

describe('KioskScreen — reproduccion de acciones remotas (accionRemota)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('"organizar" navega a Organizer, resalta el boton ~600ms y no llama a onOrganizar', async () => {
    vi.useFakeTimers()
    const onOrganizar = vi.fn()
    const onAccionRemotaConsumida = vi.fn()
    try {
      render(
        <KioskScreen
          {...baseProps({ onOrganizar })}
          accionRemota={{ accion: 'organizar', _id: 1 }}
          onAccionRemotaConsumida={onAccionRemotaConsumida}
        />,
      )
      await act(async () => { await Promise.resolve() })

      // Navega a la pantalla Organizer al instante.
      const boton = document.querySelector('[data-control-resaltar="organizar"]')
      expect(boton).toBeTruthy()
      expect(boton.classList.contains('kiosk-control-pulso')).toBe(false)

      // Tras ~450ms se resalta el boton.
      await act(async () => { await vi.advanceTimersByTimeAsync(450) })
      expect(boton.classList.contains('kiosk-control-pulso')).toBe(true)

      // ~600ms de resaltado y se consume la accion.
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(boton.classList.contains('kiosk-control-pulso')).toBe(false)
      expect(onAccionRemotaConsumida).toHaveBeenCalledTimes(1)
      expect(onOrganizar).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('"cancelar" resalta el boton de cancelar visible en pantalla sin llamar a la API', async () => {
    vi.useFakeTimers()
    const onAccionRemotaConsumida = vi.fn()
    // No hay boton de cancelar propio en el paso actual de este test: se
    // simula uno "visible en pantalla" (mismo contrato que usa el efecto,
    // `document.querySelector('[data-control-resaltar="cancelar"]')`).
    const falsoBoton = document.createElement('button')
    falsoBoton.setAttribute('data-control-resaltar', 'cancelar')
    document.body.appendChild(falsoBoton)
    try {
      render(
        <KioskScreen
          {...baseProps()}
          accionRemota={{ accion: 'cancelar', _id: 2 }}
          onAccionRemotaConsumida={onAccionRemotaConsumida}
        />,
      )
      await act(async () => { await Promise.resolve() })
      expect(falsoBoton.classList.contains('kiosk-control-pulso')).toBe(true)

      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(falsoBoton.classList.contains('kiosk-control-pulso')).toBe(false)
      expect(onAccionRemotaConsumida).toHaveBeenCalledTimes(1)
      expect(api.pickFile).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
      document.body.removeChild(falsoBoton)
    }
  })

  it('"carpeta" sin estadillo en la carpeta: al terminar, pulsa "Recibir estadillo" y arranca la espera', async () => {
    vi.useFakeTimers()
    const onAccionRemotaConsumida = vi.fn()
    try {
      render(
        <KioskScreen
          {...baseProps({
            estadilloEnCarpeta: { buscando: false, encontrado: false, nombre: null, recibidoLan: false },
          })}
          accionRemota={{ accion: 'carpeta', path: '/media/pi/USB/VUELO', _id: 4 }}
          onAccionRemotaConsumida={onAccionRemotaConsumida}
        />,
      )
      await act(async () => { await Promise.resolve() })

      // El picker recorre sus segmentos y se cierra solo. NO se usa
      // `runAllTimersAsync` aquí: en cuanto arranca la espera del estadillo
      // (más abajo) queda un `setInterval` de sondeo sin resolver a
      // propósito (`estadilloEsperaEstado` colgada, ver mock del fichero),
      // y "correr todos los timers" con un intervalo que no para nunca
      // aborta la prueba — se avanza un tiempo ACOTADO en su lugar (de
      // sobra para que el picker, que sí es finito, termine solo).
      await act(async () => { await vi.advanceTimersByTimeAsync(1250) })
      expect(document.querySelector('.picker-reproduccion')).toBeTruthy()
      // El recorrido del picker (4 segmentos de "/media/pi/USB/VUELO") tarda
      // 9900ms en total (`PAUSA_INICIAL_MS` + por segmento + `RESALTAR_
      // CONFIRMAR_MS`, ver `FolderPicker.jsx`): se avanza justo lo bastante
      // para que termine, sin llegar todavía a los 1200ms del pulso.
      await act(async () => { await vi.advanceTimersByTimeAsync(9950) })
      expect(document.querySelector('.picker-reproduccion')).toBeNull()

      // Nada mas cerrarse, el boton "Recibir estadillo" queda pulsado.
      const boton = document.querySelector('.kiosk-card-estadillo')
      expect(boton).toBeTruthy()
      expect(boton.classList.contains('kiosk-control-pulso')).toBe(true)
      expect(api.estadilloEsperaIniciar).not.toHaveBeenCalled()

      // A los 1200ms del pulso, arranca la espera (mismo handler que un
      // toque real): se ve la pantalla de "Esperando estadillo", y ya no
      // hace falta otro toque.
      await act(async () => { await vi.advanceTimersByTimeAsync(1200) })
      expect(api.estadilloEsperaIniciar).toHaveBeenCalled()
      expect(document.querySelector('[data-testid="espera-estadillo"]')).toBeTruthy()
      expect(boton.classList.contains('kiosk-control-pulso')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('"carpeta" CON estadillo ya encontrado: al terminar, no pulsa ni arranca la espera', async () => {
    vi.useFakeTimers()
    const onAccionRemotaConsumida = vi.fn()
    try {
      render(
        <KioskScreen
          {...baseProps({
            estadilloEnCarpeta: { buscando: false, encontrado: true, nombre: 'estadillo.csv', recibidoLan: false },
          })}
          accionRemota={{ accion: 'carpeta', path: '/media/pi/USB/VUELO', _id: 5 }}
          onAccionRemotaConsumida={onAccionRemotaConsumida}
        />,
      )
      await act(async () => { await Promise.resolve() })
      await act(async () => { await vi.advanceTimersByTimeAsync(1250) })
      expect(document.querySelector('.picker-reproduccion')).toBeTruthy()
      await act(async () => { await vi.runAllTimersAsync() })

      expect(document.querySelector('.picker-reproduccion')).toBeNull()
      expect(api.estadilloEsperaIniciar).not.toHaveBeenCalled()
      expect(document.querySelector('[data-testid="espera-estadillo"]')).toBeNull()
      const boton = document.querySelector('.kiosk-card-estadillo')
      expect(boton?.classList.contains('kiosk-control-pulso')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('"carpeta" abre el FolderPicker en modo reproduccion y lo cierra solo, sin fijar carpeta a mano', async () => {
    vi.useFakeTimers()
    const onPickCarpeta = vi.fn()
    const onAccionRemotaConsumida = vi.fn()
    try {
      render(
        <KioskScreen
          {...baseProps({ onPickCarpeta })}
          accionRemota={{ accion: 'carpeta', path: '/media/pi/USB/VUELO', _id: 3 }}
          onAccionRemotaConsumida={onAccionRemotaConsumida}
        />,
      )
      await act(async () => { await Promise.resolve() })

      // A los 1250ms (450ms de navegacion + 800ms en la pantalla Organizer)
      // se abre el picker en modo reproduccion.
      await act(async () => { await vi.advanceTimersByTimeAsync(1250) })
      expect(document.querySelector('.picker-reproduccion')).toBeTruthy()

      // Deja que recorra sola todos los segmentos y se cierre.
      await act(async () => { await vi.runAllTimersAsync() })

      expect(document.querySelector('.picker-reproduccion')).toBeNull()
      expect(onAccionRemotaConsumida).toHaveBeenCalledTimes(1)
      // El picker en reproduccion nunca elige carpeta a mano: el backend ya
      // la fijo por `/api/control/carpeta` antes de mandar este evento.
      expect(onPickCarpeta).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})
