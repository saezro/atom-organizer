import { useEffect, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import { api } from './bridge.js'

// App «Red» del kiosco: escanear WiFi y conectar. La Pi no tiene teclado
// físico, así que la pantalla de contraseña trae su propio teclado en
// pantalla (`PantallaPassword`), igual que `KioskTareas.jsx` pagina su lista
// en vez de fiarse de un scroll que el panel resistivo no siempre detecta.
const POR_PAGINA = 4

function IconoSenal({ senal }) {
  const activas = senal >= 80 ? 4 : senal >= 55 ? 3 : senal >= 30 ? 2 : senal > 0 ? 1 : 0
  const barras = [
    { x: 1, y: 16, h: 5 },
    { x: 7, y: 13, h: 8 },
    { x: 13, y: 10, h: 11 },
    { x: 19, y: 7, h: 14 },
  ]
  return (
    <svg viewBox="0 0 24 24" width="1.1em" height="1.1em" aria-hidden="true">
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

function IconoCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <polyline points="4 13 9 18 20 6" />
    </svg>
  )
}

function IconoRefrescar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 12a9 9 0 0115.3-6.3M21 12a9 9 0 01-15.3 6.3" />
      <path d="M18 3v4h-4M6 21v-4h4" />
    </svg>
  )
}

function IconoOjo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1.1em" height="1.1em" aria-hidden="true">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function IconoOjoTachado() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1.1em" height="1.1em" aria-hidden="true">
      <path d="M2 12s3.5-7 10-7c1.6 0 3 .35 4.2.9M22 12s-1.2 2.4-3.4 4.3M9.9 9.9a3 3 0 004.2 4.2" />
      <path d="M3 3l18 18" />
    </svg>
  )
}

function IconoMayus() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1.1em" height="1.1em" aria-hidden="true">
      <path d="M12 4l7 7h-4v7H9v-7H5z" />
    </svg>
  )
}

function IconoBorrar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="1.1em" height="1.1em" aria-hidden="true">
      <path d="M9 5h9a2 2 0 012 2v10a2 2 0 01-2 2H9l-6-7z" />
      <path d="M13 10l4 4M17 10l-4 4" />
    </svg>
  )
}

const FILAS_LETRAS = [
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
  ['z', 'x', 'c', 'v', 'b', 'n', 'm'],
]
const FILAS_NUMEROS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['-', '/', ':', ';', '(', ')', '&', '@', '"'],
  ['.', ',', '?', '!', "'"],
]

