// Catálogo de apps del launcher del kiosco. Añadir una app nueva = una
// entrada más aquí (id, nombre visible e icono); MenuApps.jsx pinta lo que
// haya, no hace falta tocar nada más para que salga en la rejilla.
//
// Los iconos son SVG inline a mano (nada de librerías ni emoji): en la Pi el
// bundle ya va justo y un set de iconos completo pesa mucho más que los
// cuatro trazos que hacen falta aquí.
//
// El fichero es .js (no .jsx): se usa `createElement` en vez de sintaxis JSX
// para no depender de que el loader de esbuild trate `.js` como JSX.
import { createElement as h } from 'react'

const PROPS_SVG = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: '2',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  width: '1em',
  height: '1em',
  'aria-hidden': 'true',
}

function IconoOrganizar() {
  return h('svg', PROPS_SVG,
    h('path', { d: 'M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z' })
  )
}

function IconoSubir() {
  return h('svg', PROPS_SVG,
    h('path', { d: 'M7 18a4 4 0 01-1-7.87A5.5 5.5 0 0116.9 8.1 4.5 4.5 0 0117 17H7z' }),
    h('path', { d: 'M12 12v7' }),
    h('path', { d: 'M9 15l3-3 3 3' })
  )
}

function IconoCuenta() {
  return h('svg', PROPS_SVG,
    h('circle', { cx: '12', cy: '8', r: '4' }),
    h('path', { d: 'M4 21a8 8 0 0116 0' })
  )
}

function IconoSistema() {
  return h('svg', PROPS_SVG,
    h('circle', { cx: '12', cy: '12', r: '3' }),
    h('path', {
      d: 'M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1.04 1.56V21a2 2 0 11-4 0v-.09a1.7 1.7 0 00-1.04-1.56 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.7 1.7 0 00.34-1.87 1.7 1.7 0 00-1.56-1.04H3a2 2 0 110-4h.09a1.7 1.7 0 001.56-1.04 1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06a1.7 1.7 0 001.87.34H9a1.7 1.7 0 001.04-1.56V3a2 2 0 114 0v.09a1.7 1.7 0 001.04 1.56 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06a1.7 1.7 0 00-.34 1.87V9a1.7 1.7 0 001.56 1.04H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.56 1.04z',
    })
  )
}

export const APPS = [
  { id: 'organizar', nombre: 'Organizar', Icono: IconoOrganizar },
  { id: 'subir', nombre: 'Subir en crudo', Icono: IconoSubir },
  { id: 'cuenta', nombre: 'Cuenta', Icono: IconoCuenta },
  { id: 'sistema', nombre: 'Sistema', Icono: IconoSistema },
]
