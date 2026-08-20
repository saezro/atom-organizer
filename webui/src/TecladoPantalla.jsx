import { useState } from 'react'
import BotonToque from './pulsacion.jsx'

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

// Teclado en pantalla (letras/números, mayúsculas) reutilizable para
// pantallas del kiosco sin teclado físico. Componente controlado: `valor` es
// el string actual y `onValor` recibe el string completo ya modificado.
export default function TecladoPantalla({ tactil, valor, onValor, className }) {
  const [mayus, setMayus] = useState(false)
  const [modo, setModo] = useState('letras')

  const filas = modo === 'letras' ? FILAS_LETRAS : FILAS_NUMEROS
  const tecla = (c) => (modo === 'letras' && mayus ? c.toUpperCase() : c)
  const escribir = (c) => onValor(valor + tecla(c))
  const borrar = () => onValor(valor.slice(0, -1))
  const espaciar = () => onValor(valor + ' ')

  return (
    <div className={'kiosk-teclado' + (className ? ` ${className}` : '')}>
      {filas.map((fila, i) => (
        <div className="kiosk-teclado-fila" key={i}>
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
      <div className="kiosk-teclado-fila kiosk-teclado-fila-control">
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
  )
}
