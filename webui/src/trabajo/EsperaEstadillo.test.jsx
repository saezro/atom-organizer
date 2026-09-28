// Pantalla «Esperando estadillo» (modo «Recibir del portátil» de
// PasoEstadillo): arranca `estadillo_espera_iniciar` y sondea
// `estadillo_espera_estado` cada 2 s. Fases por color: esperando (amarillo,
// cuenta atrás), conectado (naranja suave), recibiendo (naranja, spinner),
// recibido_ok (verde, sigue solo o botón Empezar), rechazado (rojo, errores,
// sigue esperando) y caducado (gris, Volver a esperar/Cancelar).
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const estadilloEsperaIniciar = vi.fn()
const estadilloEsperaCancelar = vi.fn()
const estadilloEsperaEstado = vi.fn()
const estadilloEsperaCarpeta = vi.fn()

vi.mock('../bridge', () => ({
  api: {
    estadilloEsperaIniciar: (...a) => estadilloEsperaIniciar(...a),
    estadilloEsperaCancelar: (...a) => estadilloEsperaCancelar(...a),
    estadilloEsperaEstado: (...a) => estadilloEsperaEstado(...a),
    estadilloEsperaCarpeta: (...a) => estadilloEsperaCarpeta(...a),
  },
}))

const EsperaEstadillo = (await import('./EsperaEstadillo.jsx')).default

// Deja correr las promesas pendientes (respuesta del bridge mockeado) y, con
// ellas, los cambios de estado que React tiene que aplicar. Con fake timers
// `await` a secas no basta: hace falta ceder el microtask queue de verdad
// (mismo patrón que PairScreen.test.jsx).
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

