// SVG inline a proposito: `webui/package.json` solo depende de react y
// react-dom, y meter `react-icons` por cinco iconos engorda el bundle que va
// empaquetado dentro del ejecutable.
const TRAZOS = {
  // Inicio: casa de trazo simple, mismo lenguaje que el resto de la nav.
  home: 'M3 10.5L12 3l9 7.5V20a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z',
  organizar: 'M3 7h18M3 12h18M3 17h12',
  bucket: 'M12 3v12m0 0l-4-4m4 4l4-4M4 19h16',
  aerotools: 'M12 2l9 6-9 6-9-6 9-6zm0 12l9-6M3 8l9 6',
  otros: 'M4 6h6v6H4zM14 6h6v6h-6zM9 16h6v4H9z',
  config: 'M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2 1-2 4-2-1-2 1-1 2h-4l-1-2-2-1-2 1-2-4 2-1v-2l-2-1 2-4 2 1 2-1 1-2h4l1 2 2 1 2-1 2 4-2 1v2z',
  // Trabajo: mismo lenguaje de línea simple que `organizar` (listado), con un
  // check para marcar el paso a paso del flujo de subida.
  trabajo: 'M3 7h18M3 12h9M3 17h9M16 15l2.5 2.5L21 14',
  // Herramientas: variación de `otros` (cuadrícula de accesos) con una llave
  // inglesa esquemática para diferenciarla del resto de la nav.
  herramientas: 'M14.7 6.3a4 4 0 00-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 005.4-5.4l-2.6 2.6-2-2z',
}

export default function NavIcon({ id }) {
  return (
    <svg viewBox="0 0 24 24" width="1.5rem" height="1.5rem" fill="none"
         stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true">
      <path d={TRAZOS[id]} />
    </svg>
  )
}
