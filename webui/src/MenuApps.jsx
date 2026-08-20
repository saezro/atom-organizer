import { useState } from 'react'
import BotonToque from './pulsacion.jsx'

// Rejilla de cards del kiosco: la usan el launcher del home (2x2, apps) y el
// índice de Ajustes (`compacta`, 3x2). Tamaño pensado para dedo en un panel
// resistivo de 480x320 (ver pulsacion.jsx). Con más de `porPagina` cards no se
// reduce el icono ni se mete scroll (nada de eso es cómodo con el dedo): se
// pagina con las dos flechas de la derecha.
//
// La columna de paginación se pinta SIEMPRE (deshabilitada si sobra), no solo
// cuando hay varias páginas: así la rejilla mide lo mismo en todas las
// pantallas y las cards no cambian de tamaño al entrar en una u otra.
export default function MenuApps({
  apps,
  tactil,
  disabled,
  onAbrir,
  porPagina = 4,
  compacta = false,
  testidPrefijo = 'kiosk-app-',
}) {
  const [pagina, setPagina] = useState(0)
  const totalPaginas = Math.max(1, Math.ceil(apps.length / porPagina))
  const inicio = pagina * porPagina
  const visibles = apps.slice(inicio, inicio + porPagina)

  return (
    <div className="kiosk-apps-wrap">
      <div className={'kiosk-apps' + (compacta ? ' kiosk-apps-compacta' : '')}>
        {visibles.map((app) => (
          <BotonToque
            key={app.id}
            className="kiosk-app"
            tactil={tactil}
            disabled={disabled}
            onActivar={() => onAbrir(app.id)}
            data-testid={testidPrefijo + app.id}
          >
            <app.Icono />
            <span className="kiosk-app-nombre">{app.nombre}</span>
          </BotonToque>
        ))}
      </div>
      <div className="kiosk-apps-nav">
        <BotonToque
          className="kiosk-apps-nav-btn"
          tactil={tactil}
          disabled={disabled || pagina <= 0}
          onActivar={() => setPagina((p) => Math.max(0, p - 1))}
          data-testid="kiosk-apps-arriba"
          aria-label="Página anterior"
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
          aria-label="Página siguiente"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M6 9l6 6-6 6" />
          </svg>
        </BotonToque>
      </div>
    </div>
  )
}
