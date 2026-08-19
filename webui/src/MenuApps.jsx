import { useState } from 'react'
import BotonToque from './pulsacion.jsx'

// Rejilla del launcher del kiosco: 2 columnas x 2 filas, tamaño pensado para
// dedo en un panel resistivo de 480x320 (ver pulsacion.jsx). Con más de
// `porPagina` apps no se reduce el icono ni se mete scroll (nada de eso es
// cómodo con el dedo): se pagina con dos flechas a la derecha, igual que la
// lista de inspecciones del paso 2 (KioskScreen.jsx).
export default function MenuApps({ apps, tactil, disabled, onAbrir, porPagina = 4 }) {
  const [pagina, setPagina] = useState(0)
  const totalPaginas = Math.max(1, Math.ceil(apps.length / porPagina))
  const inicio = pagina * porPagina
  const visibles = apps.slice(inicio, inicio + porPagina)
  const conPaginacion = apps.length > porPagina

  return (
    <div className="kiosk-apps-wrap">
      <div className="kiosk-apps">
        {visibles.map((app) => (
          <BotonToque
            key={app.id}
            className="kiosk-app"
            tactil={tactil}
            disabled={disabled}
            onActivar={() => onAbrir(app.id)}
            data-testid={'kiosk-app-' + app.id}
          >
            <app.Icono />
            <span className="kiosk-app-nombre">{app.nombre}</span>
          </BotonToque>
        ))}
      </div>
      {conPaginacion && (
        <div className="kiosk-apps-nav">
          <BotonToque
            className="kiosk-apps-nav-btn"
            tactil={tactil}
            disabled={disabled || pagina <= 0}
            onActivar={() => setPagina((p) => Math.max(0, p - 1))}
            data-testid="kiosk-apps-arriba"
            aria-label="Página anterior de apps"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 15l6-6 6 6" />
            </svg>
          </BotonToque>
          <span className="kiosk-apps-pagina" data-testid="kiosk-apps-pagina">
            {pagina + 1}/{totalPaginas}
          </span>
          <BotonToque
            className="kiosk-apps-nav-btn"
            tactil={tactil}
            disabled={disabled || pagina >= totalPaginas - 1}
            onActivar={() => setPagina((p) => Math.min(totalPaginas - 1, p + 1))}
            data-testid="kiosk-apps-abajo"
            aria-label="Página siguiente de apps"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </BotonToque>
        </div>
      )}
    </div>
  )
}
