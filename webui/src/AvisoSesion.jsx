// Cartel a pantalla completa para la Raspberry Pi (480x320). Aparece al
// arrancar y ante cualquier fallo de credencial. Es descartable a propósito:
// organizar es 100% local y subir queda en cola, así que el operario tiene
// que poder seguir trabajando sin dispositivo emparejado.
const TEXTOS = {
  'sin-credencial': {
    titulo: 'SESIÓN CERRADA',
    cuerpo: 'Este dispositivo ya no está autorizado en ATOM Suite.',
    ayuda: 'Vuelve a emparejarlo con el QR.',
  },
  'sin-conexion': {
    titulo: 'SIN CONEXIÓN',
    cuerpo: 'No se ha podido hablar con ATOM Suite.',
    ayuda: 'Comprueba la red. La sesión puede seguir siendo válida.',
  },
}

function IconoAviso() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
  )
}

export default function AvisoSesion({ estado, mensaje, pendientes = 0, onEmparejar, onCerrar }) {
  const texto = TEXTOS[estado]
  if (!texto) return null

  return (
    <div className="aviso-sesion" role="alertdialog" aria-label={texto.titulo}>
      <div className="aviso-sesion-caja">
        <div className="aviso-sesion-icono"><IconoAviso /></div>
        <h1 className="aviso-sesion-titulo">{texto.titulo}</h1>
        <p className="aviso-sesion-cuerpo">{mensaje || texto.cuerpo}</p>
        <p className="aviso-sesion-ayuda">{texto.ayuda}</p>
        {pendientes > 0 && (
          <p className="aviso-sesion-cola">
            {pendientes} subida{pendientes === 1 ? '' : 's'} en cola, saldrá{pendientes === 1 ? '' : 'n'} al recuperar la sesión.
          </p>
        )}
        <div className="aviso-sesion-acciones">
          {estado === 'sin-credencial' && onEmparejar && (
            <button type="button" className="aviso-sesion-btn aviso-sesion-btn-primario" onClick={onEmparejar}>
              Emparejar con QR
            </button>
          )}
          {onCerrar && (
            <button type="button" className="aviso-sesion-btn" onClick={onCerrar}>
              Cerrar
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
