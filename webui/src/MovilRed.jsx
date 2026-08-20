import { useEffect, useState } from 'react'
import { api } from './bridge.js'

// Pantalla de configuracion desde el MOVIL (hotspot de la Pi). Es una vista
// aparte del kiosco a proposito: `KioskRed.jsx` esta dimensionada para la
// pantalla de 480x320 y el panel resistivo (paginacion de 4 en 4, teclado en
// pantalla, targets enormes), y servida tal cual en un telefono se ve como lo
// que es: una UI de otro dispositivo. Aqui hay scroll nativo, teclado nativo
// y una sola tarea: meter la Pi en una wifi.
//
// De momento SOLO eso. Cuando la Pi ya esta en la red normal se abrira el
// resto de opciones, pero eso exige ampliar `METODOS_REMOTOS` en
// `app_webview.py`, que es una decision de seguridad (el token viaja en la URL
// y el portal cautivo se lo regala a cualquiera conectado al AP).

function IconoSenal({ senal }) {
  const activas = senal >= 80 ? 4 : senal >= 55 ? 3 : senal >= 30 ? 2 : senal > 0 ? 1 : 0
  const barras = [
    { x: 1, y: 16, h: 5 },
    { x: 7, y: 13, h: 8 },
    { x: 13, y: 10, h: 11 },
    { x: 19, y: 7, h: 14 },
  ]
  return (
    <svg viewBox="0 0 24 24" width="1.25em" height="1.25em" aria-hidden="true">
      {barras.map((b, i) => (
        <rect key={i} x={b.x} y={b.y} width="3.5" height={b.h} rx="1" fill="currentColor" opacity={i < activas ? 1 : 0.25} />
      ))}
    </svg>
  )
}

function IconoCandado() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </svg>
  )
}

function IconoRefrescar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1.15em" height="1.15em" aria-hidden="true">
      <path d="M3 12a9 9 0 0115.3-6.3M21 12a9 9 0 01-15.3 6.3" />
      <path d="M18 3v4h-4M6 21v-4h4" />
    </svg>
  )
}

function IconoCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <polyline points="4 13 9 18 20 6" />
    </svg>
  )
}

function IconoOjo({ tachado }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1.25em" height="1.25em" aria-hidden="true">
      {tachado ? (
        <>
          <path d="M2 12s3.5-7 10-7c1.6 0 3 .35 4.2.9M22 12s-1.2 2.4-3.4 4.3M9.9 9.9a3 3 0 004.2 4.2" />
          <path d="M3 3l18 18" />
        </>
      ) : (
        <>
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
          <circle cx="12" cy="12" r="3" />
        </>
      )}
    </svg>
  )
}

function Marca({ sub }) {
  return (
    <header className="movil-head">
      <h1 className="movil-marca">
        <span className="atom">ATOM</span> <span className="org">ORGANIZER</span>
      </h1>
      <span className="movil-sub">{sub}</span>
    </header>
  )
}

