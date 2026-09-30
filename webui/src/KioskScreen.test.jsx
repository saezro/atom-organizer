import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Esta suite prueba la estructura/flujo del componente con click normal
// (modo escritorio). El comportamiento de pulsacion larga en modo servidor
// se cubre aparte en KioskScreen.pulsacion.test.jsx.
// `estadilloEsperaIniciar`/`Estado`/`Cancelar` van como `vi.fn()` (no
// resueltos por defecto) para poder controlar su resolución por test al
// probar el wiring de «Recibir estadillo» (`EsperaEstadillo`).
vi.mock('./bridge.js', () => ({
  isServerMode: () => false,
  api: {
    cloudLogout: () => Promise.resolve({}),
    // PairScreen se monta dentro de la pantalla de cuenta cuando no hay
    // sesión; aquí solo interesa que llegue a pintarse, no su flujo de QR
    // (cubierto en PairScreen.test.jsx).
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    // `EstadilloField` ya no tiene selector de fichero local; `pickFile` no
    // se usa en esta suite, pero se deja resuelta (nunca colgada) por si
    // algún test futuro la dispara desde otro picker (p.ej. carpeta).
    pickFile: () => Promise.resolve(''),
    estadilloEsperaIniciar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaEstado: vi.fn(() => new Promise(() => {})),
    estadilloEsperaCancelar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaCarpeta: vi.fn(() => Promise.resolve({ ok: true })),
  },
}))
// `EstadilloField.jsx`/`EsperaEstadillo.jsx` importan `./bridge`/`../bridge`
// (sin extensión); mismo módulo que `./bridge.js` para el resolutor de
// vitest, pero hay que mockear ambas rutas para que ambos imports vean el
// mismo mock.
vi.mock('./bridge', () => ({
  isServerMode: () => false,
  api: {
    cloudLogout: () => Promise.resolve({}),
    cloudPairStart: () => new Promise(() => {}),
    cloudPairPoll: () => new Promise(() => {}),
    pickFile: () => Promise.resolve(''),
    estadilloEsperaIniciar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaEstado: vi.fn(() => new Promise(() => {})),
    estadilloEsperaCancelar: vi.fn(() => Promise.resolve({})),
    estadilloEsperaCarpeta: vi.fn(() => Promise.resolve({ ok: true })),
  },
}))

// `EsperaEstadillo`/`EstadilloField` importan sin extensión (`'../bridge'`/
// `'./bridge'`): el `api` que controlan los tests nuevos tiene que ser ESE
// objeto, no el de `'./bridge.js'` que usa `KioskScreen.jsx` directamente.
import { api } from './bridge'
import KioskScreen, { derivarDestino } from './KioskScreen.jsx'

// Shape real de `api.cloudInspecciones()`: lo que consume `InspeccionSelector`
// (prefijo/etiqueta/anio/fase), no el `{id, nombre}` simplificado que usaba
// el `<select>` de antes.
const inspecciones = [
  { id: 1, prefijo: 'ACME--PLANTA1--2026--PV', etiqueta: 'ACME PLANTA1 2026', anio: 2026, fase: 'Vuelo' },
  { id: 2, prefijo: 'BETA--PLANTA2--2025--PV', etiqueta: 'BETA PLANTA2 2025', anio: 2025, fase: 'Confirmada' },
]

function baseProps(overrides = {}) {
  return {
    status: { email: 'rebeca@ejemplo.com', picture: null },
    carpeta: '/home/pi/vuelo/PLANTA',
    onPickCarpeta: vi.fn(),
    inspecciones,
    inspeccion: null,
    onSelectInspeccion: vi.fn(),
    onActualizarInspecciones: vi.fn(),
    // El estadillo viaja como array por todo el kiosco (mismo contrato que
    // en `BucketScreen`), no como string suelto.
    estadillo: [],
    onEstadillo: vi.fn(),
    onOrganizar: vi.fn(),
    onSubirCrudo: vi.fn(),
    busy: false,
    progreso: null,
    ...overrides,
  }
}

describe('derivarDestino', () => {
  it('añade sufijo _ORGANIZADO', () => {
    expect(derivarDestino('/home/pi/vuelo/PLANTA')).toBe('/home/pi/vuelo/PLANTA_ORGANIZADO')
  })

  it('quita la barra final antes de derivar', () => {
    expect(derivarDestino('/home/pi/vuelo/PLANTA/')).toBe('/home/pi/vuelo/PLANTA_ORGANIZADO')
  })

  it('cadena vacía devuelve vacío', () => {
    expect(derivarDestino('')).toBe('')
  })

  it('null devuelve vacío', () => {
    expect(derivarDestino(null)).toBe('')
  })

  it('undefined devuelve vacío', () => {
    expect(derivarDestino(undefined)).toBe('')
  })

  it('soporta rutas Windows con backslash', () => {
    expect(derivarDestino('C:\\vuelo\\PLANTA')).toBe('C:\\vuelo\\PLANTA_ORGANIZADO')
  })

  it('soporta rutas Windows con backslash final', () => {
    expect(derivarDestino('C:\\vuelo\\PLANTA\\')).toBe('C:\\vuelo\\PLANTA_ORGANIZADO')
  })
})

