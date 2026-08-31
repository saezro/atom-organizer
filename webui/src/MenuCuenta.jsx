import { useEffect, useRef, useState } from 'react'

// Avatar de la esquina superior derecha (mismo papel que en el kiosco, ver
// `KioskScreen.jsx`) con el menú de la cuenta: Ajustes y Cerrar sesión.
// Se cierra con Escape y con clic fuera; sin `blur` ni animaciones infinitas,
// que en el WebView sin GPU lagean.
export default function MenuCuenta({ cuenta, invitado, onAjustes, onSalir }) {
  const [abierto, setAbierto] = useState(false)
  const cajaRef = useRef(null)

  useEffect(() => {
    if (!abierto) return undefined
    const alTeclado = (e) => {
      if (e.key === 'Escape') setAbierto(false)
    }
    const alClic = (e) => {
      if (cajaRef.current && !cajaRef.current.contains(e.target)) setAbierto(false)
    }
    document.addEventListener('keydown', alTeclado)
    document.addEventListener('mousedown', alClic)
    return () => {
      document.removeEventListener('keydown', alTeclado)
      document.removeEventListener('mousedown', alClic)
    }
  }, [abierto])

  const email = cuenta?.email || ''
  const inicial = (cuenta?.nombre || email || '?').trim().charAt(0).toUpperCase()
  const cabecera = invitado ? 'Sin cuenta' : email || 'Cuenta de Google'

  return (
    <div className="cuenta" ref={cajaRef}>
      <button
        type="button"
        className="cuenta-avatar"
        onClick={() => setAbierto((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={abierto}
        aria-label="Cuenta"
        title={cabecera}
        data-testid="cuenta-avatar"
      >
        {!invitado && cuenta?.picture ? (
          <img src={cuenta.picture} alt="" className="cuenta-avatar-img" />
        ) : (
          <span className="cuenta-avatar-inicial">{invitado ? '·' : inicial}</span>
        )}
      </button>
      {abierto && (
        <div className="cuenta-menu" role="menu" data-testid="cuenta-menu">
          <p className="cuenta-menu-cab" title={cabecera}>{cabecera}</p>
          <button
            type="button"
            role="menuitem"
            className="cuenta-menu-item"
            onClick={() => {
              setAbierto(false)
              onAjustes()
            }}
          >
            Ajustes
          </button>
          <button
            type="button"
            role="menuitem"
            className="cuenta-menu-item"
            onClick={() => {
              setAbierto(false)
              onSalir()
            }}
          >
            Cerrar sesión
          </button>
        </div>
      )}
    </div>
  )
}
