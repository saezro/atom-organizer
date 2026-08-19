// Pantalla de emparejamiento por QR (kiosco de la Raspberry Pi).
//
// Este equipo (`status.pairing === true`, ver bridge.js) no tiene navegador
// propio para el login OAuth de escritorio: la sesion se vincula desde el
// movil escaneando un QR. El flujo es arrancar → pintar el QR → sondear cada
// pocos segundos → exito (sesion ya activa por dentro) o caducidad, con
// reintento manual.
import { useEffect, useRef, useState } from 'react'
import qrcode from 'qrcode-generator'
import { api, isServerMode } from './bridge.js'
import BotonToque from './pulsacion.jsx'

// Cada cuanto se pregunta al backend si el movil ya completo el vinculo. 2 s
// es suficientemente vivo para no sentirse colgado y suficientemente
// espaciado para no machacar el backend en un panel que puede quedarse
// minutos en esta pantalla.
const INTERVALO_POLL_MS = 2000

// Dibuja el QR como <path> de un solo SVG (un modulo oscuro = un cuadrado de
// 1x1 en el viewBox de la matriz). Nada de <img>/canvas: en el panel
// resistivo de 480x320 el vector sale nitido a cualquier escala, y a
// diferencia de canvas es testeable en jsdom sin dependencias extra.
function moduloPath(qr) {
  const n = qr.getModuleCount()
  let d = ''
  for (let fila = 0; fila < n; fila++) {
    for (let col = 0; col < n; col++) {
      if (qr.isDark(fila, col)) d += `M${col},${fila}h1v1h-1z`
    }
  }
  return { d, n }
}

function CodigoQr({ url }) {
  // typeNumber 0 = que la libreria elija el tamano minimo que quepa la URL.
  // Nivel 'L' (no 'M'): la URL de consentimiento de Google ronda los 300
  // caracteres y en el panel de 480x320 cada modulo cae por debajo de 4 px,
  // por debajo de lo que resuelve la camara de un movil a pulso. Bajar la
  // correccion de errores quita dos versiones de rejilla y engorda cada
  // modulo; el QR se lee de cerca y limpio, no impreso ni sucio, asi que la
  // redundancia extra no aportaba nada aqui.
  const qr = qrcode(0, 'L')
  qr.addData(url)
  qr.make()
  const { d, n } = moduloPath(qr)
  return (
    <svg
      className="pair-qr-svg"
      viewBox={`0 0 ${n} ${n}`}
      role="img"
      aria-label="Codigo QR para vincular este equipo"
    >
      <path d={d} fill="#0a0a0a" />
    </svg>
  )
}

export default function PairScreen({ onPaired }) {
  // 'cargando' | 'pendiente' | 'listo' | 'expirado' | 'error'
  const [estado, setEstado] = useState('cargando')
  const [pair, setPair] = useState(null) // {pair_id, url, expires_in}
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const pollRef = useRef(null)
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
    const id = setInterval(() => {
      api
        .cloudPairPoll(pair.pair_id)
        .then((r) => {
          if (!vivoRef.current) return
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
