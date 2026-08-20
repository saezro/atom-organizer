import BotonToque from './pulsacion.jsx'

// Paginador único del kiosco: columna vertical de ▲/▼ a la DERECHA del
// contenido, con el indicador «pág/total» en medio. Se pinta SIEMPRE (las
// flechas se deshabilitan si no hay a dónde ir) para que el bloque de al lado
// mida lo mismo tenga una página o diez.
//
// En 480x320 no hay scroll cómodo con el dedo sobre un panel resistivo: todas
// las listas del kiosco paginan, y todas lo hacen con este componente (antes
// había cinco copias del mismo bloque, unas en fila y otras en columna).
export default function Paginador({
  pagina,
  totalPaginas,
  onPagina,
  tactil,
  disabled,
  testidPrefijo,
  contexto = '',
}) {
  const sufijo = contexto ? ' de ' + contexto : ''
  return (
    <div className="kiosk-pag">
      <BotonToque
        className="kiosk-pag-btn"
        tactil={tactil}
        disabled={disabled || pagina <= 0}
        onActivar={() => onPagina(Math.max(0, pagina - 1))}
        data-testid={testidPrefijo + 'arriba'}
        aria-label={'Página anterior' + sufijo}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6 15l6-6 6 6" />
        </svg>
      </BotonToque>
      <span className="kiosk-pag-num" data-testid={testidPrefijo + 'pagina'}>
        {pagina + 1}/{totalPaginas}
      </span>
      <BotonToque
        className="kiosk-pag-btn"
        tactil={tactil}
        disabled={disabled || pagina >= totalPaginas - 1}
        onActivar={() => onPagina(Math.min(totalPaginas - 1, pagina + 1))}
        data-testid={testidPrefijo + 'abajo'}
        aria-label={'Página siguiente' + sufijo}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </BotonToque>
    </div>
  )
}