describe('KioskScreen — paso 1 (menú)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('muestra el launcher con las apps del registry, sin campos del paso 2', async () => {
    render(<KioskScreen {...baseProps()} />)
    expect(screen.getByTestId('kiosk-app-organizer')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-app-tareas')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-app-ajustes')).toBeInTheDocument()
    // Son 4 apps: caben en una sola página, así que la columna de paginación
    // está (siempre lo está, para que la rejilla no cambie de ancho) pero
    // deshabilitada.
    expect(screen.getByTestId('kiosk-app-sistema')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-apps-abajo')).toBeDisabled()
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByText(/estadillo/i)).not.toBeInTheDocument()
  })

  it('pulsar "Organizer" lleva al submenú con "Organizar" y "Subir en crudo"', async () => {
    render(<KioskScreen {...baseProps()} />)
    await userEvent.click(screen.getByTestId('kiosk-app-organizer'))
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeInTheDocument()
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
  })

  it('muestra "Sin sesión" cuando status es null', () => {
    render(<KioskScreen {...baseProps({ status: null })} />)
    expect(screen.getByText(/sin sesión/i)).toBeInTheDocument()
  })

  it('renderiza avatar con picture si existe', () => {
    render(
      <KioskScreen
        {...baseProps({ status: { logged_in: true, email: 'rebeca@ejemplo.com', picture: 'http://x/foto.png' } })}
      />
    )
    const img = screen.getByRole('img', { name: /rebeca@ejemplo.com/i })
    expect(img).toHaveAttribute('src', 'http://x/foto.png')
  })

  it('sin picture pero con email muestra avatar de respaldo con la inicial, sin <img>', () => {
    render(<KioskScreen {...baseProps({ status: { logged_in: true, email: 'rebeca@ejemplo.com', picture: null } })} />)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('R')).toBeInTheDocument()
  })

  it('con estado sin-credencial NO se renderiza el avatar aunque logged_in sea true (sesión inválida)', () => {
    render(
      <KioskScreen
        {...baseProps({
          status: {
            logged_in: true,
            estado: 'sin-credencial',
            email: 'rebeca@ejemplo.com',
            picture: 'http://x/foto.png',
          },
        })}
      />
    )
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText('R')).not.toBeInTheDocument()
    expect(screen.getByText(/sin sesión/i)).toBeInTheDocument()
  })

  it('con logged_in false NO se renderiza el avatar aunque el estado sea ok', () => {
    render(
      <KioskScreen
        {...baseProps({
          status: {
            logged_in: false,
            estado: 'ok',
            email: 'rebeca@ejemplo.com',
            picture: 'http://x/foto.png',
          },
        })}
      />
    )
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText(/sin sesión/i)).toBeInTheDocument()
  })

  it('con estado sin-conexion SÍ se sigue renderizando el avatar (sesión válida sin red, no debe parpadear)', () => {
    render(
      <KioskScreen
        {...baseProps({
          status: {
            logged_in: true,
            estado: 'sin-conexion',
            email: 'rebeca@ejemplo.com',
            picture: 'http://x/foto.png',
          },
        })}
      />
    )
    const img = screen.getByRole('img', { name: /rebeca@ejemplo.com/i })
    expect(img).toHaveAttribute('src', 'http://x/foto.png')
  })

  // Regresión: BannerConexion se monta en el launcher junto al avatar, y la
  // coherencia entre ambos es el punto clave (ver comentario de
  // BannerConexion.jsx) — sin red el banner avisa, pero el avatar sigue
  // mostrando la sesión como válida, no "Sin sesión".
  it('con estado sin-conexion muestra el banner y el avatar sigue con sesión (no "Sin sesión")', () => {
    render(
      <KioskScreen
        {...baseProps({
          status: {
            logged_in: true,
            estado: 'sin-conexion',
            email: 'rebeca@ejemplo.com',
            picture: 'http://x/foto.png',
          },
        })}
      />
    )
    expect(screen.getByTestId('kiosk-banner-conexion')).toBeInTheDocument()
    const avatar = screen.getByTestId('kiosk-avatar')
    expect(avatar.querySelector('img')).toHaveAttribute('src', 'http://x/foto.png')
    expect(screen.queryByText('Sin sesión')).not.toBeInTheDocument()
  })

  it('con estado ok no hay banner de conexión', () => {
    render(
      <KioskScreen
        {...baseProps({
          status: { logged_in: true, estado: 'ok', email: 'rebeca@ejemplo.com', picture: null },
        })}
      />
    )
    expect(screen.queryByTestId('kiosk-banner-conexion')).not.toBeInTheDocument()
  })

  // Regresión: el banner se extendió del launcher a las pantallas del flujo
  // organizar/subir (menú "Organizer", "Fases", elegir inspección, "Antes de
  // subir" y el paso 2 final). Aquí solo se cubre el menú "Organizer": el
  // resto de pantallas del flujo se prueban en sus propios `describe`.
  it('con sin-conexion el banner también se ve en el menú "Organizer"', async () => {
    render(
      <KioskScreen
        {...baseProps({
          status: { logged_in: true, estado: 'sin-conexion', email: 'rebeca@ejemplo.com', picture: null },
        })}
      />
    )
    await userEvent.click(screen.getByTestId('kiosk-app-organizer'))
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-banner-conexion')).toBeInTheDocument()
  })

  it('con estado ok el menú "Organizer" no muestra el banner', async () => {
    render(
      <KioskScreen
        {...baseProps({
          status: { logged_in: true, estado: 'ok', email: 'rebeca@ejemplo.com', picture: null },
        })}
      />
    )
    await userEvent.click(screen.getByTestId('kiosk-app-organizer'))
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.queryByTestId('kiosk-banner-conexion')).not.toBeInTheDocument()
  })

  it('el avatar abre la pantalla de cuenta, no la UI completa de escritorio', async () => {
    render(<KioskScreen {...baseProps({ status: { logged_in: true, email: 'rebeca@ejemplo.com' } })} />)
    await userEvent.click(screen.getByTestId('kiosk-avatar'))
    expect(screen.getByText('Cuenta')).toBeInTheDocument()
    expect(screen.getByText('rebeca@ejemplo.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cerrar sesión/i })).toBeInTheDocument()
    // Sigue sin haber puerta a la UI de escritorio: solo se vuelve al kiosco.
    await userEvent.click(screen.getByRole('button', { name: /atrás/i }))
    expect(screen.getByTestId('kiosk-app-organizer')).toBeInTheDocument()
  })

  it('sin sesión, el avatar lleva al emparejamiento por QR', async () => {
    render(<KioskScreen {...baseProps({ status: { logged_in: false } })} />)
    await userEvent.click(screen.getByTestId('kiosk-avatar'))
    expect(screen.getByText('Cuenta')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /cerrar sesión/i })).not.toBeInTheDocument()
  })

  it('con busy=true, los dos botones del submenú de Organizer están deshabilitados', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer', busy: true })} />)
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeDisabled()
  })

  it('sin progreso no muestra barra', () => {
    render(<KioskScreen {...baseProps({ progreso: null })} />)
    expect(screen.queryByTestId('kiosk-progreso')).not.toBeInTheDocument()
  })

  it('con progreso muestra fase y pct', () => {
    render(<KioskScreen {...baseProps({ progreso: { fase: 'Copiando', pct: 42 } })} />)
    expect(screen.getByText(/copiando/i)).toBeInTheDocument()
    expect(screen.getByText(/42/)).toBeInTheDocument()
  })

  it('pulsar "Organizar" lleva al paso 2 de organizar (sin selector de inspección)', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(screen.getByText(/elegir carpeta/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /recibir estadillo/i })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /atrás/i })).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('pulsar "Subir en crudo" lleva al sub-paso A: solo la lista de inspecciones, nada de carpeta', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizer' })} />)
    await userEvent.click(screen.getByRole('button', { name: /subir en crudo/i }))
    // Sub-paso A: se elige inspección antes que carpeta; sin bloque de
    // carpeta, sin buscador de texto (no cabe con la lista en 480x320).
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/escribe para buscar/i)).not.toBeInTheDocument()
    expect(screen.getByText('ACME PLANTA1 2026')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /atrás/i })).toBeInTheDocument()
    expect(screen.queryByText(/estadillo/i)).not.toBeInTheDocument()
  })
})

