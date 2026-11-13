import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ANCHO_MIN_DIA_PX, MIN_DURACION_MIN, ALTO_MIN_BLOQUE_PX, ANCHO_MIN_BLOQUE_PX, GAP_CARRIL_PX,
  altoHoraPx, anchosColumnas, anchoBloquePx, contarVuelosDia, diasDelRango, layoutDia, minDuracionParaAlto,
  rangoHoras, segmentosDeVuelo, etiquetaCompacta, formatoBloque, textoHorasBloque,
  cubetasPorHora, formatearHora, vueloQueContiene, motivosAviso,
} from './calendarioHoras.js'

// Réplica del calendario de la Suite (CalendarioSemana + EventoVuelo): rejilla semanal, columna de horas
// sticky, alto por hora de `altoHoraPx`, días vacíos estrechos, scroll horizontal con snap por semana y el
// día entero visible (el alto lo da el rango de horas; el scroll vertical es el del modal).
// Añadido propio: TRAMOS grises de fotos libres a todo el ancho del día, en su hora real (nunca estimada).
const DIAS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const ANCHO_HORAS_PX = 56
const ALTO_CABECERA_PX = 58
const ALTO_MIN_TRAMO_PX = 36

// Color: un único color base para todos los vuelos (los colores por PB de la Suite no significaban nada
// más que «PB distinto»). Los estados se distinguen por color: aviso (ámbar), tramo sin colocar (gris),
// seleccionado (naranja), tramo dentro de un vuelo (rojo discontinuo).
const COLOR_VUELO = '#38bdf8'
const COLOR_AVISO = '#fbbf24'

// Giro: flechita SVG en la esquina superior derecha del bloque, como EventoVuelo.jsx de la Suite
// (FiRotateCw para 90, FiRotateCcw para 270, mismos paths de Feather; 14 px allí, aquí 18 px).
// Sin giro (0): flecha arriba atenuada, para que todo vuelo muestre algo. Sin dato: «?» ámbar.
export function infoGiro(angulo) {
  if (angulo === 0) return { tipo: 'cero', etiqueta: 'Sin giro (0º)' }
  if (angulo === 90) return { tipo: 'cw', etiqueta: 'Gira 90º horario' }
  if (angulo === 270) return { tipo: 'ccw', etiqueta: 'Gira 270º antihorario' }
  return { tipo: 'falta', etiqueta: 'Giro desconocido' }
}

function IconoGiro({ giro, pequeno }) {
  const px = pequeno ? 14 : 18
  if (giro.tipo === 'falta') {
    return <span className="ri-giro falta" data-testid="ri-giro" aria-label={giro.etiqueta} title={giro.etiqueta}>?</span>
  }
  const svg = { width: px, height: px, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }
  return (
    <svg {...svg} className={giro.tipo === 'cero' ? 'ri-giro cero' : 'ri-giro'} data-testid="ri-giro" aria-label={giro.etiqueta} role="img">
      <title>{giro.etiqueta}</title>
      {giro.tipo === 'cw' ? <><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></> : null}
      {giro.tipo === 'ccw' ? <><polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></> : null}
      {giro.tipo === 'cero' ? <><line x1="12" y1="19" x2="12" y2="5" /><polyline points="5 12 12 5 19 12" /></> : null}
    </svg>
  )
}

const aVueloExif = (v) => ({ pb: v.pb, vuelo: v.vuelo, sufijo: v.sufijo, inicio_exif: v.inicio, fin_exif: v.fin })

