import { estadisticasSerie } from './subidaMedicion'

// Gráfica SVG propia (sin dependencias): velocidad (MB/s, línea naranja, eje
// izquierdo) y datos acumulados enviados (GB, área gris, eje derecho) frente al
// tiempo. Las marcas verticales son reintentos y pausas que midió el backend.
const W = 720
const H = 220
const M = { l: 46, r: 46, t: 14, b: 28 }
const IW = W - M.l - M.r
const IH = H - M.t - M.b

const COLOR_MARCA = { '429': 'var(--rojo, #f87171)', '5xx': 'var(--rojo, #f87171)', auth: 'var(--rojo, #f87171)', http: '#f0b866', red: '#f0b866', pausa: '#9ca3af' }
const NOMBRE_MARCA = { '429': 'Límite de peticiones (429)', '5xx': 'Error del servidor (5xx)', auth: 'Credencial renovada (401/403)', http: 'Reintento HTTP', red: 'Corte de red', pausa: 'Pausa' }

const esc = (n, d = 1) => n.toLocaleString('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d })
function techo(v) {
  // Máximo «redondo» del eje (1, 2, 5 × 10^n) por encima de v.
  if (!(v > 0)) return 1
  const e = Math.pow(10, Math.floor(Math.log10(v)))
  const f = v / e
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e
}
function mmss(seg) {
  const t = Math.max(0, Math.round(seg))
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

export default function GraficaSubida({ serie, compacta = false }) {
  if (!serie || serie.length < 2) {
    return <div className="sub-grafica sub-grafica-vacia" data-testid="grafica-vacia">La gráfica aparece en cuanto empieza el envío.</div>
  }
  const est = estadisticasSerie(serie)
  const tMax = Math.max(serie[serie.length - 1].t, 1)
  const vMax = techo(Math.max(est.pico * 1.1, 0.5))
  const gbMax = techo(Math.max(est.bytes / 1e9, 0.01))
  const x = (t) => M.l + (t / tMax) * IW
  const yV = (v) => M.t + IH - (v / vMax) * IH
  const yG = (g) => M.t + IH - (g / gbMax) * IH

  const area = serie.map((s, i) => `${i ? 'L' : 'M'}${x(s.t).toFixed(1)},${yG(s.bytes / 1e9).toFixed(1)}`).join(' ')
  const areaCerrada = `${area} L${x(serie[serie.length - 1].t).toFixed(1)},${(M.t + IH).toFixed(1)} L${x(serie[0].t).toFixed(1)},${(M.t + IH).toFixed(1)} Z`
  // La línea de velocidad se corta donde no hay velocidad (listando/comprobando).
  const tramos = []
  let actual = []
  for (const s of serie) {
    if (s.mbs == null) { if (actual.length) tramos.push(actual); actual = [] } else actual.push(s)
  }
  if (actual.length) tramos.push(actual)
  const lineas = tramos.map((tr) => tr.map((s, i) => `${i ? 'L' : 'M'}${x(s.t).toFixed(1)},${yV(s.mbs).toFixed(1)}`).join(' '))

  const marcas = serie.flatMap((s) => s.marcas.map((m, i) => ({ ...m, t: s.t, k: `${s.t}-${m.tipo}-${i}` })))
  const ticksV = [0, vMax / 2, vMax]
  const ticksT = [0, 0.25, 0.5, 0.75, 1].map((f) => f * tMax)
  const resumenA11y = `Velocidad de subida frente al tiempo. Pico ${esc(est.pico)} MB/s${est.media != null ? `, media ${esc(est.media)} MB/s` : ''}. ${esc(est.bytes / 1e9, 2)} GB enviados en ${mmss(tMax)}. ${marcas.length} incidencias marcadas.`

  return (
    <figure className={'sub-grafica' + (compacta ? ' sub-grafica-compacta' : '')} data-testid="grafica-subida">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={resumenA11y} preserveAspectRatio="xMidYMid meet">
        {ticksV.map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={yV(v)} y2={yV(v)} className="sub-g-rejilla" />
            <text x={M.l - 6} y={yV(v) + 3} textAnchor="end" className="sub-g-eje sub-g-eje-v">{esc(v, v < 10 && v % 1 ? 1 : 0)}</text>
            <text x={W - M.r + 6} y={yV(v) + 3} textAnchor="start" className="sub-g-eje">{esc((v / vMax) * gbMax, gbMax < 10 ? 1 : 0)}</text>
          </g>
        ))}
        <text x={M.l - 6} y={M.t - 4} textAnchor="end" className="sub-g-unidad sub-g-eje-v">MB/s</text>
        <text x={W - M.r + 6} y={M.t - 4} textAnchor="start" className="sub-g-unidad">GB</text>
        {ticksT.map((t) => (
          <text key={t} x={x(t)} y={H - 8} textAnchor={t === 0 ? 'start' : t === tMax ? 'end' : 'middle'} className="sub-g-eje">{mmss(t)}</text>
        ))}
        <path d={areaCerrada} className="sub-g-area" />
        <path d={area} className="sub-g-area-linea" />
        {est.media != null && (
          <line x1={M.l} x2={W - M.r} y1={yV(est.media)} y2={yV(est.media)} className="sub-g-media">
            <title>{`Media ${esc(est.media)} MB/s`}</title>
          </line>
        )}
        {marcas.map((m) => (
          <g key={m.k}>
            <line x1={x(m.t)} x2={x(m.t)} y1={M.t} y2={M.t + IH} stroke={COLOR_MARCA[m.tipo] || '#f0b866'} className="sub-g-marca" />
            <circle cx={x(m.t)} cy={M.t + IH} r="3" fill={COLOR_MARCA[m.tipo] || '#f0b866'}>
              <title>{`${NOMBRE_MARCA[m.tipo] || m.tipo}${m.n > 1 ? ` ×${m.n}` : ''} · ${mmss(m.t)}`}</title>
            </circle>
          </g>
        ))}
        {lineas.map((d, i) => <path key={i} d={d} className="sub-g-velocidad" />)}
      </svg>
      <figcaption className="sub-g-leyenda">
        <span><i className="sub-leyenda-vel" /> Velocidad (MB/s)</span>
        <span><i className="sub-leyenda-area" /> Datos enviados (GB)</span>
        {est.media != null && <span><i className="sub-leyenda-media" /> Media {esc(est.media)} MB/s</span>}
        <span>Pico {esc(est.pico)} MB/s</span>
        {marcas.length > 0 && <span><i className="sub-leyenda-marca" /> {marcas.length} incidencia{marcas.length === 1 ? '' : 's'} (reintentos y pausas)</span>}
      </figcaption>
    </figure>
  )
}