// Pantalla de contraseña: campo (oculto por defecto, con toggle «ver») +
// teclado en pantalla (letras/números, mayúsculas) porque la Pi no tiene
// teclado físico. `onConectar` recibe la contraseña ya escrita.
function PantallaPassword({ tactil, ssid, conectando, error, onVolver, onConectar }) {
  const [password, setPassword] = useState('')
  const [mostrar, setMostrar] = useState(false)
  const [mayus, setMayus] = useState(false)
  const [modo, setModo] = useState('letras')

  const filas = modo === 'letras' ? FILAS_LETRAS : FILAS_NUMEROS
  const tecla = (c) => (modo === 'letras' && mayus ? c.toUpperCase() : c)
  const escribir = (c) => setPassword((p) => p + tecla(c))
  const borrar = () => setPassword((p) => p.slice(0, -1))
  const espaciar = () => setPassword((p) => p + ' ')

  return (
    <div className="kiosk kiosk-red kiosk-red-password">
      <div className="kiosk-header kiosk-header-paso">
        <BotonToque className="kiosk-atras" tactil={tactil} onActivar={onVolver} disabled={conectando}>
          ← Atrás
        </BotonToque>
        <span className="kiosk-titulo">{ssid}</span>
      </div>

      <div className="kiosk-red-campo">
        <input
          className="kiosk-input"
          type={mostrar ? 'text' : 'password'}
          value={password}
          readOnly
          placeholder="Contraseña"
          data-testid="kiosk-red-password"
        />
        <BotonToque
          className="btn-ghost kiosk-btn kiosk-red-ver"
          tactil={tactil}
          onActivar={() => setMostrar((v) => !v)}
          data-testid="kiosk-red-ver"
          aria-label={mostrar ? 'Ocultar contraseña' : 'Ver contraseña'}
        >
          {mostrar ? <IconoOjoTachado /> : <IconoOjo />}
        </BotonToque>
      </div>

      <div className="kiosk-red-teclado">
        {filas.map((fila, i) => (
          <div className="kiosk-red-fila" key={i}>
            {fila.map((c) => (
              <BotonToque
                key={c}
                className="kiosk-teclado-tecla"
                tactil={tactil}
                data-testid={`kiosk-tecla-${c}`}
                onActivar={() => escribir(c)}
              >
                {tecla(c)}
              </BotonToque>
            ))}
          </div>
        ))}
        <div className="kiosk-red-fila kiosk-red-fila-control">
          <BotonToque
            className={'kiosk-teclado-tecla kiosk-teclado-especial' + (mayus ? ' activa' : '')}
            tactil={tactil}
            onActivar={() => setMayus((m) => !m)}
            disabled={modo !== 'letras'}
            data-testid="kiosk-tecla-mayus"
            aria-label="Mayúsculas"
          >
            <IconoMayus />
          </BotonToque>
          <BotonToque
            className="kiosk-teclado-tecla kiosk-teclado-especial"
            tactil={tactil}
            onActivar={() => setModo((m) => (m === 'letras' ? 'numeros' : 'letras'))}
            data-testid="kiosk-tecla-modo"
          >
            {modo === 'letras' ? '123' : 'ABC'}
          </BotonToque>
          <BotonToque
            className="kiosk-teclado-tecla kiosk-teclado-espacio"
            tactil={tactil}
            onActivar={espaciar}
            data-testid="kiosk-tecla-espacio"
          >
            Espacio
          </BotonToque>
          <BotonToque
            className="kiosk-teclado-tecla kiosk-teclado-especial"
            tactil={tactil}
            onActivar={borrar}
            data-testid="kiosk-tecla-borrar"
            aria-label="Borrar"
          >
            <IconoBorrar />
          </BotonToque>
        </div>
      </div>

      <BotonToque
        className="btn kiosk-btn kiosk-red-conectar"
        tactil={tactil}
        disabled={conectando || password.length === 0}
        data-testid="kiosk-red-conectar"
        onActivar={() => onConectar(password)}
      >
        {conectando ? 'Conectando…' : 'Conectar'}
      </BotonToque>

      {error && <span className="kiosk-red-error" data-testid="kiosk-red-password-error">{error}</span>}
    </div>
  )
}