describe('KioskScreen — paso 2 (organizar)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('muestra el destino derivado cuando hay carpeta', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/home/pi/vuelo/PLANTA' })} />)
    expect(screen.getByText('/home/pi/vuelo/PLANTA_ORGANIZADO')).toBeInTheDocument()
  })

  it('sin carpeta no muestra destino y el botón "Organizar" no se renderiza', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '' })} />)
    expect(screen.queryByText(/_ORGANIZADO/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^organizar$/i })).not.toBeInTheDocument()
  })

  // Indicador «¿hay estadillo en esta carpeta?» junto al selector (App.jsx
  // manda `estadilloEnCarpeta`, calculado con `api.estadillosDetectar`).
  describe('indicador de estadillo en la carpeta', () => {
    it('mientras busca, dice "Buscando estadillo…"', () => {
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: { buscando: true, encontrado: false, nombre: null, recibidoLan: false },
          })}
        />
      )
      expect(screen.getByTestId('kiosk-estadillo-en-carpeta')).toHaveTextContent('Buscando estadillo…')
    })

    it('con estadillo encontrado, muestra su nombre', () => {
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: {
              buscando: false, encontrado: true, nombre: '20260920_estadillo_Rebeca.csv', recibidoLan: false,
            },
          })}
        />
      )
      const indicador = screen.getByTestId('kiosk-estadillo-en-carpeta')
      expect(indicador).toHaveTextContent('Estadillo encontrado: 20260920_estadillo_Rebeca.csv')
      expect(indicador.className).toMatch(/hint-ok/)
    })

    it('sin estadillo en la carpeta, avisa en ámbar', () => {
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: { buscando: false, encontrado: false, nombre: null, recibidoLan: false },
          })}
        />
      )
      const indicador = screen.getByTestId('kiosk-estadillo-en-carpeta')
      expect(indicador).toHaveTextContent('Sin estadillo en la carpeta')
      expect(indicador.className).toMatch(/hint-warn/)
    })

    it('con un estadillo recibido por LAN pendiente, lo avisa aparte', () => {
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: { buscando: false, encontrado: false, nombre: null, recibidoLan: true },
          })}
        />
      )
      expect(screen.getByTestId('kiosk-estadillo-en-carpeta')).toHaveTextContent(
        'Estadillo recibido por red: se añadirá al organizar'
      )
    })

    it('sin carpeta no se pinta el indicador', () => {
      render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '' })} />)
      expect(screen.queryByTestId('kiosk-estadillo-en-carpeta')).not.toBeInTheDocument()
    })
  })

  // Candidatos de la carpeta PADRE (`estadilloEnCarpeta.candidatosPadre`,
  // App.jsx `kioskUsarCandidatoPadre`): mismo flujo de confirmación que
  // `PasoEstadillo.jsx` en escritorio, nunca se usan sin pulsar.
  describe('candidatos de estadillo en la carpeta padre', () => {
    it('muestra la ruta completa y el botón "Usar este estadillo"', () => {
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: {
              buscando: false, encontrado: false, nombre: null, recibidoLan: false,
              candidatosPadre: ['/home/pi/vuelo/estadillo_padre.xlsx'],
            },
          })}
        />
      )
      expect(screen.getByTestId('kiosk-candidato-padre')).toHaveTextContent(
        'Estadillo encontrado FUERA de la carpeta seleccionada: /home/pi/vuelo/estadillo_padre.xlsx'
      )
      expect(screen.getByRole('button', { name: /usar este estadillo/i })).toBeInTheDocument()
    })

    it('al pulsar, llama a onUsarCandidatoPadre con la ruta', async () => {
      const onUsarCandidatoPadre = vi.fn()
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: {
              buscando: false, encontrado: false, nombre: null, recibidoLan: false,
              candidatosPadre: ['/home/pi/vuelo/estadillo_padre.xlsx'],
            },
            onUsarCandidatoPadre,
          })}
        />
      )
      await userEvent.click(screen.getByRole('button', { name: /usar este estadillo/i }))
      expect(onUsarCandidatoPadre).toHaveBeenCalledTimes(1)
      expect(onUsarCandidatoPadre).toHaveBeenCalledWith('/home/pi/vuelo/estadillo_padre.xlsx')
    })

    // Al usarlo, `App.jsx` (`kioskUsarCandidatoPadre`) quita la ruta de
    // `candidatosPadre` (mismo patrón que `PasoEstadillo` filtrando
    // `candidatosPadre` local): el aviso desaparece del todo y ya no queda
    // botón sobre el que hacer un segundo click, así que no puede duplicarse.
    it('tras usarlo, el aviso desaparece (candidatosPadre ya sin esa ruta)', () => {
      const { rerender } = render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: {
              buscando: false, encontrado: false, nombre: null, recibidoLan: false,
              candidatosPadre: ['/home/pi/vuelo/estadillo_padre.xlsx'],
            },
          })}
        />
      )
      expect(screen.getByTestId('kiosk-candidato-padre')).toBeInTheDocument()
      rerender(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: {
              buscando: false, encontrado: false, nombre: null, recibidoLan: false,
              candidatosPadre: [],
            },
          })}
        />
      )
      expect(screen.queryByTestId('kiosk-candidato-padre')).not.toBeInTheDocument()
    })

    it('sin candidatosPadre no se pinta ningún aviso', () => {
      render(
        <KioskScreen
          {...baseProps({
            accionInicial: 'organizar',
            carpeta: '/home/pi/vuelo/PLANTA',
            estadilloEnCarpeta: { buscando: false, encontrado: true, nombre: 'x.csv', recibidoLan: false, candidatosPadre: [] },
          })}
        />
      )
      expect(screen.queryByTestId('kiosk-candidato-padre')).not.toBeInTheDocument()
    })
  })

  // El botón «Elegir…» (`EstadilloField`, `permitirElegir`) es SOLO de
  // escritorio (`PasoEstadillo.jsx`): el kiosco nunca pasa esa prop, así que
  // no puede aparecer aquí, ni sin estadillo (donde antes se pintaba) ni con
  // uno ya en la lista.
  it('nunca ofrece el botón «Elegir…» (solo escritorio, `PasoEstadillo`)', () => {
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'organizar',
          carpeta: '/home/pi/vuelo/PLANTA',
          estadillo: ['/home/pi/estadillos_recibidos/estadillo.csv'],
        })}
      />
    )
    // Regex acotada a "Elegir…" (con puntos suspensivos, el texto exacto del
    // botón de `EstadilloField`): "Elegir carpeta" es un botón distinto y sí
    // debe seguir presente en el kiosco.
    expect(screen.queryByRole('button', { name: /^elegir…$/i })).toBeNull()
  })

  it('botón "Organizar" llama a onOrganizar con origen/destino/estadillo (array)', async () => {
    const onOrganizar = vi.fn()
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'organizar',
          carpeta: '/home/pi/vuelo/PLANTA',
          estadillo: ['/home/pi/estadillo.xlsx'],
          onOrganizar,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^organizar$/i }))
    expect(onOrganizar).toHaveBeenCalledTimes(1)
    expect(onOrganizar).toHaveBeenCalledWith({
      origen: '/home/pi/vuelo/PLANTA',
      destino: '/home/pi/vuelo/PLANTA_ORGANIZADO',
      estadillo: ['/home/pi/estadillo.xlsx'],
    })
  })

  it('el campo de estadillo empieza vacío (array vacío) y no deshabilita "Organizar"', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', estadillo: [] })} />)
    expect(screen.queryByTestId('estadillo-actual')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^organizar$/i })).not.toBeDisabled()
  })

  it('con un estadillo detectado/recibido, EstadilloField lo pinta de solo lectura (sin tecleo)', () => {
    render(
      <KioskScreen
        {...baseProps({ accionInicial: 'organizar', estadillo: ['/home/pi/estadillo.xlsx'] })}
      />
    )
    expect(screen.getByTestId('estadillo-actual')).toHaveTextContent('estadillo.xlsx')
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('sin carpeta, "Recibir estadillo" sigue habilitado (la espera puede arrancar sin carpeta)', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '' })} />)
    expect(screen.getByRole('button', { name: /recibir estadillo/i })).toBeEnabled()
  })

  it('pulsar "Recibir estadillo" arranca la espera y sustituye el campo', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/home/pi/vuelo/PLANTA' })} />)
    await userEvent.click(screen.getByRole('button', { name: /recibir estadillo/i }))
    expect(api.estadilloEsperaIniciar).toHaveBeenCalledWith('/home/pi/vuelo/PLANTA', null)
    expect(await screen.findByTestId('espera-estadillo')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /recibir estadillo/i })
    ).not.toBeInTheDocument()
  })

  it('al recibir el estadillo, rellena onEstadillo con la ruta y vuelve al campo normal', async () => {
    // Primera respuesta: la del chequeo al montar (sin espera activa, no
    // debe reabrir nada). Segunda: la PRIMERA respuesta del poll real que
    // arranca el click (el test no espera los 2 s del intervalo).
    // `mockResolvedValueOnce`, no `mockResolvedValue`, para no dejar esta
    // implementación contaminando los tests siguientes.
    api.estadilloEsperaEstado.mockResolvedValueOnce({ esperando: false, caducado: false })
    api.estadilloEsperaEstado.mockResolvedValueOnce({
      recibido: true,
      rutas: ['/tmp/recibido/estadillo.csv'],
      fase: 'recibido_ok',
    })
    const onEstadillo = vi.fn()
    render(
      <KioskScreen
        {...baseProps({ accionInicial: 'organizar', carpeta: '/home/pi/vuelo/PLANTA', onEstadillo })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /recibir estadillo/i }))
    // El botón "OK, seguir" del estado `recibido_ok` salta el retraso automático.
    await userEvent.click(await screen.findByRole('button', { name: /^ok, seguir$/i }))
    // Segundo argumento: el resumen (vacío aquí, el mock no manda `resumen`/
    // `info`) que `EstadilloField` usaría para la tarjeta de recibido.
    expect(onEstadillo).toHaveBeenCalledWith(
      ['/tmp/recibido/estadillo.csv'],
      expect.objectContaining({ origen: 'recibido' })
    )
    expect(
      screen.getByRole('button', { name: /recibir estadillo/i })
    ).toBeInTheDocument()
  })

  it('"Cancelar" en la espera llama a estadilloEsperaCancelar y vuelve al campo normal', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/home/pi/vuelo/PLANTA' })} />)
    await userEvent.click(screen.getByRole('button', { name: /recibir estadillo/i }))
    await screen.findByTestId('espera-estadillo')
    await userEvent.click(screen.getByRole('button', { name: /cancelar/i }))
    expect(api.estadilloEsperaCancelar).toHaveBeenCalled()
    expect(
      screen.getByRole('button', { name: /recibir estadillo/i })
    ).toBeInTheDocument()
  })

  it('salir del paso con la espera activa (Atrás) cancela la espera', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/home/pi/vuelo/PLANTA' })} />)
    await userEvent.click(screen.getByRole('button', { name: /recibir estadillo/i }))
    await screen.findByTestId('espera-estadillo')
    await userEvent.click(screen.getByRole('button', { name: /atrás/i }))
    expect(api.estadilloEsperaCancelar).toHaveBeenCalled()
  })

  // Retomar tras recargar Chromium (o desbloquear el PIN, que desmonta y
  // vuelve a montar este componente entero): si el backend ya tenía una
  // espera activa, se abre EsperaEstadillo directo al montar, sin volver a
  // llamar a `estadillo_espera_iniciar` (eso resetearía la caducidad).
  it('con espera activa en el backend al montar, abre EsperaEstadillo directo (sin reiniciarla)', async () => {
    api.estadilloEsperaEstado.mockResolvedValue({ esperando: true, caducado: false, fase: 'esperando' })
    render(<KioskScreen {...baseProps({ accionInicial: null, carpeta: '/home/pi/vuelo/PLANTA' })} />)
    expect(await screen.findByTestId('espera-estadillo')).toBeInTheDocument()
    expect(api.estadilloEsperaIniciar).not.toHaveBeenCalled()
  })

  it('sin espera activa (o caducada) al montar, no abre EsperaEstadillo', async () => {
    api.estadilloEsperaEstado.mockResolvedValue({ esperando: false, caducado: true })
    render(<KioskScreen {...baseProps({ accionInicial: null, carpeta: '/home/pi/vuelo/PLANTA' })} />)
    await Promise.resolve()
    expect(screen.queryByTestId('espera-estadillo')).not.toBeInTheDocument()
  })

  it('con busy=true, "Elegir carpeta" y "Organizar" están deshabilitados', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar', carpeta: '/x/y', busy: true })} />)
    expect(screen.getByRole('button', { name: /elegir carpeta/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeDisabled()
  })

  it('"Atrás" vuelve al paso 1 y muestra de nuevo el menú', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'organizar' })} />)
    await userEvent.click(screen.getByRole('button', { name: /atrás/i }))
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /subir en crudo/i })).toBeInTheDocument()
    expect(screen.queryByText(/elegir carpeta/i)).not.toBeInTheDocument()
  })

  // Regresión: el paso 2 final ("Organizar") es una de las 5 pantallas a las
  // que se extendió el banner (antes solo vivía en el launcher).
  it('con sin-conexion el banner se ve en el paso 2 final "Organizar"', () => {
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'organizar',
          status: { logged_in: true, estado: 'sin-conexion', email: 'rebeca@ejemplo.com', picture: null },
        })}
      />
    )
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-banner-conexion')).toBeInTheDocument()
  })

  it('con estado ok el paso 2 final "Organizar" no muestra el banner', () => {
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'organizar',
          status: { logged_in: true, estado: 'ok', email: 'rebeca@ejemplo.com', picture: null },
        })}
      />
    )
    expect(screen.getByRole('button', { name: /^organizar$/i })).toBeInTheDocument()
    expect(screen.queryByTestId('kiosk-banner-conexion')).not.toBeInTheDocument()
  })
})

