// Pantalla previa a la de trabajo: se pinta cuando `useSesion().entrado` es
// falso. Solo dos caminos — cuenta Google o "sin cuenta" (invitado) — sin
// más chrome que el lockup de marca, igual que el header de `App.jsx`.
export default function PantallaEntrada({ onGoogle, onInvitado, cargando, error }) {
  return (
    <div className="entrada" data-testid="pantalla-entrada">
      <div className="entrada-marca brand">
        <h1>
          <span className="atom">ATOM</span> <span className="org">ORGANIZER</span>
        </h1>
      </div>
      <div className="entrada-acciones">
        <button
          type="button"
          className="btn-run entrada-btn"
          onClick={onGoogle}
          disabled={cargando}
        >
          Entrar con Google
        </button>
        {/* Nunca se deshabilita: es la salida cuando el login de Google se
            queda a medias (ventana de consentimiento cerrada, red caída).
            Sin cuenta se puede trabajar en local igual. */}
        <button
          type="button"
          className="btn-ghost entrada-btn"
          onClick={onInvitado}
        >
          Entrar sin cuenta
        </button>
      </div>
      {error && <p className="entrada-error">{error}</p>}
    </div>
  )
}
