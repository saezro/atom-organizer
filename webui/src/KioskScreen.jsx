// Pantalla "kiosco" para la Raspberry Pi con pantalla táctil de 480x320 px,
// que sustituye a la UI normal cuando el Organizer corre en modo servidor.
// Debe caber entera sin scroll y con botones grandes para dedo.

// Deriva la ruta de destino a partir de la carpeta de origen, añadiendo el
// sufijo "_ORGANIZADO". Función pura: sin efectos, sin acceso a props/estado.
export function derivarDestino(carpeta) {
  if (!carpeta) return ''
  const sinBarraFinal = carpeta.replace(/[/\\]+$/, '')
  if (!sinBarraFinal) return ''
  return `${sinBarraFinal}_ORGANIZADO`
}

export default function KioskScreen({
  status,
  carpeta,
  onPickCarpeta,
  inspecciones,
  inspeccion,
  onSelectInspeccion,
  estadillo,
  onEstadillo,
  onOrganizar,
  onSubirCrudo,
  busy,
  progreso,
  onAbrirCompleta,
}) {
  const destino = derivarDestino(carpeta)
  const email = status?.email || ''
  const inicial = email ? email[0].toUpperCase() : ''

  const organizarDeshabilitado = !carpeta || busy
  const subirCrudoDeshabilitado = !carpeta || !inspeccion || busy

  function organizar() {
    onOrganizar({ origen: carpeta, destino, estadillo })
  }

  function subirCrudo() {
    onSubirCrudo({ carpeta, inspeccion })
  }

  return (
    <div className="kiosk">
      <div className="kiosk-header">
        <button
          type="button"
          className="kiosk-avatar"
          data-testid="kiosk-avatar"
          onClick={onAbrirCompleta}
        >
          {status ? (
            status.picture ? (
              <img src={status.picture} alt={`Avatar de ${email}`} className="kiosk-avatar-img" />
            ) : (
              <span className="kiosk-avatar-fallback">{inicial}</span>
            )
          ) : (
            <span className="kiosk-sin-sesion">Sin sesión</span>
          )}
        </button>
      </div>

      <div className="kiosk-carpeta">
        <button type="button" className="btn-ghost kiosk-btn-carpeta" onClick={onPickCarpeta} disabled={busy}>
          Elegir carpeta…
        </button>
        <span className="kiosk-carpeta-actual">{carpeta || 'Sin carpeta'}</span>
        {carpeta && <span className="kiosk-destino">{destino}</span>}
      </div>

      <details className="kiosk-estadillo">
        <summary>Estadillo (opcional)</summary>
        <input
          className="glass-input"
          type="text"
          value={estadillo || ''}
          onChange={(e) => onEstadillo(e.target.value)}
          disabled={busy}
          placeholder="Ruta del estadillo"
        />
      </details>

      <div className="kiosk-inspeccion">
        <select
          className="glass-input"
          value={inspeccion?.id ?? ''}
          disabled={busy}
          onChange={(e) => {
            const elegida = inspecciones.find((i) => String(i.id) === e.target.value) || null
            onSelectInspeccion(elegida)
          }}
        >
          <option value="">Sin inspección</option>
          {inspecciones.map((i) => (
            <option key={i.id} value={i.id}>
              {i.nombre}
            </option>
          ))}
        </select>
      </div>

      {progreso && (
        <div className="kiosk-progreso" data-testid="kiosk-progreso">
          <span className="kiosk-progreso-fase">{progreso.fase}</span>
          <span className="kiosk-progreso-pct">{progreso.pct}%</span>
        </div>
      )}

      <div className="kiosk-acciones">
        <button
          type="button"
          className="kiosk-btn kiosk-btn-organizar"
          onClick={organizar}
          disabled={organizarDeshabilitado}
        >
          Organizar
        </button>
        <button
          type="button"
          className="kiosk-btn kiosk-btn-subir-crudo"
          onClick={subirCrudo}
          disabled={subirCrudoDeshabilitado}
        >
          Subir en crudo
        </button>
      </div>
    </div>
  )
}