describe('KioskScreen — paso 2 (subir en crudo)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('no muestra el destino derivado', () => {
    render(
      <KioskScreen
        {...baseProps({ accionInicial: 'subir', carpeta: '/home/pi/vuelo/PLANTA', inspeccion: inspecciones[0] })}
      />
    )
    expect(screen.queryByText(/_ORGANIZADO/)).not.toBeInTheDocument()
  })

  it('sin carpeta, con inspección ya elegida (sub-paso B), el botón "Subir" está deshabilitado', () => {
    render(
      <KioskScreen {...baseProps({ accionInicial: 'subir', carpeta: '', inspeccion: inspecciones[0] })} />
    )
    expect(screen.getByRole('button', { name: /^subir$/i })).toBeDisabled()
  })

  it('sin inspección elegida (sub-paso A), no se pinta el botón "Subir" ni el de carpeta', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'subir', carpeta: '', inspeccion: null })} />)
    expect(screen.queryByRole('button', { name: /^subir$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /elegir carpeta/i })).not.toBeInTheDocument()
  })

  it('botón "Subir" llama a onSubirCrudo con carpeta e inspección', async () => {
    const onSubirCrudo = vi.fn()
    const inspeccion = { id: 1, nombre: 'ACME 2026' }
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion,
          onSubirCrudo,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^subir$/i }))
    expect(onSubirCrudo).toHaveBeenCalledTimes(1)
    expect(onSubirCrudo).toHaveBeenCalledWith({ carpeta: '/home/pi/vuelo/PLANTA', inspeccion })
  })

  it('con busy=true, "Elegir carpeta" y "Subir" están deshabilitados', () => {
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/x/y',
          inspeccion: { id: 1, nombre: 'X' },
          busy: true,
        })}
      />
    )
    expect(screen.getByRole('button', { name: /elegir carpeta/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^subir$/i })).toBeDisabled()
  })

  it('sin inspección elegida muestra SOLO la lista de InspeccionSelector (sin buscador ni chips)', () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'subir', inspeccion: null })} />)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/escribe para buscar/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /mostrar terminadas/i })).not.toBeInTheDocument()
    // Por defecto solo Vuelo y Preparación: BETA está en Confirmada y no sale.
    expect(screen.getByText('ACME PLANTA1 2026')).toBeInTheDocument()
    expect(screen.queryByText('BETA PLANTA2 2025')).not.toBeInTheDocument()
    // Recargar el catálogo sigue accesible desde la cabecera del sub-paso.
    expect(screen.getByRole('button', { name: /actualizar lista de inspecciones/i })).toBeInTheDocument()
  })

  it('la pantalla de fases deja añadir otra fase y esa inspección aparece', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'subir', inspeccion: null })} />)
    await userEvent.click(screen.getByRole('button', { name: /filtrar por fase/i }))
    await userEvent.click(screen.getByRole('button', { name: /confirmada/i }))
    await userEvent.click(screen.getByRole('button', { name: /listo/i }))
    expect(screen.getByText('BETA PLANTA2 2025')).toBeInTheDocument()
    expect(screen.getByText('ACME PLANTA1 2026')).toBeInTheDocument()
  })

  it('«Todas las fases» quita el filtro y salen todas', async () => {
    render(<KioskScreen {...baseProps({ accionInicial: 'subir', inspeccion: null })} />)
    await userEvent.click(screen.getByRole('button', { name: /filtrar por fase/i }))
    await userEvent.click(screen.getByRole('button', { name: /todas las fases/i }))
    await userEvent.click(screen.getByRole('button', { name: /listo/i }))
    expect(screen.getByText('BETA PLANTA2 2025')).toBeInTheDocument()
  })

  it('el botón de actualizar del sub-paso A llama a onActualizarInspecciones', async () => {
    const onActualizarInspecciones = vi.fn()
    render(
      <KioskScreen {...baseProps({ accionInicial: 'subir', inspeccion: null, onActualizarInspecciones })} />
    )
    await userEvent.click(screen.getByRole('button', { name: /actualizar lista de inspecciones/i }))
    expect(onActualizarInspecciones).toHaveBeenCalledTimes(1)
  })

  it('elegir una inspección del buscador llama a onSelectInspeccion con el objeto completo', async () => {
    const onSelectInspeccion = vi.fn()
    render(
      <KioskScreen {...baseProps({ accionInicial: 'subir', inspeccion: null, onSelectInspeccion })} />
    )
    await userEvent.click(screen.getByText('ACME PLANTA1 2026'))
    expect(onSelectInspeccion).toHaveBeenCalledTimes(1)
    expect(onSelectInspeccion).toHaveBeenCalledWith(inspecciones[0])
  })

  it('con inspección ya elegida se muestra su etiqueta y "Cambiar" en vez del buscador', () => {
    render(
      <KioskScreen
        {...baseProps({ accionInicial: 'subir', inspeccion: inspecciones[0] })}
      />
    )
    expect(screen.getByDisplayValue('ACME PLANTA1 2026')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/escribe para buscar/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cambiar/i })).toBeInTheDocument()
  })

  it('"Cambiar" limpia la inspección elegida (onSelectInspeccion con null)', async () => {
    const onSelectInspeccion = vi.fn()
    render(
      <KioskScreen
        {...baseProps({ accionInicial: 'subir', inspeccion: inspecciones[0], onSelectInspeccion })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /cambiar/i }))
    expect(onSelectInspeccion).toHaveBeenCalledWith(null)
  })
})

