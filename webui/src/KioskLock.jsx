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

  async function completar(valor) {
    const actual = pasos[pasoRef.current]
    pinRef.current = ''
    setPin('')
    if (actual === 'verificar') {
      setOcupado(true)
      const res = await api.pinVerificar(valor).catch(() => ({ ok: false, error: 'No responde.' }))
      setOcupado(false)
      if (res?.ok) { onOk?.(); return }
      setError(res?.error || 'PIN incorrecto.')
      setEspera(res?.espera_segundos || 0)
      return
    }
    if (actual === 'repetir') {
      if (valor !== previosRef.current.nuevo) {
        setError('Los PIN no coinciden.')
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
      setError(res?.error || 'No se pudo guardar el PIN.')
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

  const puntos = Array.from({ length: LONGITUD }, (_, i) => (
    <span key={i} className={i < pin.length ? 'kiosk-pin-punto lleno' : 'kiosk-pin-punto'} />
  ))

  return (
    <div className="kiosk kiosk-pin" data-testid="kiosk-pin">
      <h2 className="kiosk-pin-titulo">{TITULOS[pasos[paso]] || TITULOS.verificar}</h2>
      <div className="kiosk-pin-puntos">{puntos}</div>
      {error && <p className="kiosk-pin-error" data-testid="kiosk-pin-error">{error}</p>}
      {espera > 0 && (
        <p className="kiosk-pin-espera">Demasiados intentos. Espera {espera} s.</p>
      )}
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
          <BotonToque className="kiosk-pin-tecla kiosk-pin-aux" tactil={tactil} onActivar={onCancelar}>
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
        >
          Borrar
        </BotonToque>
      </div>
    </div>
  )
}
