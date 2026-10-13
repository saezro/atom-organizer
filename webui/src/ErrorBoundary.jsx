import { Component } from 'react'
import { logFront } from './bridge.js'

// Objetivo: el kiosco NUNCA debe quedarse en una pantalla rota sin salida.
// Un error de render no capturado desmonta TODO React y deja la Pi con una
// pantalla en blanco que nadie en campo sabe arreglar (no hay teclado ni
// consola). Con este limite, se ve un aviso minimo y se recarga sola a los
// pocos segundos, como si alguien hubiera pulsado F5.
const ESPERA_RECARGA_MS = 5000
// Tope de recargas automaticas seguidas: evita el bucle de recargas si el
// error se repite en cada arranque.
const MAX_RECARGAS = 2
const CLAVE_RECARGAS = 'atom-eb-recargas'
const SANO_MS = 30000

const PREFIJO_NAME = 'atom-eb:'

// Contador en sessionStorage y, de respaldo, en window.name (sobrevive a
// reload). Devuelve {n, fiable}: fiable=false si ningun almacen persiste.
function leerRecargas() {
  try {
    const v = window.sessionStorage.getItem(CLAVE_RECARGAS)
    if (v !== null) return { n: Number(v) || 0, fiable: true }
  } catch {
    // cae a window.name
  }
  try {
    const nombre = String(window.name || '')
    const m = nombre.match(/(?:^|\|)atom-eb:(\d+)(?:\||$)/)
    return { n: m ? Number(m[1]) || 0 : 0, fiable: true }
  } catch {
    return { n: 0, fiable: false }
  }
}

function escribirRecargas(n) {
  let ok = false
  try {
    if (n > 0) window.sessionStorage.setItem(CLAVE_RECARGAS, String(n))
    else window.sessionStorage.removeItem(CLAVE_RECARGAS)
    ok = true
  } catch {
    // sin sessionStorage: se usa window.name
  }
  try {
    const resto = String(window.name || '')
      .split('|')
      .filter((p) => p && !p.startsWith(PREFIJO_NAME))
    if (n > 0) resto.push(`${PREFIJO_NAME}${n}`)
    window.name = resto.join('|')
    if (n > 0 && window.name.indexOf(`${PREFIJO_NAME}${n}`) === -1) return ok
    ok = true
  } catch {
    // ni window.name
  }
  return ok
}

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { fallo: false, mensaje: '', agotado: false }
  }

  componentDidMount() {
    // La app lleva un rato montada sin error: no acumular entre sesiones sanas.
    this._temporizadorSano = setTimeout(() => {
      if (!this.state.fallo) escribirRecargas(0)
    }, SANO_MS)
  }

  reintentar = () => {
    escribirRecargas(0)
    window.location.reload()
  }

  static getDerivedStateFromError() {
    return { fallo: true }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary: fallo no capturado en la UI', error, info)
    logFront(
      'error',
      `ErrorBoundary: ${error?.message || error}`,
      `${error?.stack || ''}\ncomponentStack:${info?.componentStack || ''}`,
    )
    clearTimeout(this._temporizadorSano)
    const { n, fiable } = leerRecargas()
    const mensaje = String(error?.message || error || '')
    // Si el contador no persiste en ningun sitio, recargar solo podria
    // ciclar sin fin: se muestra el error con «Reintentar».
    if (n >= MAX_RECARGAS || !fiable || !escribirRecargas(n + 1)) {
      this.setState({ mensaje, agotado: true })
      return
    }
    this.setState({ mensaje, agotado: false })
    this._temporizador = setTimeout(() => window.location.reload(), ESPERA_RECARGA_MS)
  }

  componentWillUnmount() {
    clearTimeout(this._temporizador)
    clearTimeout(this._temporizadorSano)
  }

  render() {
    if (this.state.fallo) {
      return (
        <div
          data-testid="error-boundary"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100vh',
            width: '100vw',
            background: '#0a0a0a',
            color: '#fff',
            fontFamily: 'sans-serif',
            textAlign: 'center',
            gap: '0.5rem',
          }}
        >
          {this.state.agotado ? (
            <>
              <span>Algo falló y no se pudo recuperar.</span>
              {this.state.mensaje && <span>{this.state.mensaje}</span>}
              <button type="button" onClick={this.reintentar}>
                Reintentar
              </button>
            </>
          ) : (
            <span>Algo falló, recargando…</span>
          )}
        </div>
      )
    }
    return this.props.children
  }
}
