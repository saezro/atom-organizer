import { useEffect, useState } from 'react'
import BotonToque from './pulsacion.jsx'
import CodigoQr from './CodigoQr.jsx'
import { api, esRemoto } from './bridge.js'

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
//
// Vista desde el móvil (`esRemoto()`) es la excepción: ahí SÍ hay teclado
// físico/táctil nativo del propio móvil, así que el teclado en pantalla sobra
// (y en una pantalla de móvil normal el input readOnly ni siquiera lo abre).
// El campo pasa a ser un input controlado normal, con autoFocus para que el
// teclado del móvil salga solo al entrar en esta pantalla.
function PantallaPassword({ tactil, ssid, conectando, error, onVolver, onConectar }) {
  const [password, setPassword] = useState('')
  const [mostrar, setMostrar] = useState(false)
  const [mayus, setMayus] = useState(false)
  const [modo, setModo] = useState('letras')
  const remoto = esRemoto()

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
          placeholder="Contraseña"
          data-testid="kiosk-red-password"
          {...(remoto
            ? { onChange: (e) => setPassword(e.target.value), autoFocus: true }
            : { readOnly: true })}
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

      {!remoto && (
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
      )}

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

  // Punto de acceso para conectar desde el móvil (ver bloque `vista === 'ap'`
  // más abajo): datos del hotspot ({ssid, password, ip, token}), el paso del
  // QR mostrado (1. wifi / 2. url — nunca los dos a la vez, la pantalla de
  // 480x320 no da para ello) y el estado de arranque/error de `redApActivar`.
  const [apInfo, setApInfo] = useState(null)
  const [apIntento, setApIntento] = useState('')
  const [apCargando, setApCargando] = useState(false)
  const [apError, setApError] = useState('')
  const [paso, setPaso] = useState('wifi')
  // SSID al que el MOVIL ha conectado la Pi. La confirmacion tiene que darla
  // esta pantalla (la de la Pi) y no el movil: quien mira la Pi es quien
  // necesita saber que ya esta en la red, y ademas el movil pierde la
  // conexion en cuanto el AP cae, asi que su "listo" es un acto de fe.
  const [apConectado, setApConectado] = useState('')

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

  // Mientras se ensena el QR, la Pi vigila su propio hotspot. `red_conectar`
  // (app_webview.py) apaga el AP en cuanto la wifi entra, asi que ver el AP
  // caido es la senal de que el flujo del movil ha terminado. Que haya red o
  // no lo dice `red_listar().actual`: el autoapagado a los 600 s tambien tumba
  // el AP, y ahi no hay nada que celebrar.
  useEffect(() => {
    if (vista !== 'ap' || !apInfo || apConectado) return
    let vivo = true
    let caidas = 0
    const id = setInterval(async () => {
      try {
        const est = await api.redApEstado()
        if (!vivo || !est?.ok) return
        if (est.activo) {
          caidas = 0
          // El movil ya ha pulsado "Conectar": nmcli tarda hasta 60 s, asi que
          // la Pi lo dice en cuanto empieza en vez de parecer congelada.
          setApIntento(est.intento || '')
          return
        }
        // AP caido: la wifi puede tardar en asociarse y coger IP, asi que
        // `actual` puede venir vacia unos segundos. Damos ~30 s de gracia
        // antes de concluir que fue el autoapagado y no una conexion.
        caidas += 1
        const lista = await api.redListar()
        if (!vivo) return
        if (lista?.ok && lista.actual) {
          clearInterval(id)
          setApIntento('')
          setApInfo(null)
          setApConectado(lista.actual)
          setRedes(lista.redes || [])
          setActual(lista.actual)
        } else if (caidas >= 25) {
          setApIntento('')
          // AP caido sin red: autoapagado. Devolver a la lista es mas util
          // que dejar un QR que ya no sirve.
          clearInterval(id)
          setApInfo(null)
          setVista('lista')
          refrescar()
        }
      } catch {
        // Un fallo suelto del poll no significa nada: se reintenta al tick
        // siguiente y, si el AP sigue arriba, el QR sigue siendo valido.
      }
    }, 1200)
    return () => { vivo = false; clearInterval(id) }
  }, [vista, apInfo, apConectado])

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
    // `guardada` = la Pi ya tiene el perfil de esa red con su clave, asi que
    // pedirla otra vez seria hacer teclear algo que el sistema ya sabe. Si el
    // perfil resultase invalido, `conectar` cae en el error de siempre y se
    // puede reintentar eligiendola de nuevo.
    if (red.segura && !red.guardada) {
      setRedSel(red)
      setVista('password')
    } else {
      conectar(red.ssid)
    }
  }

  // Levanta el hotspot para que el usuario configure la wifi desde el móvil
  // (teclado en pantalla inviable en 480x320). Se lanza al entrar en la
  // vista, no al pulsar un botón dentro de ella, para no añadir un paso extra
  // solo para arrancar algo que ya se quiere ver.
  async function abrirAp() {
    setVista('ap')
    setPaso('wifi')
    setApError('')
    setApConectado('')
    setApCargando(true)
    try {
      const r = await api.redApActivar()
      if (r?.ok) {
        setApInfo(r)
      } else {
        setApError(r?.error || 'No se pudo levantar el punto de acceso.')
      }
    } catch (e) {
      setApError(String(e?.message || e))
    } finally {
      setApCargando(false)
    }
  }

  // Apaga el hotspot y restaura la wifi previa (lo hace el backend). Se llama
  // tanto al terminar el flujo a propósito como al salir por Atrás: dejar el
  // AP encendido sin que el usuario lo sepa es peor que cerrarlo de más.
  async function cerrarAp() {
    setApInfo(null)
    setApError('')
    setApConectado('')
    try {
      await api.redApDesactivar()
    } catch {
      // El AP tiene autoapagado a los 600s en el backend; si desactivar falla
      // aquí no dejamos al usuario colgado en una pantalla sin salida.
    }
    setVista('lista')
    refrescar()
  }

  // Confirmacion EN LA PI: el movil ya la metio en una red. Se queda aqui
  // hasta que alguien la lea (nada de volver solo a la lista), con una unica
  // salida al menu.
  if (vista === 'ap' && apIntento && !apConectado) {
    return (
      <div className="kiosk kiosk-red kiosk-red-ap kiosk-red-ap-listo" data-testid="kiosk-red-ap-conectando">
        <div className="kiosk-red-ap-ok">
          <span className="kiosk-red-ap-ok-icono kiosk-red-ap-ok-girando"><IconoRefrescar /></span>
          <span className="kiosk-red-ap-ok-titulo">Conectando a {apIntento}…</span>
          <span className="kiosk-red-ap-ok-sub">Puede tardar unos segundos.</span>
        </div>
      </div>
    )
  }

  if (vista === 'ap' && apConectado) {
    return (
      <div className="kiosk kiosk-red kiosk-red-ap kiosk-red-ap-listo" data-testid="kiosk-red-ap-listo">
        <div className="kiosk-red-ap-ok">
          <span className="kiosk-red-ap-ok-icono"><IconoCheck /></span>
          <span className="kiosk-red-ap-ok-titulo">Conectado a {apConectado}</span>
          <span className="kiosk-red-ap-ok-sub">El punto de acceso se ha apagado.</span>
        </div>
        <BotonToque
          className="btn kiosk-btn kiosk-red-ap-cerrar"
          tactil={tactil}
          onActivar={() => { setApConectado(''); setVista('lista'); onVolver() }}
          data-testid="kiosk-red-ap-listo-volver"
        >
          Volver al menú
        </BotonToque>
      </div>
    )
  }

  if (vista === 'ap') {
    return (
      <div className="kiosk kiosk-red kiosk-red-ap">
        <div className="kiosk-header kiosk-header-paso">
          <BotonToque className="kiosk-atras" tactil={tactil} onActivar={cerrarAp}>
            ← Atrás
          </BotonToque>
          <span className="kiosk-titulo">Conectar desde el móvil</span>
        </div>

        {apCargando ? (
          <span className="kiosk-red-cargando" data-testid="kiosk-red-ap-cargando">
            Levantando punto de acceso…
          </span>
        ) : apError ? (
          <span className="kiosk-red-error" data-testid="kiosk-red-ap-error">{apError}</span>
        ) : apInfo && paso === 'wifi' ? (
          <div className="kiosk-red-ap-paso">
            <span className="kiosk-red-ap-titulo">1. Conecta el móvil</span>
            <div className="kiosk-red-ap-qr" data-testid="kiosk-red-ap-qr-wifi">
              <div className="kiosk-red-ap-qr-marco">
                <CodigoQr url={`WIFI:S:${apInfo.ssid};T:WPA;P:${apInfo.password};;`} />
              </div>
            </div>
            <span className="kiosk-red-ap-datos">
              {apInfo.ssid} · {apInfo.password}
            </span>
            <BotonToque
              className="btn kiosk-btn"
              tactil={tactil}
              onActivar={() => setPaso('url')}
              data-testid="kiosk-red-ap-siguiente"
            >
              Ya estoy conectado →
            </BotonToque>
          </div>
        ) : apInfo && paso === 'url' ? (
          <div className="kiosk-red-ap-paso">
            <span className="kiosk-red-ap-titulo">2. Abre esta página</span>
            <div className="kiosk-red-ap-qr" data-testid="kiosk-red-ap-qr-url">
              <div className="kiosk-red-ap-qr-marco">
                <CodigoQr url={`http://${apInfo.ip}:8765/?t=${apInfo.token}`} />
              </div>
            </div>
            <span className="kiosk-red-ap-datos">
              http://{apInfo.ip}:8765/?t={apInfo.token}
            </span>
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              onActivar={() => setPaso('wifi')}
              data-testid="kiosk-red-ap-volver"
            >
              ← Volver
            </BotonToque>
          </div>
        ) : null}

        <BotonToque
          className="btn-ghost kiosk-btn kiosk-red-ap-cerrar"
          tactil={tactil}
          onActivar={cerrarAp}
          data-testid="kiosk-red-ap-cerrar"
        >
          {apInfo ? 'Terminar' : 'Cancelar'}
        </BotonToque>
      </div>
    )
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

      {!esRemoto() && (
        <BotonToque
          className="btn-ghost kiosk-btn kiosk-red-ap-abrir"
          tactil={tactil}
          onActivar={abrirAp}
          data-testid="kiosk-red-ap-abrir"
        >
          Conectar desde el móvil
        </BotonToque>
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