describe('KioskScreen — paso 2 (subir en crudo), pantalla de resumen', () => {
  beforeEach(() => vi.clearAllMocks())

  it('al pulsar "Subir" con onComprobarSubida se pinta el resumen antes de subir, y solo tras Aceptar se sube', async () => {
    const onSubirCrudo = vi.fn()
    const onComprobarSubida = vi.fn().mockResolvedValue({
      prepare: { ok: true, files: 120, bytes: 1024 * 1024 * 500, pendientes: 80, bytes_pendientes: 1024 * 1024 * 300 },
      estadillos: {
        n_estadillos: 3,
        info: { fechas: ['01/08/2026', '02/08/2026', '03/08/2026'], num_vuelos: 12, pilotos: ['Nacho'], drones: ['M3T'] },
      },
    })
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion: inspecciones[0],
          onSubirCrudo,
          onComprobarSubida,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^subir$/i }))
    expect(onComprobarSubida).toHaveBeenCalledTimes(1)
    expect(onComprobarSubida).toHaveBeenCalledWith({ carpeta: '/home/pi/vuelo/PLANTA', inspeccion: inspecciones[0] })

    const resumen = await screen.findByTestId('kiosk-resumen')
    expect(resumen).toBeInTheDocument()
    // 3 estadillos y 3 días de vuelo: dos "3" distintos en la misma pantalla.
    expect(screen.getAllByText('3')).toHaveLength(2)
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(onSubirCrudo).not.toHaveBeenCalled()

    await userEvent.click(screen.getByTestId('kiosk-resumen-aceptar'))
    expect(onSubirCrudo).toHaveBeenCalledTimes(1)
    expect(onSubirCrudo).toHaveBeenCalledWith({ carpeta: '/home/pi/vuelo/PLANTA', inspeccion: inspecciones[0] })
  })

  it('sin estadillos detectados pero con archivos pendientes, sale el aviso pero no bloquea el botón', async () => {
    const onComprobarSubida = vi.fn().mockResolvedValue({
      prepare: { ok: true, files: 10, bytes: 1024, pendientes: 10, bytes_pendientes: 1024 },
      estadillos: { n_estadillos: 0, info: null },
    })
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion: inspecciones[0],
          onComprobarSubida,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^subir$/i }))
    await screen.findByTestId('kiosk-resumen')
    expect(screen.getByTestId('kiosk-resumen-sin-estadillo')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-resumen-aceptar')).not.toBeDisabled()
  })

  it('sin archivos pendientes (prepare.ok=false), sale el aviso y el botón Aceptar queda deshabilitado', async () => {
    const onComprobarSubida = vi.fn().mockResolvedValue({
      prepare: { ok: false, error: 'La carpeta no tiene ningún fichero subible' },
      estadillos: { n_estadillos: 1, info: { fechas: ['01/08/2026'], num_vuelos: 1, pilotos: [], drones: [] } },
    })
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion: inspecciones[0],
          onComprobarSubida,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^subir$/i }))
    await screen.findByTestId('kiosk-resumen')
    expect(screen.getByTestId('kiosk-resumen-sin-archivos')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-resumen-aceptar')).toBeDisabled()
  })

  it('sin la prop onComprobarSubida, pulsar "Subir" llama directo a onSubirCrudo (comportamiento antiguo)', async () => {
    const onSubirCrudo = vi.fn()
    render(
      <KioskScreen
        {...baseProps({
          accionInicial: 'subir',
          carpeta: '/home/pi/vuelo/PLANTA',
          inspeccion: inspecciones[0],
          onSubirCrudo,
        })}
      />
    )
    await userEvent.click(screen.getByRole('button', { name: /^subir$/i }))
    expect(onSubirCrudo).toHaveBeenCalledTimes(1)
    expect(onSubirCrudo).toHaveBeenCalledWith({ carpeta: '/home/pi/vuelo/PLANTA', inspeccion: inspecciones[0] })
    expect(screen.queryByTestId('kiosk-resumen')).not.toBeInTheDocument()
  })
})