function EventoVuelo({ seg, vuelo, desdeHora, altoHora, anchoCol, minDuracionMin }) {
  const top = ((seg.iniMin - desdeHora * 60) / 60) * altoHora
  const alto = Math.max((Math.max(seg.finMin - seg.iniMin, minDuracionMin) / 60) * altoHora, ALTO_MIN_BLOQUE_PX)
  const w = 100 / seg.nCols
  const fmt = formatoBloque(anchoBloquePx(anchoCol, seg.nCols), alto)
  const giro = infoGiro(vuelo.angulo)
  const motivos = motivosAviso(vuelo)
  const textoMotivos = motivos.join(' · ')
  const color = motivos.length ? COLOR_AVISO : COLOR_VUELO
  const cubetas = cubetasPorHora(vuelo)
  return (
    <div className="ri-vuelo" data-testid="ri-vuelo"
         title={`${etiquetaCompacta(vuelo)} · ${formatearHora(seg.iniMin)}-${formatearHora(seg.finMin)} · ${vuelo.n_rgb} RGB / ${vuelo.n_t} T${motivos.length ? ` · Aviso: ${textoMotivos}` : ''}`}
         style={{
           top, height: Math.max(alto - 1, 0), left: `calc(${seg.col * w}% + ${GAP_CARRIL_PX / 2}px)`,
           width: `max(${ANCHO_MIN_BLOQUE_PX}px, calc(${w}% - ${GAP_CARRIL_PX}px))`,
           borderColor: `${color}b3`, backgroundColor: `${color}2e`,
         }}>
      <div className="ri-vuelo-tit">{etiquetaCompacta(vuelo)}</div>
      <IconoGiro giro={giro} pequeno={fmt.iconoPequeno} />
      <div className="ri-vuelo-hora">
        {textoHorasBloque(fmt.linea2, seg.iniMin, seg.finMin)}
        {fmt.linea2 !== 'completa' && motivos.length ? <span className="ri-vuelo-aviso" title={`Aviso: ${textoMotivos}`}> ⚠</span> : null}
      </div>
      {fmt.linea2 === 'completa' && motivos.length ? (
        <div className="ri-vuelo-motivo" data-testid="ri-vuelo-motivo" title={`Aviso: ${textoMotivos}`}>⚠ {textoMotivos}</div>
      ) : null}
      {fmt.linea2 === 'completa' && cubetas.length ? (
        <div className="ri-cubetas">
          {cubetas.map((c) => <span key={c.hora} className="ri-cubeta" title={`${String(c.hora).padStart(2, '0')}h: ${c.n_rgb} RGB, ${c.n_t} T`}>{c.hora}h·{c.n_rgb + c.n_t + c.n_otro}</span>)}
        </div>
      ) : null}
    </div>
  )
}