export default function MovilRed() {
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState('')
  const [redes, setRedes] = useState([])
  const [actual, setActual] = useState(null)
  const [recargas, setRecargas] = useState(0)

  const [vista, setVista] = useState('lista')   // lista | password | listo
  const [redSel, setRedSel] = useState(null)
  const [password, setPassword] = useState('')
  const [mostrar, setMostrar] = useState(false)
  const [conectando, setConectando] = useState(false)
  const [errorConexion, setErrorConexion] = useState('')
  const [conectada, setConectada] = useState('')

  useEffect(() => {
    if (vista !== 'lista') return
    let vivo = true
    setCargando(true)
    setError('')
    api
      .redListar()
      .then((r) => {
        if (!vivo) return
        if (r?.ok) {
          setRedes(r.redes || [])
          setActual(r.actual ?? null)
        } else {
          setError(r?.error || 'No se pudieron listar las redes.')
        }
      })
      .catch((e) => { if (vivo) setError(String(e?.message || e)) })
      .finally(() => { if (vivo) setCargando(false) })
    return () => { vivo = false }
  }, [recargas, vista])

  const refrescar = () => setRecargas((n) => n + 1)

  // Al conectar con exito el backend APAGA el hotspot (app_webview.py,
  // `red_conectar`), asi que la respuesta puede no llegar nunca a este movil:
  // wlan0 ya esta en la otra red. Por eso un fallo de RED aqui no es un error
  // que mostrar, es la senal esperada de que la Pi se ha ido a su wifi. Solo
  // se pinta error cuando el backend contesta `ok:false`, que es el caso en
  // que la Pi sigue con el AP arriba y se puede reintentar.
  async function conectar(ssid, pwd) {
    setConectando(true)
    setErrorConexion('')
    try {
      const r = await api.redConectar(ssid, pwd)
      if (r?.ok) {
        setConectada(ssid)
        setVista('listo')
      } else {
        setErrorConexion(r?.error || 'No se pudo conectar.')
      }
    } catch {
      setConectada(ssid)
      setVista('listo')
    } finally {
      setConectando(false)
    }
  }

  function elegirRed(red) {
    if (conectando) return
    setErrorConexion('')
    // `guardada` = la Pi ya tiene el perfil con su clave; volver a pedirla
    // seria hacer teclear algo que el sistema ya sabe.
    if (red.segura && !red.guardada) {
      setRedSel(red)
      setPassword('')
      setMostrar(false)
      setVista('password')
    } else {
      conectar(red.ssid)
    }
  }

  if (vista === 'listo') {
    return (
      <div className="movil" data-testid="movil-listo">
        <Marca sub="Configuración de red" />
        <div className="movil-listo">
          <span className="movil-listo-icono"><IconoCheck /></span>
          <span className="movil-listo-titulo">Enviado</span>
          <p className="movil-listo-texto">
            La Pi se está conectando a <strong>{conectada}</strong>. La confirmación sale
            en <strong>la pantalla de la Pi</strong>: este móvil pierde el punto de acceso
            en cuanto la wifi entra, así que vuelve a tu red de siempre.
          </p>
        </div>
      </div>
    )
  }

  if (vista === 'password' && redSel) {
    return (
      <div className="movil" data-testid="movil-password">
        <Marca sub={redSel.ssid} />
        <form
          className="movil-form"
          onSubmit={(e) => { e.preventDefault(); conectar(redSel.ssid, password) }}
        >
          <label className="movil-label" htmlFor="movil-red-password">Contraseña de la red</label>
          <div className="movil-campo">
            <input
              id="movil-red-password"
              className="movil-input"
              type={mostrar ? 'text' : 'password'}
              value={password}
              autoFocus
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck="false"
              placeholder="••••••••"
              data-testid="movil-red-password"
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              className="movil-ver"
              data-testid="movil-red-ver"
              aria-label={mostrar ? 'Ocultar contraseña' : 'Ver contraseña'}
              onClick={() => setMostrar((v) => !v)}
            >
              <IconoOjo tachado={mostrar} />
            </button>
          </div>

          {errorConexion && (
            <span className="movil-error" data-testid="movil-red-password-error">{errorConexion}</span>
          )}

          <button
            type="submit"
            className="movil-btn movil-btn-primario"
            disabled={conectando || password.length === 0}
            data-testid="movil-red-conectar"
          >
            {conectando ? 'Conectando…' : 'Conectar'}
          </button>
          <button
            type="button"
            className="movil-btn"
            disabled={conectando}
            data-testid="movil-red-volver"
            onClick={() => { setVista('lista'); setRedSel(null); setErrorConexion('') }}
          >
            Cancelar
          </button>
        </form>
      </div>
    )
  }

  return (
    <div className="movil" data-testid="movil-red">
      <Marca sub="Configuración de red" />

      <div className="movil-estado" data-testid="movil-red-actual">
        <span className="movil-estado-label">Red de la Pi</span>
        <span className={'movil-estado-valor' + (actual ? ' ok' : '')}>
          {actual || 'Sin conexión'}
        </span>
      </div>

      <div className="movil-seccion">
        <span className="movil-seccion-titulo">Redes disponibles</span>
        <button
          type="button"
          className="movil-refrescar"
          onClick={refrescar}
          disabled={cargando || conectando}
          data-testid="movil-red-refrescar"
          aria-label="Buscar redes de nuevo"
        >
          <IconoRefrescar />
        </button>
      </div>

      {errorConexion && (
        <span className="movil-error" data-testid="movil-red-error-conexion">{errorConexion}</span>
      )}

      {cargando ? (
        <span className="movil-nota" data-testid="movil-red-cargando">Buscando redes…</span>
      ) : error ? (
        <span className="movil-error" data-testid="movil-red-error">{error}</span>
      ) : redes.length === 0 ? (
        <span className="movil-nota" data-testid="movil-red-vacio">No se han encontrado redes.</span>
      ) : (
        <ul className="movil-lista">
          {redes.map((red) => (
            <li key={red.ssid}>
              <button
                type="button"
                className={'movil-item' + (red.activa ? ' activa' : '')}
                disabled={conectando}
                onClick={() => elegirRed(red)}
                data-testid={`movil-red-${red.ssid}`}
              >
                <span className="movil-item-senal"><IconoSenal senal={red.senal} /></span>
                <span className="movil-item-texto">
                  <span className="movil-item-nombre">{red.ssid}</span>
                  <span className="movil-item-meta">
                    {red.activa ? 'Conectada' : red.guardada ? 'Guardada' : red.segura ? 'Protegida' : 'Abierta'}
                    {' · '}{red.senal}%
                  </span>
                </span>
                {red.segura && (
                  <span className="movil-item-candado" data-testid={`movil-red-candado-${red.ssid}`}>
                    <IconoCandado />
                  </span>
                )}
                {red.activa && (
                  <span className="movil-item-check" data-testid={`movil-red-activa-${red.ssid}`}>
                    <IconoCheck />
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {conectando && (
        <span className="movil-nota" data-testid="movil-red-conectando">Conectando…</span>
      )}
    </div>
  )
}
