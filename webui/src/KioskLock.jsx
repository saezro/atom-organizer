import { useEffect, useRef, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import { api, isServerMode } from './bridge.js'

const LONGITUD = 4
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

export default function KioskLock({ modo = 'verificar', onOk, onCancelar }) {
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
    if (actual === 'verificar') {
      setOcupado(true)
      const res = await api.pinVerificar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
      setOcupado(false)
      if (res?.ok) { onOk?.(); return }
      marcarError(res?.error || 'PIN incorrecto.')
      setEspera(res?.espera_segundos || 0)
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
      const res = modo === 'cambiar'
        ? await api.pinCambiar(previosRef.current.actual, valor).catch(() => ({ ok: false, error: 'No responde.' }))
        : await api.pinFijar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
      setOcupado(false)
      if (res?.ok) { onOk?.(); return }
      marcarError(res?.error || 'No se pudo guardar el PIN.')
      setEspera(res?.espera_segundos || 0)
      pasoRef.current = 0
      setPaso(0)
      return
    }
    previosRef.current = { ...previosRef.current, [actual]: valor }
    setError('')
    pasoRef.current += 1
    setPaso(pasoRef.current)
  }

  function pulsar(digito) {
    if (bloqueado) return
    setError('')
    const valor = (pinRef.current + digito).slice(0, LONGITUD)
    pinRef.current = valor
    setPin(valor)
    if (valor.length === LONGITUD) completar(valor)
  }

  function borrar() {
    if (bloqueado) return
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
