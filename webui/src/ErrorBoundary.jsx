import { Component } from 'react'

// Objetivo: el kiosco NUNCA debe quedarse en una pantalla rota sin salida.
// Un error de render no capturado desmonta TODO React y deja la Pi con una
// pantalla en blanco que nadie en campo sabe arreglar (no hay teclado ni
// consola). Con este limite, se ve un aviso minimo y se recarga sola a los
// pocos segundos, como si alguien hubiera pulsado F5.
const ESPERA_RECARGA_MS = 5000

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { fallo: false }
  }

  static getDerivedStateFromError() {
    return { fallo: true }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary: fallo no capturado en la UI', error, info)
    this._temporizador = setTimeout(() => window.location.reload(), ESPERA_RECARGA_MS)
  }

  componentWillUnmount() {
    clearTimeout(this._temporizador)
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
          <span>Algo falló, recargando…</span>
        </div>
      )
    }
    return this.props.children
  }
}
