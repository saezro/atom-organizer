// Pantalla "kiosco" para la Raspberry Pi con pantalla táctil de 480x320 px,
// que sustituye a la UI normal cuando el Organizer corre en modo servidor.
//
// El flujo va en DOS pasos y en este orden: primero se elige QUÉ se quiere
// hacer (organizar / subir en crudo) y solo después se piden los datos que esa
// acción necesita. Antes se pedían todos los datos por delante y las dos
// acciones compartían pantalla, lo que obligaba a elegir carpeta sin saber
// para qué y dejaba a la vista campos que no aplicaban (el selector de
// inspección no pinta nada al organizar).
//
// El panel de la Pi es resistivo (ADS7846): menos preciso que uno capacitivo,
// así que el paso 1 son dos botones que ocupan media pantalla cada uno y en el
// paso 2 nada táctil baja de `--toque-min`.
import { useState } from 'react'

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
  // Solo para pruebas: permite montar el componente directamente en un paso.
  accionInicial = null,
}) {
  const [accion, setAccion] = useState(accionInicial)
  const destino = derivarDestino(carpeta)
  const email = status?.email || ''
  const inicial = email ? email[0].toUpperCase() : ''

  const avatar = (
    <button type="button" className="kiosk-avatar" data-testid="kiosk-avatar" onClick={onAbrirCompleta}>
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
  )

  const barraProgreso = progreso && (
    <div className="kiosk-progreso" data-testid="kiosk-progreso">
      <span className="kiosk-progreso-fase">{progreso.fase}</span>
      <span className="kiosk-progreso-pct">{progreso.pct}%</span>
    </div>
  )

  // ---------------------------------------------------------------- paso 1
  if (!accion) {
    return (
      <div className="kiosk">
        <div className="kiosk-header">{avatar}</div>
        {barraProgreso}
        <div className="kiosk-menu">
          <button
            type="button"
            className="kiosk-menu-btn kiosk-menu-organizar"
            onClick={() => setAccion('organizar')}
            disabled={busy}
          >
            Organizar
          </button>
          <button
            type="button"
            className="kiosk-menu-btn kiosk-menu-subir"
            onClick={() => setAccion('subir')}
            disabled={busy}
          >
            Subir en crudo
          </button>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------- paso 2
  const esOrganizar = accion === 'organizar'
  // «Subir en crudo» no tiene destino derivado: el destino es el prefijo del
  // bucket, que sale de la inspección elegida.
  const listo = esOrganizar ? Boolean(carpeta) : Boolean(carpeta && inspeccion)

  function confirmar() {
    if (esOrganizar) onOrganizar({ origen: carpeta, destino, estadillo })
    else onSubirCrudo({ carpeta, inspeccion })
  }

  return (
    <div className="kiosk">
      <div className="kiosk-header kiosk-header-paso">
        <button type="button" className="kiosk-atras" onClick={() => setAccion(null)} disabled={busy}>
          ← Atrás
        </button>
        <span className="kiosk-titulo">{esOrganizar ? 'Organizar' : 'Subir en crudo'}</span>
        {avatar}
      </div>

      <div className="kiosk-carpeta">
        <button type="button" className="btn-ghost kiosk-btn-carpeta" onClick={onPickCarpeta} disabled={busy}>
          Elegir carpeta…
        </button>
        <span className="kiosk-carpeta-actual">{carpeta || 'Sin carpeta'}</span>
        {esOrganizar && carpeta && <span className="kiosk-destino">{destino}</span>}
      </div>

      {esOrganizar ? (
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
      ) : (
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
      )}

      {barraProgreso}

      <div className="kiosk-acciones">
        <button
          type="button"
          className={'kiosk-btn ' + (esOrganizar ? 'kiosk-btn-organizar' : 'kiosk-btn-subir-crudo')}
          onClick={confirmar}
          disabled={!listo || busy}
        >
          {esOrganizar ? 'Organizar' : 'Subir'}
        </button>
      </div>
    </div>
  )
}