describe('KioskScreen — pantalla de resultado', () => {
  beforeEach(() => vi.clearAllMocks())

  it('resultado ok con subidos>0 muestra "Subida completada" y el detalle menciona los archivos subidos', () => {
    render(
      <KioskScreen
        {...baseProps({ resultado: { ok: true, subidos: 42, bytes: 1024 * 1024 * 10 } })}
      />
    )
    expect(screen.getByTestId('kiosk-resultado')).toBeInTheDocument()
    expect(screen.getByText('Subida completada')).toBeInTheDocument()
    expect(screen.getByTestId('kiosk-resultado-detalle')).toHaveTextContent('42 archivos subidos')
  })

  it('el detalle pinta la garantía del backend: verificados en la nube e intentos', () => {
    render(
      <KioskScreen
        {...baseProps({
          resultado: { ok: true, subidos: 1213, verificado: true,
                       verificados: 1213, items_total: 1213, rondas: 3 },
        })}
      />
    )
    const detalle = screen.getByTestId('kiosk-resultado-detalle')
    expect(detalle).toHaveTextContent('1213/1213 verificados en la nube')
    expect(detalle).toHaveTextContent('3 intentos')
  })

  it('si no se pudo listar el bucket, el detalle lo dice en vez de mentir', () => {
    render(
      <KioskScreen
        {...baseProps({
          resultado: { ok: true, subidos: 5, verificado: false,
                       verificados: 0, items_total: 5, rondas: 1 },
        })}
      />
    )
    const detalle = screen.getByTestId('kiosk-resultado-detalle')
    expect(detalle).toHaveTextContent('sin comprobar en la nube')
    expect(detalle).not.toHaveTextContent('intentos')
  })

  it('un payload viejo (sin items_total) no pinta ni undefined ni garantía', () => {
    render(<KioskScreen {...baseProps({ resultado: { ok: true, subidos: 7 } })} />)
    const detalle = screen.getByTestId('kiosk-resultado-detalle')
    expect(detalle).toHaveTextContent('7 archivos subidos')
    expect(detalle).not.toHaveTextContent('verificados')
    expect(detalle).not.toHaveTextContent('undefined')
  })

  it('resultado ok con subidos=0 muestra "No había nada nuevo que subir"', () => {
    render(<KioskScreen {...baseProps({ resultado: { ok: true, subidos: 0 } })} />)
    expect(screen.getByText('No había nada nuevo que subir')).toBeInTheDocument()
  })

  it('resultado {ok:false, error} muestra el error en el detalle y "Aceptar" llama a onCerrarResultado', async () => {
    const onCerrarResultado = vi.fn()
    render(
      <KioskScreen
        {...baseProps({ resultado: { ok: false, error: 'X' }, onCerrarResultado })}
      />
    )
    expect(screen.getByTestId('kiosk-resultado-detalle')).toHaveTextContent('X')
    await userEvent.click(screen.getByTestId('kiosk-resultado-aceptar'))
    expect(onCerrarResultado).toHaveBeenCalledTimes(1)
  })
})