export default function CalendarioHoras({ vuelos, tramos, seleccion, onSeleccion }) {
  const scrollRef = useRef(null)
  const [ancho, setAncho] = useState(0)
  const [scrollX, setScrollX] = useState(0)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return undefined
    const medir = () => setAncho(el.clientWidth)
    medir()
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(medir)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const calc = useMemo(() => {
    const conTramo = tramos.filter((t) => t.fecha)
    const dias = diasDelRango([
      ...vuelos.map(aVueloExif),
      ...conTramo.map((t) => ({ inicio_exif: `${t.fecha}T00:00:00`, fin_exif: null })),
    ])
    const porClave = new Map()
    const porDia = new Map(dias.map((d) => [d, []]))
    const todos = []
    for (const v of vuelos) {
      for (const s of segmentosDeVuelo(aVueloExif(v))) {
        porClave.set(s.clave, v)
        porDia.get(s.fecha).push(s)
        todos.push({ ...s, finMin: Math.min(1440, Math.max(s.finMin, s.iniMin + MIN_DURACION_MIN)) })
      }
    }
    for (const t of conTramo) todos.push({ iniMin: t.iniMin, finMin: Math.min(1440, Math.max(t.finMin, t.iniMin + MIN_DURACION_MIN)) })
    return { dias, porDia, porClave, rango: todos.length ? rangoHoras(todos) : null }
  }, [vuelos, tramos])

  const { dias, porDia, porClave, rango } = calc
  if (!rango) return <p className="ri-vacio">Sin vuelos con hora de cámara.</p>

  const anchoDias = Math.max(ancho - ANCHO_HORAS_PX, 0)
  const altoHora = altoHoraPx(dias.map((d) => porDia.get(d)))
  const minDur = minDuracionParaAlto(altoHora)
  const layouts = dias.map((d) => layoutDia(porDia.get(d), { minDuracionMin: minDur }))
  const nTramosDia = dias.map((d) => tramos.filter((t) => t.fecha === d).length)
  const anchos = anchosColumnas(
    layouts.map((l, i) => Math.max(contarVuelosDia(l), nTramosDia[i] ? 1 : 0)),
    anchoDias > 0 ? anchoDias : ANCHO_MIN_DIA_PX * 7, { anchoMinPx: ANCHO_MIN_DIA_PX },
  )
  const inicios = anchos.map((_, i) => anchos.slice(0, i).reduce((a, b) => a + b, 0))
  const alto = (rango.hasta - rango.desde) * altoHora
  const horas = Array.from({ length: rango.hasta - rango.desde + 1 }, (_, i) => rango.desde + i)
  const maxScroll = anchos.reduce((a, b) => a + b, 0) - anchoDias
  const mover = (dir) => {
    const semanas = inicios.filter((_, i) => i % 7 === 0)
    const destino = dir > 0 ? semanas.find((x) => x > scrollX + 1) : [...semanas].reverse().find((x) => x < scrollX - 1)
    scrollRef.current?.scrollTo?.({ left: destino ?? (dir > 0 ? maxScroll : 0), behavior: 'smooth' })
  }
  const pxMin = (min) => ((min - rango.desde * 60) / 60) * altoHora

  return (
    <div className="ri-cal" data-testid="ri-calendario">
      <div className="ri-cal-barra">
        <button type="button" className="btn-ghost ri-nav" onClick={() => mover(-1)} disabled={scrollX <= 1} aria-label="Semana anterior">‹</button>
        <button type="button" className="btn-ghost ri-nav" onClick={() => mover(1)} disabled={scrollX >= maxScroll - 1} aria-label="Semana siguiente">›</button>
        <span className="ri-cal-eje">Eje: hora cámara (EXIF, sin zona)</span>
      </div>
      <ul className="ri-leyenda" data-testid="ri-leyenda" aria-label="Leyenda">
        <li><i className="ri-ley vuelo" /> Vuelo del estadillo</li>
        <li><i className="ri-ley aviso" /> Vuelo con aviso: «RGB ≠ T» (distinto nº de fotos RGB y T) o «sin giro» (giro ?)</li>
        <li><i className="ri-ley tramo" /> Fotos sin colocar (tramo)</li>
        <li><i className="ri-ley sel" /> Tramo seleccionado</li>
        <li><i className="ri-ley dentro" /> Tramo dentro de un vuelo (aviso)</li>
        <li><i className="ri-ley alta" /> Confianza alta</li>
        <li><i className="ri-ley media" /> media</li>
        <li><i className="ri-ley baja" /> baja</li>
        <li className="ri-ley-giro">Giro: icono arriba a la derecha (flecha = sin giro, ↻ 90º, ↺ 270º, ? = sin dato)</li>
      </ul>
      <div ref={scrollRef} className="ri-cal-scroll" onScroll={(e) => setScrollX(e.currentTarget.scrollLeft)} style={{ scrollPaddingLeft: ANCHO_HORAS_PX }}>
        <div className="ri-cal-fila">
          <div className="ri-cal-horas" style={{ width: ANCHO_HORAS_PX }}>
            <div className="ri-cal-horas-cab" style={{ height: ALTO_CABECERA_PX }}>hora cámara</div>
            <div className="ri-escala-cuerpo" style={{ height: alto }}>
              {horas.map((h, i) => <span key={h} className="ri-hora" style={{ top: i * altoHora }}>{String(h % 24).padStart(2, '0')}:00</span>)}
            </div>
          </div>
          {dias.map((fecha, i) => {
            const segs = layouts[i]
            const nVuelos = contarVuelosDia(segs)
            const delTramos = tramos.filter((t) => t.fecha === fecha)
            const vacio = !nVuelos && !delTramos.length
            return (
              <div key={fecha} className={vacio ? 'ri-dia vacio' : 'ri-dia'} style={{ width: anchos[i] }} data-testid={`ri-dia-${fecha}`}>
                <div className="ri-dia-cab" style={{ height: ALTO_CABECERA_PX }}>
                  {DIAS[i % 7]}
                  <b className={nVuelos ? 'ri-dia-num act' : 'ri-dia-num'}>{Number(fecha.slice(8))}</b>
                  <em>{nVuelos ? `${nVuelos} ${nVuelos === 1 ? 'vuelo' : 'vuelos'}` : 'sin vuelos'}</em>
                </div>
                {vacio ? (
                  <div className="ri-dia-vacio" style={{ height: alto }}><span>sin vuelos</span></div>
                ) : (
                  <div className="ri-dia-cuerpo" style={{ height: alto, backgroundSize: `100% ${altoHora}px` }}>
                    <div className="ri-vuelos">
                      {segs.map((s) => (
                        <EventoVuelo key={`${s.clave}|${s.fecha}`} seg={s} vuelo={porClave.get(s.clave)} desdeHora={rango.desde}
                                     altoHora={altoHora} anchoCol={anchos[i]} minDuracionMin={minDur} />
                      ))}
                    </div>
                    {delTramos.map((t) => {
                      const dentro = vueloQueContiene(t, vuelos)
                      const cls = ['ri-tramo', t.id === seleccion ? 'act' : '', dentro ? 'aviso' : ''].filter(Boolean).join(' ')
                      const txt = `${formatearHora(t.iniMin)}-${formatearHora(t.finMin)}`
                      return (
                        <button key={t.id} type="button" data-testid="ri-tramo-bloque" className={cls}
                                style={{ top: pxMin(t.iniMin), height: Math.max(pxMin(t.finMin) - pxMin(t.iniMin), ALTO_MIN_TRAMO_PX) }}
                                title={dentro ? `AVISO: cae dentro de PB${dentro.pb}/V${dentro.vuelo}` : `Tramo ${txt} · ${t.n} fotos`}
                                aria-label={`Tramo ${txt}, ${t.n} fotos`}
                                onClick={() => onSeleccion?.(t.id === seleccion ? null : t.id)}>
                          <span className="ri-tramo-tit">{dentro ? '⚠ ' : ''}Sin colocar</span>
                          <span className="ri-tramo-hora">{txt} · {t.n} fotos</span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