export default function KioskRed({ tactil, onVolver }) {
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState('')
  const [redes, setRedes] = useState([])
  const [actual, setActual] = useState(null)
  const [pagina, setPagina] = useState(0)
  const [recargas, setRecargas] = useState(0)

  const [vista, setVista] = useState('lista')
  const [redSel, setRedSel] = useState(null)
  const [conectando, setConectando] = useState(false)
  const [errorConexion, setErrorConexion] = useState('')
  const [ssidConectando, setSsidConectando] = useState(null)

  useEffect(() => {
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
  }, [recargas])

  const refrescar = () => setRecargas((n) => n + 1)

  async function conectar(ssid, password) {
    setConectando(true)
    setSsidConectando(ssid)
    setErrorConexion('')
    try {
      const r = await api.redConectar(ssid, password)
      if (r?.ok) {
        setVista('lista')
        setRedSel(null)
        setSsidConectando(null)
        refrescar()
      } else {
        setErrorConexion(r?.error || 'No se pudo conectar.')
      }
    } catch (e) {
      setErrorConexion(String(e?.message || e))
    } finally {
      setConectando(false)
    }
  }

  function elegirRed(red) {
    if (conectando) return
    if (red.segura) {
      setRedSel(red)
      setVista('password')
    } else {
      conectar(red.ssid)
    }
  }

  if (vista === 'password' && redSel) {
    return (
      <PantallaPassword
        tactil={tactil}
        ssid={redSel.ssid}
        conectando={conectando}
        error={errorConexion}
        onVolver={() => {
          setVista('lista')
          setRedSel(null)
          setErrorConexion('')
          setSsidConectando(null)
        }}
        onConectar={(pwd) => conectar(redSel.ssid, pwd)}
      />
    )
  }

  const totalPaginas = Math.max(1, Math.ceil(redes.length / POR_PAGINA))
  const paginaSegura = Math.min(pagina, totalPaginas - 1)
  const inicio = paginaSegura * POR_PAGINA
  const visibles = redes.slice(inicio, inicio + POR_PAGINA)
  const conPaginacion = redes.length > POR_PAGINA

  return (
    <div className="kiosk kiosk-red">
      <div className="kiosk-header kiosk-header-paso">
        <BotonToque className="kiosk-atras" tactil={tactil} onActivar={onVolver}>
          ← Atrás
        </BotonToque>
        <span className="kiosk-titulo">Red</span>
        <BotonToque
          className="kiosk-actualizar-insp"
          tactil={tactil}
          onActivar={refrescar}
          disabled={cargando}
          data-testid="kiosk-red-refrescar"
          aria-label="Actualizar redes"
        >
          <IconoRefrescar />
        </BotonToque>
      </div>

      {actual && !cargando && !error && (
        <span className="kiosk-red-actual" data-testid="kiosk-red-actual">Conectado: {actual}</span>
      )}

      {cargando ? (
        <span className="kiosk-red-cargando" data-testid="kiosk-red-cargando">Buscando redes…</span>
      ) : error ? (
        <span className="kiosk-red-error" data-testid="kiosk-red-error">{error}</span>
      ) : redes.length === 0 ? (
        <span className="kiosk-red-vacio" data-testid="kiosk-red-vacio">No se han encontrado redes.</span>
      ) : (
        <>
          <div className="kiosk-red-lista">
            {visibles.map((red) => (
              <BotonToque
                key={red.ssid}
                className={'kiosk-red-item' + (red.activa ? ' activa' : '')}
                tactil={tactil}
                disabled={conectando}
                onActivar={() => elegirRed(red)}
                data-testid={`kiosk-red-${red.ssid}`}
              >
                <span className="kiosk-red-nombre">{red.ssid}</span>
                <span className="kiosk-red-senal">
                  <IconoSenal senal={red.senal} />
                  {red.senal}%
                </span>
                {red.segura && (
                  <span className="kiosk-red-candado" data-testid={`kiosk-red-candado-${red.ssid}`}>
                    <IconoCandado />
                  </span>
                )}
                {red.activa && (
                  <span className="kiosk-red-activa" data-testid={`kiosk-red-activa-${red.ssid}`}>
                    <IconoCheck /> Conectada
                  </span>
                )}
              </BotonToque>
            ))}
          </div>
          {conPaginacion && (
            <div className="kiosk-red-nav">
              <BotonToque
                className="kiosk-red-nav-btn"
                tactil={tactil}
                disabled={paginaSegura <= 0}
                onActivar={() => setPagina((p) => Math.max(0, p - 1))}
                data-testid="kiosk-red-arriba"
                aria-label="Página anterior de redes"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M6 15l6-6 6 6" />
                </svg>
              </BotonToque>
              <span className="kiosk-red-pagina" data-testid="kiosk-red-pagina">
                {paginaSegura + 1}/{totalPaginas}
              </span>
              <BotonToque
                className="kiosk-red-nav-btn"
                tactil={tactil}
                disabled={paginaSegura >= totalPaginas - 1}
                onActivar={() => setPagina((p) => Math.min(totalPaginas - 1, p + 1))}
                data-testid="kiosk-red-abajo"
                aria-label="Página siguiente de redes"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </BotonToque>
            </div>
          )}
        </>
      )}

      {ssidConectando && (
        <div className="kiosk-red-banner" data-testid="kiosk-red-banner">
          {conectando ? (
            <span>Conectando a {ssidConectando}…</span>
          ) : (
            <>
              <span className="kiosk-red-banner-error" data-testid="kiosk-red-banner-error">{errorConexion}</span>
              <BotonToque
                className="btn-ghost kiosk-btn"
                tactil={tactil}
                onActivar={() => { setErrorConexion(''); setSsidConectando(null) }}
                data-testid="kiosk-red-banner-cerrar"
              >
                Cerrar
              </BotonToque>
            </>
          )}
        </div>
      )}
    </div>
  )
}