describe('EsperaEstadillo', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    estadilloEsperaIniciar.mockReset().mockResolvedValue({ started: true })
    estadilloEsperaCancelar.mockReset().mockResolvedValue({ ok: true })
    estadilloEsperaEstado.mockReset()
    estadilloEsperaCarpeta.mockReset().mockResolvedValue({ ok: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('fase ESPERANDO (derivada, sin campo fase): cuenta atrás, direcciones sin puerto (80) y fotos', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      fotos: { total: 5, calculando: false, primera: '2026-09-21T08:00:00Z', ultima: '2026-09-21T08:10:00Z' },
      errores: [],
      red: { hostname: 'organizer.local', puerto: 80, ips: [{ interfaz: 'wlan0', ip: '10.42.0.1' }] },
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={{ etiqueta: 'ACME--P--2026--T' }} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(estadilloEsperaIniciar).toHaveBeenCalledWith('/c', { etiqueta: 'ACME--P--2026--T' })
    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'esperando')
    expect(screen.getByText('Esperando estadillo del portátil')).toBeInTheDocument()
    expect(screen.getByText('http://organizer.local')).toBeInTheDocument()
    expect(screen.getByText('http://10.42.0.1 (Wifi)')).toBeInTheDocument()
    expect(screen.getByText(/5 fotos/)).toBeInTheDocument()
    // Cuenta atrás desde los 10 min por defecto (sin caduca_en/segundos_restantes).
    expect(document.querySelector('.espera-contador')).toHaveTextContent(/^(09:5\d|10:00)$/)
  })

  it('fase ESPERANDO: hostname sin sufijo mDNS se muestra con .local añadido', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      fotos: { total: 0, calculando: false },
      errores: [],
      red: { hostname: 'organizer', puerto: 80, ips: [] },
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={{ etiqueta: 'ACME--P--2026--T' }} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByText('http://organizer.local')).toBeInTheDocument()
    expect(screen.queryByText('http://organizer')).not.toBeInTheDocument()
  })

  it('fase ESPERANDO: usa red.url del backend cuando viene con .local ya resuelto', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      fotos: { total: 0, calculando: false },
      errores: [],
      red: { hostname: 'organizer', puerto: 80, ips: [], url: 'http://organizer.local' },
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={{ etiqueta: 'ACME--P--2026--T' }} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByText('http://organizer.local')).toBeInTheDocument()
  })

  it('fase CONECTADO (explícita del backend): título con ip y sigue esperando', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      fase: 'conectado',
      ultimo_contacto: { ip: '10.42.0.7', cuando: '2026-09-21T08:00:00Z', accion: 'ping' },
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'conectado')
    expect(screen.getByText('Portátil conectado · 10.42.0.7')).toBeInTheDocument()
  })

  it('fase RECIBIENDO (explícita del backend): spinner y sin cuenta atrás', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      fase: 'recibiendo',
      ultimo_contacto: { ip: '10.42.0.7', cuando: '2026-09-21T08:00:00Z', accion: 'envio' },
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'recibiendo')
    expect(screen.getByText('Recibiendo estadillo…')).toBeInTheDocument()
    expect(document.querySelector('.espera-contador')).not.toBeInTheDocument()
  })

  it('fase RECIBIDO_OK: modal con resumen que NO avanza solo (ni tras muchos polls), solo "OK, seguir" avisa', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.xlsx'],
        info: { pilotos: ['Juan'], drones: ['Dron1'], num_vuelos: 3 },
      })

    const onRecibido = vi.fn()
    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={onRecibido} onCancelar={vi.fn()} />)
    await flush()
    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'esperando')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'recibido_ok')
    expect(screen.getByText('Estadillo recibido · 3 vuelos')).toBeInTheDocument()
    expect(screen.getByText('Juan')).toBeInTheDocument()
    expect(onRecibido).not.toHaveBeenCalled()

    // Nada de avance automático: por mucho que pase el tiempo y sigan
    // corriendo los polls, el modal se queda abierto hasta que se pulse el
    // botón (pedido de Rodrigo: "nada de auto-avance ni timeout").
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000)
    })
    expect(onRecibido).not.toHaveBeenCalled()
    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'recibido_ok')

    fireEvent.click(screen.getByText('OK, seguir'))
    // Segundo argumento: el resumen rico para que `EstadilloField` lo siga
    // pintando después de que este modal se desmonte (pedido de Rodrigo).
    expect(onRecibido).toHaveBeenCalledWith(
      ['/estadillo.xlsx'],
      expect.objectContaining({
        origen: 'recibido',
        pilotos: ['Juan'],
        drones: ['Dron1'],
        numVuelos: 3,
      })
    )
    expect(onRecibido).toHaveBeenCalledTimes(1)

    // Ni un segundo aviso tras el click, por mucho que siga corriendo el reloj.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(onRecibido).toHaveBeenCalledTimes(1)
  })

  it('fase RECIBIDO_OK con `resumen` (forma real del backend): pinta pilotos/drones/nº vuelos', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.xlsx'],
        resumen: { planta: 'X', fecha: '2026-01-01', pilotos: ['Ana'], drones: ['Dron2'], n_vuelos: 5 },
      })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'recibido_ok')
    expect(screen.getByText('Estadillo recibido · 5 vuelos')).toBeInTheDocument()
    expect(screen.getByText('Ana')).toBeInTheDocument()
    expect(screen.getByText('Dron2')).toBeInTheDocument()
  })

  it('onRecibido: calcula vuelos con duración y el tiempo de vuelo total a partir de `resumen.vuelos`', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.csv'],
        resumen: {
          planta: 'KL05',
          fecha: '2026-09-20',
          fechas: ['2026-09-20'],
          pilotos: ['Rebeca'],
          drones: ['M300'],
          n_vuelos: 2,
          vuelos: [
            { pb: 'PB1', vuelo: '1', fecha: '2026-09-20', inicio: '09:00', final: '09:40', cruza_medianoche: false },
            { pb: 'PB1', vuelo: '2', fecha: '2026-09-20', inicio: '10:00', final: '10:35', cruza_medianoche: false },
          ],
        },
      })

    const onRecibido = vi.fn()
    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={onRecibido} onCancelar={vi.fn()} />)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    fireEvent.click(screen.getByText('OK, seguir'))
    expect(onRecibido).toHaveBeenCalledWith(
      ['/estadillo.csv'],
      expect.objectContaining({
        vuelos: [
          { n: '1', inicio: '09:00', fin: '09:40', duracionMin: 40 },
          { n: '2', inicio: '10:00', fin: '10:35', duracionMin: 35 },
        ],
        tiempoTotalMin: 75,
      })
    )
  })

  it('fase RECHAZADO (explícita del backend): errores legibles y sigue esperando reenvío', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      fase: 'rechazado',
      errores: ['Fila 3: fecha inválida'],
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'rechazado')
    expect(screen.getByText('Fila 3: fecha inválida')).toBeInTheDocument()
    expect(screen.getByText('El portátil puede reenviarlo; se sigue esperando.')).toBeInTheDocument()
  })

  it('fase RECHAZADO sin `errores`: pinta el `motivo` que mande el backend (ni "rechazado" a secas)', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      fase: 'rechazado',
      motivo: 'No hay carpeta seleccionada en el Organizer',
      errores: [],
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-motivo')).toHaveTextContent(
      'No hay carpeta seleccionada en el Organizer'
    )
  })

  it('fase RECHAZADO sin `motivo` ni `errores`: cae al detalle del último evento "rechazado"', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      fase: 'rechazado',
      errores: [],
      eventos: [
        { cuando: '2026-09-22T10:00:00Z', ip: '10.42.0.7', tipo: 'envio' },
        { cuando: '2026-09-22T10:00:01Z', ip: '10.42.0.7', tipo: 'rechazado', detalle: 'ya se habia recibido (409)' },
      ],
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-motivo')).toHaveTextContent('ya se habia recibido (409)')
  })

  it('fase RECIBIDO_OK con `validacion` (ejemplo real, CSV Rebeca 2026-09-20): semáforo verde y lista por vuelo', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.csv'],
        resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
        validacion: {
          ok: true,
          vuelos: [
            { id: 'PB1_V1', fotos: 30, esperado: 30, estado: 'ok' },
            { id: 'PB1_V2', fotos: 28, esperado: 28, estado: 'ok' },
          ],
          fotos_fuera: 0,
          avisos: [],
        },
      })

    render(<EsperaEstadillo carpeta="/media/pi/USB_HDD/KL05" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(screen.getByTestId('espera-semaforo')).toHaveTextContent('Todo encaja')
    expect(screen.getByText('KL05')).toBeInTheDocument()
    expect(screen.getByText('2026-09-20')).toBeInTheDocument()
    const lista = screen.getByTestId('espera-validacion-vuelos')
    expect(lista).toHaveTextContent('PB1_V1')
    expect(lista).toHaveTextContent('30/30 fotos')
  })

  it('fase RECIBIDO_OK con `validacion` con problemas: semáforo ámbar, avisos y "OK, seguir" sigue habilitado', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.csv'],
        resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
        validacion: {
          ok: false,
          vuelos: [
            { id: 'PB1_V1', fotos: 12, esperado: 30, estado: 'pocas_fotos' },
            { id: 'PB1_V2', fotos: 0, esperado: 28, estado: 'sin_fotos' },
          ],
          fotos_fuera: 3,
          avisos: ['PB1_V2 no tiene fotos en la carpeta'],
        },
      })

    render(<EsperaEstadillo carpeta="/media/pi/USB_HDD/KL05" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(screen.getByTestId('espera-semaforo')).toHaveTextContent(/Revisar: \d avisos?/)
    expect(screen.getByText('3 fotos fuera de vuelo')).toBeInTheDocument()
    expect(screen.getByText('PB1_V2 no tiene fotos en la carpeta')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'OK, seguir' })).not.toBeDisabled()
  })

  it('fase RECIBIDO_OK sin `validacion` (backend todavía no la manda): no rompe nada, sección oculta', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.csv'],
        resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
      })

    render(<EsperaEstadillo carpeta="/media/pi/USB_HDD/KL05" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(screen.queryByTestId('espera-semaforo')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'OK, seguir' })).toBeInTheDocument()
  })

  it('fase RECIBIDO_OK sin carpeta elegida (pendiente_carpeta): avisa "Se aplicará al elegir la carpeta"', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, carpeta_seleccionada: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: true,
        recibido: true,
        pendiente_carpeta: true,
        carpeta_seleccionada: false,
        carpeta: null,
        rutas: ['/estadillo.csv'],
        resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
      })

    render(<EsperaEstadillo carpeta={null} inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'recibido_ok')
    expect(screen.getByTestId('espera-pendiente-carpeta')).toHaveTextContent('Se aplicará al elegir la carpeta')
  })

  it('fase RECIBIDO_OK: el resumen se abre como modal (portal a document.body, role dialog)', async () => {
    estadilloEsperaEstado
      .mockResolvedValueOnce({ esperando: true, recibido: false, errores: [] })
      .mockResolvedValueOnce({
        esperando: false,
        recibido: true,
        rutas: ['/estadillo.csv'],
        resumen: { planta: 'KL05', fecha: '2026-09-20', pilotos: ['Rebeca'], drones: ['M300'], n_vuelos: 2 },
      })

    const { container } = render(
      <EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />
    )
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    // El panel NO cuelga del contenedor donde se montó el componente: sale
    // por portal a document.body, como un modal de verdad por encima de lo
    // que hubiera debajo.
    expect(container.querySelector('[data-testid="espera-estadillo"]')).toBeNull()
    const panel = screen.getByTestId('espera-estadillo')
    expect(panel.closest('[role="dialog"]')).toBe(panel)
    expect(panel).toHaveAttribute('aria-modal', 'true')
  })

  it('fase CADUCADO: aviso gris y "Volver a esperar" rearranca la espera', async () => {
    estadilloEsperaEstado.mockResolvedValue({ esperando: true, recibido: false, caducado: true, errores: [] })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'caducado')
    expect(screen.getByText('Tiempo de espera agotado')).toBeInTheDocument()
    expect(estadilloEsperaIniciar).toHaveBeenCalledTimes(1)

    estadilloEsperaEstado.mockResolvedValue({ esperando: true, recibido: false, errores: [] })
    fireEvent.click(screen.getByText('Volver a esperar'))
    await flush()

    expect(estadilloEsperaIniciar).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'esperando')
  })

  it('errores de validación en curso (sin fase explícita) se muestran sin dejar de esperar', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      errores: ['Fila 3: fecha inválida'],
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'esperando')
    expect(screen.getByText('Fila 3: fecha inválida')).toBeInTheDocument()
  })

  it('registro de actividad: últimos eventos, el más reciente arriba', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      errores: [],
      eventos: [
        { cuando: '2026-09-21T08:00:00Z', ip: '10.42.0.7', tipo: 'ping' },
        { cuando: '2026-09-21T08:00:05Z', ip: '10.42.0.7', tipo: 'consulta', detalle: 'GET /estado' },
      ],
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    const registro = screen.getByTestId('espera-registro')
    const lineas = registro.querySelectorAll('.espera-registro-linea')
    expect(lineas).toHaveLength(2)
    // El más reciente (consulta) va primero.
    expect(lineas[0]).toHaveTextContent('consulta · GET /estado')
    expect(lineas[1]).toHaveTextContent('ping')
  })

  it('sin carpeta seleccionada avisa en texto, SIN botón de selector propio (ya está arriba)', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      carpeta_seleccionada: false,
      carpeta: null,
      aviso: 'No hay carpeta seleccionada en el Organizer',
      errores: [],
    })

    render(<EsperaEstadillo carpeta={null} inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.getByTestId('espera-carpeta')).toBeInTheDocument()
    expect(screen.getByText('Elige carpeta arriba')).toBeInTheDocument()
    expect(screen.queryByText('Elegir carpeta…')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /elegir carpeta/i })).not.toBeInTheDocument()
  })

  it('con carpeta ya seleccionada no muestra el aviso de "Elige carpeta"', async () => {
    estadilloEsperaEstado.mockResolvedValue({
      esperando: true,
      recibido: false,
      carpeta_seleccionada: true,
      carpeta: '/c',
      errores: [],
    })

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={vi.fn()} />)
    await flush()

    expect(screen.queryByTestId('espera-carpeta')).not.toBeInTheDocument()
    expect(screen.queryByText('Elige carpeta')).not.toBeInTheDocument()
  })

  it('Cancelar avisa al backend y al padre', async () => {
    estadilloEsperaEstado.mockResolvedValue({ esperando: true, recibido: false, errores: [] })
    const onCancelar = vi.fn()

    render(<EsperaEstadillo carpeta="/c" inspeccion={null} onRecibido={vi.fn()} onCancelar={onCancelar} />)
    await flush()

    fireEvent.click(screen.getByText('Cancelar'))
    expect(estadilloEsperaCancelar).toHaveBeenCalled()
    expect(onCancelar).toHaveBeenCalled()
  })
})
