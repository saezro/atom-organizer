import { useEffect, useRef, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import { api, isServerMode } from './bridge.js'

const LONGITUD = 4
// El panel tactil resistivo de la Pi a veces registra un toque como dos
// eventos seguidos (rebote mecanico): se ignora cualquier tecla repetida
// dentro de esta ventana.
const DEBOUNCE_TOQUE_MS = 150
const FILAS = [
  ['1', '2', '3'],
  ['4', '5', '6'],
  ['7', '8', '9'],
]

// Cada modo es una secuencia de pasos; cada paso pide un PIN completo.
const PASOS = {
  verificar: ['verificar'],
  fijar: ['nuevo', 'repetir'],
  cambiar: ['actual', 'nuevo', 'repetir'],
}

const TITULOS = {
  verificar: 'Introduce el PIN',
  actual: 'PIN actual',
  nuevo: 'PIN nuevo',
  repetir: 'Repite el PIN nuevo',
}

// Telemetria del paso 'verificar' (unico que manda al backend): contadores
// de un intento, sin rastro alguno del PIN tecleado. Se vacia en cada
// `completar()`, tanto si se manda como si no (pasos que no son
// 'verificar').
function telemetriaVacia() {
  return { aceptados: 0, descartadosDebounce: 0, borrados: 0, intervalos: [], ultimoTs: null, inicioTs: null }
}

export default function KioskLock({ modo = 'verificar', onOk, onCancelar, simularEntrada = 0 }) {
  const tactil = isServerMode()
  const pasos = PASOS[modo] || PASOS.verificar
  const [paso, setPaso] = useState(0)
  const [pin, setPin] = useState('')
  const [error, setError] = useState('')
  const [espera, setEspera] = useState(0)
  const [ocupado, setOcupado] = useState(false)
  // Contador que se incrementa en cada fallo: usado como `key` para
  // reiniciar la animacion del marco rojo y del shake aunque el fallo
  // anterior no haya terminado de desvanecerse.
  const [fallo, setFallo] = useState(0)

  // El pad puede recibir varios toques seguidos antes de que React llegue a
  // repintar (en un panel resistivo real, o en un test sin await entre
  // medias). El estado de React es solo para PINTAR; el avance de verdad
  // (paso actual, PIN acumulado, PIN previo a comparar) vive en refs para no
  // depender de un cierre desactualizado.
  const pasoRef = useRef(0)
  const pinRef = useRef('')
  const previosRef = useRef({})
  // Tecla y marca de tiempo del ultimo toque aceptado (borrar usa su propia
  // clave 'borrar'): solo se descarta la MISMA tecla repetida a <150ms, que
  // es el rebote/ghost tap del panel resistivo; teclas distintas siempre
  // cuentan aunque lleguen casi juntas al teclear rapido.
  const ultimoToqueRef = useRef({ tecla: null, ts: 0 })

  // Telemetria del intento en curso (paso 'verificar') y contador de
  // intentos consecutivos desde el ultimo desbloqueo. Ver
  // `pin_telemetria` (bridge.js / app_webview.py): fire-and-forget, jamas
  // bloquea la UI ni lleva digitos del PIN.
  const telRef = useRef(telemetriaVacia())
  const nIntentoRef = useRef(0)

  function toqueValido(tecla) {
    const ahora = Date.now()
    const ultimo = ultimoToqueRef.current
    if (ultimo.tecla === tecla && ahora - ultimo.ts < DEBOUNCE_TOQUE_MS) {
      telRef.current.descartadosDebounce += 1
      return false
    }
    ultimoToqueRef.current = { tecla, ts: ahora }
    return true
  }

  function registrarToqueAceptado() {
    const t = telRef.current
    const ahora = Date.now()
    if (t.inicioTs === null) t.inicioTs = ahora
    if (t.ultimoTs !== null) t.intervalos.push(ahora - t.ultimoTs)
    t.ultimoTs = ahora
    t.aceptados += 1
  }

  // Envia la telemetria del intento de 'verificar' que acaba de resolverse
  // y prepara el contador de intentos para el siguiente. `ok` en `false`
  // (o si el propio envio falla) nunca bloquea ni afecta a la UI: es
  // fire-and-forget puro.
  function enviarTelemetria(t, ok) {
    nIntentoRef.current += 1
    const payload = {
      ts: new Date().toISOString(),
      ok,
      n_intento: nIntentoRef.current,
      toques_aceptados: t.aceptados,
      toques_descartados_debounce: t.descartadosDebounce,
      borrados: t.borrados,
      intervalos_ms: t.intervalos,
      duracion_total_ms: t.inicioTs !== null ? Date.now() - t.inicioTs : 0,
    }
    if (ok) nIntentoRef.current = 0
    api.pinTelemetria(payload).catch(() => {})
  }

  // Login remoto (`atom:control_ui` accion 'login', ver `KioskGuard.jsx`):
  // el backend YA validó el PIN (portátil vinculado), así que aquí solo se
  // ANIMA el desbloqueo rellenando los cuatro puntos uno a uno (sin revelar
  // el PIN real, sin resaltar ni pulsar ninguna tecla del teclado: quien
  // mire la pantalla no debe poder leer nada) — y al terminar se sigue el
  // MISMO camino de éxito que el pad (`onOk`), SIN llamar a
  // `api.pinVerificar`. Cada incremento de `simularEntrada` dispara una
  // animación nueva; solo tiene sentido en el paso 'verificar' (login), pero
  // no hace falta comprobarlo aquí: quien decide cuándo incrementar el
  // contador es `KioskGuard`.
  useEffect(() => {
    if (!simularEntrada) return undefined
    let cancelado = false
    const timers = []
    for (let i = 0; i < LONGITUD; i += 1) {
      timers.push(setTimeout(() => {
        if (cancelado) return
        setPin((p) => (p + '0').slice(0, LONGITUD))
      }, (i + 1) * 250))
    }
    timers.push(setTimeout(() => {
      if (cancelado) return
      setPin('')
      onOk?.()
    }, (LONGITUD + 1) * 250))
    return () => { cancelado = true; timers.forEach(clearTimeout) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simularEntrada])

  // La cuenta atras del bloqueo la lleva el frontend; el backend sigue
  // siendo quien decide, esto solo evita teclear en balde.
  useEffect(() => {
    if (espera <= 0) return undefined
    const t = setTimeout(() => setEspera((s) => s - 1), 1000)
    return () => clearTimeout(t)
  }, [espera])

  const bloqueado = espera > 0 || ocupado

  // Fallo (PIN incorrecto, repeticion que no coincide, guardado fallido): el
  // mensaje va a un texto solo-lectores (no ocupa layout) y dispara el marco
  // rojo a pantalla completa + el shake de los puntos. El PIN tecleado ya
  // esta vacio en todas las llamadas (se limpia al entrar a `completar`, o
  // aqui mismo para "no coinciden").
  function marcarError(msg) {
    setError(msg)
    setFallo((f) => f + 1)
  }

  async function completar(valor) {
    const actual = pasos[pasoRef.current]
    pinRef.current = ''
    setPin('')
    // Se toma una foto de la telemetria acumulada y se vacia el contador ya:
    // el siguiente intento (o paso) arranca limpio pase lo que pase abajo.
    const telemetria = telRef.current
    telRef.current = telemetriaVacia()
    if (actual === 'verificar') {
      setOcupado(true)
      // `finally` reactiva el teclado pase lo que pase: si `pinVerificar`
      // rechaza (bridge colgado, plazo vencido — bridge.js le da 20 s por
      // defecto) el `.catch` de abajo ya lo convierte en `{ok:false}`, pero
      // sin el `finally` un fallo INESPERADO (p.ej. dentro de
      // `enviarTelemetria`) dejaría `ocupado` en `true` para siempre y el
      // teclado muerto.
      try {
        const res = await api.pinVerificar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
        enviarTelemetria(telemetria, res?.ok === true)
        if (res?.ok) { onOk?.(); return }
        marcarError(res?.error || 'PIN incorrecto.')
        setEspera(res?.espera_segundos || 0)
      } finally {
        setOcupado(false)
      }
      return
    }
    if (actual === 'repetir') {
      if (valor !== previosRef.current.nuevo) {
        marcarError('Los PIN no coinciden.')
        pasoRef.current = pasos.indexOf('nuevo')
        setPaso(pasoRef.current)
        return
      }
      setOcupado(true)
      try {
        const res = modo === 'cambiar'
          ? await api.pinCambiar(previosRef.current.actual, valor).catch(() => ({ ok: false, error: 'No responde.' }))
          : await api.pinFijar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
        if (res?.ok) { onOk?.(); return }
        marcarError(res?.error || 'No se pudo guardar el PIN.')
        setEspera(res?.espera_segundos || 0)
        pasoRef.current = 0
        setPaso(0)
      } finally {
        setOcupado(false)
      }
      return
    }
    previosRef.current = { ...previosRef.current, [actual]: valor }
    setError('')
    pasoRef.current += 1
    setPaso(pasoRef.current)
  }

  function pulsar(digito) {
    if (bloqueado) return
    if (!toqueValido(digito)) return
    setError('')
    registrarToqueAceptado()
    const valor = (pinRef.current + digito).slice(0, LONGITUD)
    pinRef.current = valor
    setPin(valor)
    if (valor.length === LONGITUD) completar(valor)
  }

  function borrar() {
    if (bloqueado) return
    if (!toqueValido('borrar')) return
    telRef.current.borrados += 1
    const valor = pinRef.current.slice(0, -1)
    pinRef.current = valor
    setPin(valor)
  }

  function borrarTodo() {
    if (bloqueado) return
    pinRef.current = ''
    setPin('')
  }

  const puntos = Array.from({ length: LONGITUD }, (_, i) => (
    <span key={i} className={i < pin.length ? 'kiosk-pin-punto lleno' : 'kiosk-pin-punto'} />
  ))

  return (
    <div className="kiosk kiosk-pin" data-testid="kiosk-pin">
      {/* Marco rojo a pantalla completa: fixed, pointer-events none, se
          desvanece solo en ~1s. `key` reinicia la animacion en cada fallo
          aunque el anterior no haya terminado de desvanecerse. */}
      {fallo > 0 && <div key={fallo} className="kiosk-pin-flash" aria-hidden="true" />}
      {/* Agrupa titulo+puntos+mensaje en un solo bloque: en desktop es
          transparente (gap propio replica el gap que tenian sueltos), en
          la pantalla pequena de la Pi permite ponerlos en una columna a la
          izquierda mientras el teclado ocupa toda la altura a la derecha
          (dianas grandes para el tactil resistivo, que calibra mal). */}
      <div className="kiosk-pin-info">
        <h2 className="kiosk-pin-titulo">{TITULOS[pasos[paso]] || TITULOS.verificar}</h2>
        <div
          key={`puntos-${fallo}`}
          className={fallo > 0 ? 'kiosk-pin-puntos kiosk-pin-shake' : 'kiosk-pin-puntos'}
        >
          {puntos}
        </div>
        {/* El error ya no ocupa hueco visual (antes desplazaba el teclado):
            solo para lectores de pantalla, el marco rojo es la senal visible. */}
        <p className="kiosk-pin-sr" role="alert" data-testid="kiosk-pin-error">{error}</p>
        <div className="kiosk-pin-mensaje">
          {espera > 0 && (
            <p className="kiosk-pin-espera">Demasiados intentos. Espera {espera} s.</p>
          )}
        </div>
      </div>
      <div className="kiosk-pin-pad">
        {FILAS.flat().map((d) => (
          <BotonToque
            key={d}
            className="kiosk-pin-tecla"
            tactil={tactil}
            aria-label={d}
            disabled={bloqueado}
            onActivar={() => pulsar(d)}
          >
            {d}
          </BotonToque>
        ))}
        {onCancelar ? (
          <BotonToque
            className="kiosk-pin-tecla kiosk-pin-aux"
            tactil={tactil}
            disabled={bloqueado}
            onActivar={onCancelar}
          >
            Cancelar
          </BotonToque>
        ) : (
          <span className="kiosk-pin-tecla kiosk-pin-hueco" />
        )}
        <BotonToque
          className="kiosk-pin-tecla"
          tactil={tactil}
          aria-label="0"
          disabled={bloqueado}
          onActivar={() => pulsar('0')}
        >
          0
        </BotonToque>
        <BotonToque
          className="kiosk-pin-tecla kiosk-pin-aux"
          tactil={tactil}
          aria-label="Borrar"
          disabled={bloqueado}
          onActivar={borrar}
          onPulsarLargo={borrarTodo}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z" />
            <line x1="18" y1="9" x2="12" y2="15" />
            <line x1="12" y1="9" x2="18" y2="15" />
          </svg>
        </BotonToque>
      </div>
    </div>
  )
}
