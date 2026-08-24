// Pantalla de emparejamiento por QR (kiosco de la Raspberry Pi).
//
// Este equipo (`status.pairing === true`, ver bridge.js) no tiene navegador
// propio para el login OAuth de escritorio: la sesion se vincula desde el
// movil escaneando un QR. El flujo es arrancar → pintar el QR → sondear cada
// pocos segundos → exito (sesion ya activa por dentro) o caducidad, con
// reintento manual.
import { useEffect, useRef, useState } from 'react'
import { api, isServerMode } from './bridge.js'
import BotonToque from './pulsacion.jsx'
import CodigoQr from './CodigoQr.jsx'

// Cada cuanto se pregunta al backend si el movil ya completo el vinculo. 2 s
// es suficientemente vivo para no sentirse colgado y suficientemente
// espaciado para no machacar el backend en un panel que puede quedarse
// minutos en esta pantalla.
const INTERVALO_POLL_MS = 2000

// Fallos de red seguidos que se toleran antes de dar el vinculo por perdido.
// El kiosco vive de wifi: un blip de un par de segundos no puede obligar a
// reemparejar la Pi desde cero (implica volver al movil con el QR). Con 2 s de
// intervalo esto son ~20 s de corte aguantados; a partir de ahi si es un fallo
// de verdad y merece la pantalla de error.
const FALLOS_POLL_TOLERADOS = 10

export default function PairScreen({ onPaired }) {
  // 'cargando' | 'pendiente' | 'listo' | 'expirado' | 'error'
  const [estado, setEstado] = useState('cargando')
  const [pair, setPair] = useState(null) // {pair_id, url, expires_in}
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const pollRef = useRef(null)
  const fallosRef = useRef(0)
  const vivoRef = useRef(true)
  const tactil = isServerMode()

  function pararPoll() {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  // Arranca (o rearranca, desde "Reintentar") un vinculo nuevo. Cada llamada
  // pide un `pair_id` fresco: reusar uno caducado no tiene sentido, el
  // backend ya lo habra descartado.
  function iniciar() {
    pararPoll()
    setEstado('cargando')
    setError('')
    setEmail('')
    api
      .cloudPairStart()
      .then((r) => {
        if (!vivoRef.current) return
        if (!r || r.ok === false) {
          setEstado('error')
          setError(r?.error || 'No se pudo generar el codigo.')
          return
        }
        setPair(r)
        setEstado('pendiente')
      })
      .catch((e) => {
        if (!vivoRef.current) return
        setEstado('error')
        setError(String(e))
      })
  }

  useEffect(() => {
    vivoRef.current = true
    iniciar()
    return () => {
      vivoRef.current = false
      pararPoll()
    }
    // Solo al montar: "Reintentar" llama a `iniciar` directamente.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Sondeo mientras el vinculo sigue pendiente. Vive en su propio efecto
  // (no dentro de `iniciar`) para que se limpie solo con cada cambio de
  // `estado`/`pair`, sin duplicar intervalos si el componente rerenderiza.
  useEffect(() => {
    if (estado !== 'pendiente' || !pair?.pair_id) return undefined
    fallosRef.current = 0
    const id = setInterval(() => {
      api
        .cloudPairPoll(pair.pair_id)
        .then((r) => {
          if (!vivoRef.current) return
          fallosRef.current = 0
          if (r?.estado === 'listo') {
            pararPoll()
            setEmail(r.email || '')
            setEstado('listo')
            onPaired?.()
          } else if (r?.estado === 'expirado') {
            pararPoll()
            setEstado('expirado')
          }
          // 'pendiente': no hay nada que hacer, el intervalo sigue.
        })
        .catch((e) => {
          if (!vivoRef.current) return
          // Un fallo aislado casi siempre es la wifi, no el vinculo: se
          // reintenta en el siguiente tick y solo se rinde tras varios
          // seguidos. El contador se resetea con cada respuesta buena.
          fallosRef.current += 1
          if (fallosRef.current < FALLOS_POLL_TOLERADOS) return
          pararPoll()
          setEstado('error')
          setError(String(e))
        })
    }, INTERVALO_POLL_MS)
    pollRef.current = id
    return () => clearInterval(id)
  }, [estado, pair, onPaired])

  return (
    <div className="pair" data-testid="pair-screen">
      <div className="pair-qr">
        {estado === 'pendiente' && pair?.url ? (
          <CodigoQr url={pair.url} />
        ) : (
          <span className="pair-qr-vacio" data-testid="pair-qr-vacio" />
        )}
      </div>

      {estado === 'cargando' && <p className="pair-texto">Generando código…</p>}

      {estado === 'pendiente' && (
        <p className="pair-texto">Escanea con el móvil para vincular este equipo.</p>
      )}

      {estado === 'listo' && (
        <p className="pair-texto pair-ok">Vinculado{email ? ` como ${email}` : ''}.</p>
      )}

      {(estado === 'expirado' || estado === 'error') && (
        <>
          <p className="pair-texto pair-warn">
            {estado === 'expirado' ? 'El código ha caducado.' : error || 'Algo ha ido mal.'}
          </p>
          <BotonToque
            className="btn-run pair-reintentar"
            tactil={tactil}
            onActivar={iniciar}
          >
            Reintentar
          </BotonToque>
        </>
      )}
    </div>
  )
}
