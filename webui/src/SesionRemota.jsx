// Overlay bloqueante: aparece cuando OTRA sesión (remota, p.ej. un benchmark
// lanzado por SSH) está usando la máquina. A diferencia de AvisoSesion, este
// NO se puede cerrar ni descartar: mientras la sesión remota siga activa, el
// operador no puede seguir trabajando en esta misma máquina (evita que los
// dos procesos se solapen). Reusa el mismo estilo/estructura que AvisoSesion.
function IconoCandado() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  )
}

function formatearDesde(desde) {
  if (!desde) return null
  const fecha = new Date(desde)
  if (Number.isNaN(fecha.getTime())) return desde
  return fecha.toLocaleString()
}

export default function SesionRemota({ motivo, desde }) {
  const desdeTexto = formatearDesde(desde)

  return (
    <div className="aviso-sesion" role="alertdialog" aria-label="Máquina ocupada">
      <div className="aviso-sesion-caja">
        <div className="aviso-sesion-icono"><IconoCandado /></div>
        <h1 className="aviso-sesion-titulo">Máquina ocupada</h1>
        {motivo && <p className="aviso-sesion-cuerpo">{motivo}</p>}
        {desdeTexto && <p className="aviso-sesion-ayuda">Desde: {desdeTexto}</p>}
        <p className="aviso-sesion-ayuda">
          Otra sesión está usando este equipo. Espera a que termine para poder trabajar aquí.
        </p>
      </div>
    </div>
  )
}
