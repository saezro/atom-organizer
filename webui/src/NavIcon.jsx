// SVG inline a proposito: `webui/package.json` solo depende de react y
// react-dom, y meter `react-icons` por cinco iconos engorda el bundle que va
// empaquetado dentro del ejecutable.
const TRAZOS = {
  organizar: 'M3 7h18M3 12h18M3 17h12',
  bucket: 'M12 3v12m0 0l-4-4m4 4l4-4M4 19h16',
  aerotools: 'M12 2l9 6-9 6-9-6 9-6zm0 12l9-6M3 8l9 6',
  otros: 'M4 6h6v6H4zM14 6h6v6h-6zM9 16h6v4H9z',
  config: 'M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2 1-2 4-2-1-2 1-1 2h-4l-1-2-2-1-2 1-2-4 2-1v-2l-2-1 2-4 2 1 2-1 1-2h4l1 2 2 1 2-1 2 4-2 1v2z',
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
