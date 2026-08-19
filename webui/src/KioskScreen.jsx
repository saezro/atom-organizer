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
import { useRef, useState } from 'react'
import { api, isServerMode } from './bridge.js'
import BotonToque, { pxDeRem, UMBRAL_REM } from './pulsacion.jsx'
import PairScreen from './PairScreen.jsx'
import InspeccionSelector from './InspeccionSelector.jsx'
import EstadilloField from './EstadilloField.jsx'

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
  onActualizarInspecciones,
  estadillo,
  onEstadillo,
  onOrganizar,
  onSubirCrudo,
  onRefreshStatus,
  busy,
  progreso,
  // Solo para pruebas: permite montar el componente directamente en un paso.
  accionInicial = null,
}) {
  const [accion, setAccion] = useState(accionInicial)
  const destino = derivarDestino(carpeta)
  const email = status?.email || ''
  const inicial = email ? email[0].toUpperCase() : ''
  const tactil = isServerMode()

  // La lista de `InspeccionSelector` puede crecer por encima del hueco
  // disponible en el paso 2: mismo problema que la lista de `FolderPicker`
  // (panel resistivo = ratón absoluto, arrastrar el dedo no scrollea solo),
  // mismo arreglo (`FolderPicker.jsx:83-116`), aplicado al `div` que la
  // envuelve porque el componente en sí no expone su `<ul>` interno.
  const inspRef = useRef(null)
  const arrastreInsp = useRef({ activo: false, y0: 0, top0: 0, umbral: pxDeRem(UMBRAL_REM), movido: false })
  const alPulsarInsp = (e) => {
    const el = inspRef.current
    if (!el) return
    arrastreInsp.current = {
      activo: true, y0: e.clientY, top0: el.scrollTop,
      umbral: pxDeRem(UMBRAL_REM), movido: false,
    }
  }
  const alMoverInsp = (e) => {
    const a = arrastreInsp.current
    const el = inspRef.current
    if (!a.activo || !el) return
    const dy = e.clientY - a.y0
    if (!a.movido && Math.abs(dy) < a.umbral) return
    a.movido = true
    el.scrollTop = a.top0 - dy
  }
  const alSoltarInsp = () => { arrastreInsp.current.activo = false }
  // Si hubo arrastre, el toque era scroll, no selección: se descarta el click
  // sintetizado antes de que llegue al botón de la inspección.
  const alHacerClickInsp = (e) => {
    if (!arrastreInsp.current.movido) return
    arrastreInsp.current.movido = false
    e.preventDefault()
    e.stopPropagation()
  }

  // El avatar es la ÚNICA vía táctil a la sesión: abre una pantalla propia
  // dentro del kiosco (cuenta vinculada o QR de emparejamiento). Sigue sin ser
  // una puerta a la UI completa de escritorio, que es inusable con el dedo en
  // un panel de 480x320 sin ratón ni teclado.
  const avatar = (
    <BotonToque
      className="kiosk-avatar"
      tactil={tactil}
      onActivar={() => setAccion('cuenta')}
      data-testid="kiosk-avatar"
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
    </BotonToque>
  )

  const barraProgreso = progreso && (
    <div className="kiosk-progreso" data-testid="kiosk-progreso">
      <span className="kiosk-progreso-fase">{progreso.fase}</span>
      <span className="kiosk-progreso-pct">{progreso.pct}%</span>
    </div>
  )

  // ------------------------------------------------------------- cuenta
  // Sin sesión vinculada la única salida es el QR: este equipo no tiene
  // navegador propio para el consentimiento OAuth (ver PairScreen).
  if (accion === 'cuenta') {
    const logueado = Boolean(status?.logged_in)
    return (
      <div className="kiosk kiosk-cuenta">
        <div className="kiosk-header kiosk-header-paso">
          <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setAccion(null)}>
            ← Atrás
          </BotonToque>
          <span className="kiosk-titulo">Cuenta</span>
        </div>
        {logueado ? (
          <div className="kiosk-cuenta-datos">
            <span className="kiosk-cuenta-email">{email}</span>
            <BotonToque
              className="btn-ghost kiosk-btn"
              tactil={tactil}
              onActivar={async () => {
                await api.cloudLogout().catch(() => {})
                onRefreshStatus?.()
              }}
            >
              Cerrar sesión
            </BotonToque>
          </div>
        ) : (
          <PairScreen onPaired={() => { onRefreshStatus?.(); setAccion(null) }} />
        )}
      </div>
    )
  }

  // ---------------------------------------------------------------- paso 1
  if (!accion) {
    return (
      <div className="kiosk">
        <div className="kiosk-header">{avatar}</div>
        {barraProgreso}
        <div className="kiosk-menu">
          <BotonToque
            className="kiosk-menu-btn kiosk-menu-organizar"
            tactil={tactil}
            onActivar={() => setAccion('organizar')}
            disabled={busy}
          >
            Organizar
          </BotonToque>
          <BotonToque
            className="kiosk-menu-btn kiosk-menu-subir"
            tactil={tactil}
            onActivar={() => setAccion('subir')}
            disabled={busy}
          >
            Subir en crudo
          </BotonToque>
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
        <BotonToque className="kiosk-atras" tactil={tactil} onActivar={() => setAccion(null)} disabled={busy}>
          ← Atrás
        </BotonToque>
        <span className="kiosk-titulo">{esOrganizar ? 'Organizar' : 'Subir en crudo'}</span>
        {avatar}
      </div>

      <div className="kiosk-carpeta">
        <BotonToque className="btn-ghost kiosk-btn-carpeta" tactil={tactil} onActivar={onPickCarpeta} disabled={busy}>
          Elegir carpeta…
        </BotonToque>
        <span className="kiosk-carpeta-actual">{carpeta || 'Sin carpeta'}</span>
        {esOrganizar && carpeta && <span className="kiosk-destino">{destino}</span>}
      </div>

      {esOrganizar ? (
        <div className="kiosk-estadillo">
          <EstadilloField value={estadillo} onChange={onEstadillo} disabled={busy} />
        </div>
      ) : (
        <div className="kiosk-inspeccion">
          {inspeccion ? (
            <div className="field-row">
              <input className="glass-input" type="text" value={inspeccion.etiqueta || inspeccion.nombre || ''} readOnly />
              <BotonToque
                className="btn-ghost"
                tactil={tactil}
                onActivar={() => onSelectInspeccion(null)}
                disabled={busy}
              >
                Cambiar
              </BotonToque>
            </div>
          ) : (
            <div
              className="kiosk-insp-scroll"
              ref={inspRef}
              {...(tactil ? {
                onPointerDown: alPulsarInsp,
                onPointerMove: alMoverInsp,
                onPointerUp: alSoltarInsp,
                onPointerCancel: alSoltarInsp,
                onPointerLeave: alSoltarInsp,
                onClickCapture: alHacerClickInsp,
              } : {})}
            >
              <InspeccionSelector
                inspecciones={inspecciones}
                onElegir={(prefijo) => {
                  const elegida = inspecciones.find((i) => i.prefijo === prefijo) || null
                  onSelectInspeccion(elegida)
                }}
                // El kiosco no crea inspecciones nuevas: requiere teclear
                // Empresa--Planta--Año--Tipo, inviable con el panel táctil sin
                // teclado. Esa vía sigue solo en la UI de escritorio.
                onNueva={() => {}}
                ocupado={busy}
                onActualizar={onActualizarInspecciones}
              />
            </div>
          )}
        </div>
      )}

      {barraProgreso}

      <div className="kiosk-acciones">
        <BotonToque
          className={'kiosk-btn ' + (esOrganizar ? 'kiosk-btn-organizar' : 'kiosk-btn-subir-crudo')}
          tactil={tactil}
          onActivar={confirmar}
          disabled={!listo || busy}
        >
          {esOrganizar ? 'Organizar' : 'Subir'}
        </BotonToque>
      </div>
    </div>
  )
}
