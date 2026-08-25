// Banner NO bloqueante del kiosco para el estado `sin-conexion`.
//
// El cartel a pantalla completa (`AvisoSesion`) es descartable a propósito, y
// una vez cerrado no quedaba ni un rastro de que el dispositivo seguía sin
// hablar con ATOM Suite: la única pista era el resto de la UI, que se lee como
// "no emparejado" y manda al operario a buscar el QR sin necesidad. Este
// banner separa las dos cosas: la sesión SIGUE siendo válida (por eso el
// avatar no parpadea al caerse el wifi, ver `KioskScreen`), lo único que falta
// es red.
//
// No lleva botón de reintentar: el backoff de `_ciclo_comprobacion_arranque`
// ya repregunta solo (5s → 300s) y empuja un evento `session` en cada cambio
// de estado, así que el banner aparece y desaparece sin que nadie lo toque.

// Nube con una barra encima: distingue "no llego a ATOM Suite" del wifi
// tachado de `EstadoRed`, que informa de otra cosa (puede haber wifi y no
// haber nube, p.ej. si el DNS aún no resuelve al arrancar).
function IconoNubeTachada() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden="true">
      <path d="M17.5 18H7a4.5 4.5 0 01-.9-8.9A6 6 0 0117 7.5" opacity="0.45" />
      <path d="M3 3l18 18" />
    </svg>
  )
}

// `compacto` solo aprieta márgenes y padding, NO recorta el texto: en los
// pasos de organizar/subir el alto es oro (la lista de inspecciones vive de
// él), pero un "Sin conexión" a secas se lee como el wifi tachado de la barra
// y volvería a perderse el matiz de que se está reintentando solo.
export default function BannerConexion({ estado, pendientes = 0, compacto = false }) {
  if (estado !== 'sin-conexion') return null

  return (
    <div
      className={`kiosk-banner-conexion${compacto ? ' kiosk-banner-conexion-compacto' : ''}`}
      role="status"
      aria-live="polite"
      data-testid="kiosk-banner-conexion"
    >
      <IconoNubeTachada />
      <span className="kiosk-banner-conexion-texto">Sin conexión — reintentando</span>
      {pendientes > 0 && (
        <span className="kiosk-banner-conexion-cola">
          {pendientes} en cola
        </span>
      )}
    </div>
  )
}
