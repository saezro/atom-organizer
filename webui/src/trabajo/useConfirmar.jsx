import { useCallback, useRef, useState } from 'react'

// Confirmación propia en React. `window.confirm` no se muestra en QtWebEngine
// (pywebview gui="qt") y devuelve false en silencio, así que la acción se
// perdía sin avisar. Uso: `const [confirmar, dialogo] = useConfirmar()`; render
// `{dialogo}` en el componente y `await confirmar('texto')` → true/false.
// Reutiliza las clases del modal de progreso (`pm-*`) y `btn-ghost`/`btn-run`.
export default function useConfirmar() {
  const [texto, setTexto] = useState(null)
  const resolver = useRef(null)

  const confirmar = useCallback(
    (mensaje) =>
      new Promise((resolve) => {
        resolver.current = resolve
        setTexto(mensaje)
      }),
    []
  )

  function responder(valor) {
    const r = resolver.current
    resolver.current = null
    setTexto(null)
    if (r) r(valor)
  }

  const dialogo =
    texto == null ? null : (
      <div className="pm-overlay" role="dialog" aria-modal="true">
        <div className="pm-card">
          <p className="pm-title">{texto}</p>
          <div className="pm-actions">
            <button type="button" className="btn-ghost" onClick={() => responder(false)}>
              No
            </button>
            <button type="button" className="btn-run" onClick={() => responder(true)}>
              Sí
            </button>
          </div>
        </div>
      </div>
    )

  return [confirmar, dialogo]
}
