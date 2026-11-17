import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ANCHO_MIN_DIA_PX, MIN_DURACION_MIN, ALTO_MIN_BLOQUE_PX, ANCHO_MIN_BLOQUE_PX, GAP_CARRIL_PX,
  altoHoraPx, altoHoraAjustado, marcasEje, anchosColumnas, anchoBloquePx, contarVuelosDia, diasDelRango, layoutDia, minDuracionParaAlto,
  rangoHoras, segmentosDeVuelo, ALTO_COMPACTO_PX, etiquetaCompacta, formatoBloque, textoHorasBloque, modoContenido, textoRangoHora, rangoZoomHora, horaZoomVecina, PASOS_EJE_ZOOM, textoFotos,
  cubetasPorHora, formatearHora, vueloQueContiene, motivosAviso,
} from './calendarioHoras.js'

// Réplica del calendario de la Suite (CalendarioSemana + EventoVuelo): rejilla semanal, columna de horas
// sticky, alto por hora de `altoHoraPx`, días vacíos estrechos, scroll horizontal con snap por semana y el
// día entero visible (el alto lo da el rango de horas; el scroll vertical es el del modal).
// Añadido propio: TRAMOS grises de fotos libres a todo el ancho del día, en su hora real (nunca estimada).
const DIAS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const ANCHO_HORAS_PX = 30 // gutter de etiquetas de hora DENTRO de cada día (el eje es por día)
const ALTO_CABECERA_PX = 58
const MARGEN_ALTO_PX = 30 // padding inferior del scroll + aire para la última etiqueta de hora

// Color: un único color base para todos los vuelos (los colores por PB de la Suite no significaban nada
// más que «PB distinto»). Los estados se distinguen por color: aviso (ámbar), tramo sin colocar (gris),
// seleccionado (naranja), tramo dentro de un vuelo (rojo discontinuo).
// Desde el giro: cada ángulo tiene su color (clase `ri-g-<tipo>` en App.css, variables --gc*): 0º azul,
// 90º verde, 270º violeta, sin dato ámbar, GENERALES gris. El aviso (ámbar) pisa al color de giro.

// Giro: flechita SVG en la esquina superior derecha del bloque, como EventoVuelo.jsx de la Suite
// (FiRotateCw para 90, FiRotateCcw para 270, mismos paths de Feather; 14 px allí, aquí 18 px).
// Sin giro (0): flecha arriba atenuada, para que todo vuelo muestre algo. Sin dato: «?» ámbar.
// Vuelo GENERALES (flag `generales` del backend): no gira, sin icono (un «?» parecería un fallo).
export function infoGiro(angulo, generales = false) {
  if (generales === true) return { tipo: 'generales', etiqueta: 'Generales, no gira' }
  if (angulo === 0) return { tipo: 'cero', etiqueta: 'Sin giro (0º)' }
  if (angulo === 90) return { tipo: 'cw', etiqueta: 'Gira 90º horario' }
  if (angulo === 270) return { tipo: 'ccw', etiqueta: 'Gira 270º antihorario' }
  return { tipo: 'falta', etiqueta: 'Giro desconocido' }
}

function IconoGiro({ giro, pequeno }) {
  if (giro.tipo === 'generales') return null
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

const altoReal = (seg, altoHora) => Math.max(((seg.finMin - seg.iniMin) / 60) * altoHora, ALTO_MIN_BLOQUE_PX)

function EventoVuelo({ seg, vuelo, desdeMin, altoHora, anchoCol, conSegundos }) {
  const top = ((seg.iniMin - desdeMin) / 60) * altoHora
  const alto = altoReal(seg, altoHora)
  const w = 100 / seg.nCols
  const modo = modoContenido(alto)
  const fmt = formatoBloque(anchoBloquePx(anchoCol, seg.nCols), alto)
  const giro = infoGiro(vuelo.angulo, vuelo.generales)
  // Calendario previo (antes de procesar): aún no hay paridad RGB/T que avisar; el giro se pinta siempre ('?' si no hay dato).
  const motivos = vuelo.previo ? [] : motivosAviso(vuelo)
  const textoMotivos = motivos.join(' · ')
  const claseColor = motivos.length ? 'ri-g-aviso' : `ri-g-${giro.tipo}`
  const cubetas = cubetasPorHora(vuelo)
  const parcial = seg.parte !== 'unico'
  const hora = parcial ? textoHorasBloque('rango', seg.iniMin, seg.finMin) : textoRangoHora(vuelo.inicio, vuelo.fin, seg.iniMin, seg.finMin, conSegundos)
  const fotos = textoFotos(vuelo)
  const personas = [vuelo.equipo, vuelo.piloto].filter(Boolean).join(' · ')
  const tit = [
    etiquetaCompacta(vuelo), vuelo.equipo ? `Dron: ${vuelo.equipo}` : null, vuelo.piloto ? `Piloto: ${vuelo.piloto}` : null,
    hora, fotos, giro.etiqueta, motivos.length ? `Aviso: ${textoMotivos}` : null,
  ].filter(Boolean).join(' · ')
  return (
    <div className={`ri-vuelo ri-${modo} ${claseColor}`} data-testid="ri-vuelo" aria-label={tit} title={tit}
         style={{
           top, height: Math.max(alto - 1, 0), left: `calc(${seg.col * w}% + ${GAP_CARRIL_PX / 2}px)`,
           width: `max(${ANCHO_MIN_BLOQUE_PX}px, calc(${w}% - ${GAP_CARRIL_PX}px))`,
         }}>
      <IconoGiro giro={giro} pequeno={fmt.iconoPequeno || modo !== 'completo'} />
      {modo === 'minimo' ? null : modo === 'compacto' ? (
        <div className="ri-vuelo-tit ri-una-linea">{etiquetaCompacta(vuelo)} · {hora}{motivos.length ? ' ⚠' : ''}</div>
      ) : (
        <>
          <div className="ri-vuelo-tit">{etiquetaCompacta(vuelo)}</div>
          {personas ? <div className="ri-vuelo-equipo" data-testid="ri-vuelo-equipo">{personas}</div> : null}
          <div className="ri-vuelo-hora">
            {hora}
            {motivos.length ? <span className="ri-vuelo-aviso" title={`Aviso: ${textoMotivos}`}> ⚠</span> : null}
          </div>
          {alto >= 70 && motivos.length ? (
            <div className="ri-vuelo-motivo" data-testid="ri-vuelo-motivo" title={`Aviso: ${textoMotivos}`}>⚠ {textoMotivos}</div>
          ) : null}
          {alto >= 58 && fotos ? <div className="ri-vuelo-fotos" data-testid="ri-vuelo-fotos">{fotos}</div> : null}
        </>
      )}
    </div>
  )
}

function BloqueTramo({ seg, vuelos, seleccion, onSeleccion, desdeMin, altoHora, conSegundos }) {
  const t = seg.tramo
  const top = ((seg.iniMin - desdeMin) / 60) * altoHora
  // Los tramos grises nunca bajan de modo «compacto»: siempre llevan texto (qué son y cuántas fotos).
  const alto = Math.max(altoReal(seg, altoHora), ALTO_COMPACTO_PX)
  const modo = modoContenido(alto)
  const w = 100 / seg.nCols
  const dentro = vueloQueContiene(t, vuelos)
  const cls = ['ri-tramo', `ri-${modo}`, t.id === seleccion ? 'act' : '', dentro ? 'aviso' : ''].filter(Boolean).join(' ')
  const txt = textoRangoHora(t.ini, t.fin, t.iniMin, t.finMin, conSegundos)
  const info = `Tramo sin colocar ${txt} · ${t.n} fotos`
  return (
    <button type="button" data-testid="ri-tramo-bloque" className={cls}
            style={{
              top, height: Math.max(alto - 1, 0), left: `calc(${seg.col * w}% + ${GAP_CARRIL_PX / 2}px)`,
              width: `max(${ANCHO_MIN_BLOQUE_PX}px, calc(${w}% - ${GAP_CARRIL_PX}px))`,
            }}
            title={dentro ? `AVISO: cae dentro de PB${dentro.pb}/V${dentro.vuelo} · ${info}` : info}
            aria-label={info}
            onClick={() => onSeleccion?.(t.id === seleccion ? null : t.id)}>
      {modo === 'compacto' ? (
        <span className="ri-tramo-tit ri-una-linea">{dentro ? '⚠ ' : ''}Sin colocar · {txt} · {t.n} fotos</span>
      ) : (
        <>
          <span className="ri-tramo-tit">{dentro ? '⚠ ' : ''}Sin colocar</span>
          <span className="ri-tramo-hora">{txt} · {t.n} fotos</span>
        </>
      )}
    </button>
  )
}

export default function CalendarioHoras({ vuelos, tramos, seleccion, onSeleccion }) {
  const scrollRef = useRef(null)
  const [ancho, setAncho] = useState(0)
  const [altoDisp, setAltoDisp] = useState(0)
  const [scrollX, setScrollX] = useState(0)
  const [zoom, setZoom] = useState(null) // hora entera ampliada, o null = rango completo
  useEffect(() => {
    if (zoom === null) return undefined
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); setZoom(null) } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [zoom])
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return undefined
    const medir = () => { setAncho(el.clientWidth); setAltoDisp(el.clientHeight) }
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
    for (const t of conTramo) {
      const finMin = Math.min(1440, Math.max(t.finMin, t.iniMin + MIN_DURACION_MIN))
      porDia.get(t.fecha).push({ clave: `tramo|${t.id}`, tramo: t, fecha: t.fecha, iniMin: t.iniMin, finMin: t.finMin, parte: 'unico' })
      todos.push({ iniMin: t.iniMin, finMin })
    }
    return { dias, porDia, porClave, hayDatos: todos.length > 0 }
  }, [vuelos, tramos])

  const { dias, porDia, porClave } = calc
  if (!calc.hayDatos) return <p className="ri-vacio">Sin vuelos con hora de cámara.</p>

  const anchoDias = Math.max(ancho, 0)
  const dispBody = altoDisp - ALTO_CABECERA_PX - MARGEN_ALTO_PX
  // Eje POR DÍA: de la primera a la última foto/vuelo de ese día (rangoHoras: margen pequeño / horas enteras),
  // salvo en zoom (misma hora para todos). Cada día se estira para llenar el alto disponible del modal.
  const vistas = dias.map((d) => {
    const segs = porDia.get(d)
    if (!segs.length) return null
    const rango = zoom !== null ? rangoZoomHora(zoom)
      : rangoHoras(segs.map((x) => ({ iniMin: x.iniMin, finMin: Math.min(1440, Math.max(x.finMin, x.iniMin + MIN_DURACION_MIN)) })))
    const spanMin = rango.hastaMin - rango.desdeMin
    const altoHora = altoHoraAjustado(altoHoraPx([segs.filter((x) => !x.tramo)]), dispBody, spanMin)
    return { rango, altoHora, alto: (spanMin / 60) * altoHora }
  })
  const altoVacio = Math.max(0, ...vistas.filter(Boolean).map((v) => v.alto))
  const layouts = dias.map((d, i) => (vistas[i]
    ? layoutDia(porDia.get(d), { minDuracionMin: Math.ceil((ALTO_COMPACTO_PX / vistas[i].altoHora) * 60) }) : []))
  const nTramosDia = layouts.map((l) => l.filter((x) => x.tramo).length)
  const anchos = anchosColumnas(
    layouts.map((l, i) => Math.max(contarVuelosDia(l), nTramosDia[i] ? 1 : 0)),
    anchoDias > 0 ? anchoDias : ANCHO_MIN_DIA_PX * 7, { anchoMinPx: ANCHO_MIN_DIA_PX },
  )
  const inicios = anchos.map((_, i) => anchos.slice(0, i).reduce((a, b) => a + b, 0))
  const maxScroll = anchos.reduce((a, b) => a + b, 0) - anchoDias
  const mover = (dir) => {
    const semanas = inicios.filter((_, i) => i % 7 === 0)
    const destino = dir > 0 ? semanas.find((x) => x > scrollX + 1) : [...semanas].reverse().find((x) => x < scrollX - 1)
    scrollRef.current?.scrollTo?.({ left: destino ?? (dir > 0 ? maxScroll : 0), behavior: 'smooth' })
  }
  const clickCuerpo = (e, v) => {
    if (zoom !== null || !(e.target === e.currentTarget || e.target.classList?.contains('ri-vuelos'))) return
    const y = e.clientY - e.currentTarget.getBoundingClientRect().top
    setZoom(Math.min(23, Math.max(0, Math.floor((v.rango.desdeMin + (y / v.altoHora) * 60) / 60))))
  }

  return (
    <div className="ri-cal" data-testid="ri-calendario">
      <div className="ri-cal-barra">
        <button type="button" className="btn-ghost ri-nav" onClick={() => mover(-1)} disabled={scrollX <= 1} aria-label="Semana anterior">‹</button>
        <button type="button" className="btn-ghost ri-nav" onClick={() => mover(1)} disabled={scrollX >= maxScroll - 1} aria-label="Semana siguiente">›</button>
        {zoom !== null ? (
          <>
            <button type="button" className="btn-ghost ri-nav" onClick={() => setZoom(horaZoomVecina(zoom, -1))} disabled={zoom <= 0} aria-label="Hora anterior">↑ hora</button>
            <button type="button" className="btn-ghost ri-nav" onClick={() => setZoom(horaZoomVecina(zoom, 1))} disabled={zoom >= 23} aria-label="Hora siguiente">↓ hora</button>
            <button type="button" className="btn-ghost ri-nav" data-testid="ri-ver-todo" onClick={() => setZoom(null)}>Ver todo</button>
            <span className="ri-cal-eje">Zoom {String(zoom).padStart(2, '0')}:00-{String(zoom + 1).padStart(2, '0')}:00 (Esc: ver todo)</span>
          </>
        ) : <span className="ri-cal-eje ri-cal-ayuda" title="Eje: hora de cámara (EXIF, sin zona). Pulsa una hora para ampliarla">Hora cámara · pulsa una hora para ampliar</span>}
        <ul className="ri-leyenda" data-testid="ri-leyenda" aria-label="Leyenda"
            title="Color = giro (icono arriba a la derecha: flecha = sin giro, ↻ 90º, ↺ 270º, ? = sin dato). Aviso: «RGB ≠ T» (distinto nº de fotos RGB y T) o «sin giro»">
          <li><i className="ri-ley g-cero" /> 0º</li>
          <li><i className="ri-ley g-cw" /> 90º</li>
          <li><i className="ri-ley g-ccw" /> 270º</li>
          <li><i className="ri-ley g-falta" /> ? sin consenso</li>
          <li><i className="ri-ley g-generales" /> GENERALES</li>
          <li><i className="ri-ley aviso" /> Aviso (RGB ≠ T / sin giro)</li>
          <li><i className="ri-ley tramo" /> Sin colocar</li>
          <li><i className="ri-ley sel" /> Seleccionado</li>
          <li><i className="ri-ley dentro" /> Tramo dentro de vuelo</li>
          <li><i className="ri-ley alta" /> Conf. alta</li>
          <li><i className="ri-ley media" /> media</li>
          <li><i className="ri-ley baja" /> baja</li>
        </ul>
      </div>
      <div ref={scrollRef} className="ri-cal-scroll" onScroll={(e) => setScrollX(e.currentTarget.scrollLeft)}>
        <div className="ri-cal-fila">
          {dias.map((fecha, i) => {
            const segs = layouts[i]
            const nVuelos = contarVuelosDia(segs)
            const vacio = !nVuelos && !nTramosDia[i]
            const vista = vistas[i]
            const { rango, altoHora, alto } = vista ?? {}
            const eje = vista ? marcasEje(rango.desdeMin, rango.hastaMin, altoHora, zoom !== null ? PASOS_EJE_ZOOM : undefined) : null
            // Rayas al paso de las etiquetas, ancladas a la primera marca.
            const pasoPx = vista ? (eje.paso / 60) * altoHora : 0
            const primeraPx = vista ? (((Math.ceil(rango.desdeMin / eje.paso) * eje.paso) - rango.desdeMin) / 60) * altoHora : 0
            return (
              <div key={fecha} className={vacio ? 'ri-dia vacio' : 'ri-dia'} style={{ width: anchos[i] }} data-testid={`ri-dia-${fecha}`}>
                <div className="ri-dia-cab" style={{ height: ALTO_CABECERA_PX }}>
                  {DIAS[i % 7]}
                  <b className={nVuelos ? 'ri-dia-num act' : 'ri-dia-num'}>{Number(fecha.slice(8))}</b>
                  <em>{nVuelos ? `${nVuelos} ${nVuelos === 1 ? 'vuelo' : 'vuelos'}` : 'sin vuelos'}</em>
                </div>
                {vacio ? (
                  <div className="ri-dia-vacio" style={{ height: altoVacio }}><span>sin vuelos</span></div>
                ) : (
                  <div className={zoom === null ? 'ri-dia-cuerpo clic' : 'ri-dia-cuerpo'} onClick={(e) => clickCuerpo(e, vista)} style={{ height: alto, backgroundSize: `100% ${pasoPx}px`, backgroundPosition: `0 ${primeraPx}px` }}>
                    {eje.marcas.map((m) => <span key={m} className={zoom === null ? 'ri-hora clic' : 'ri-hora'} style={{ top: ((m - rango.desdeMin) / 60) * altoHora }}
                          data-testid="ri-hora" {...(zoom === null ? { role: 'button', tabIndex: 0, title: `Ampliar ${String(Math.floor(m / 60) % 24).padStart(2, '0')}h`, onClick: () => setZoom(Math.min(23, Math.floor(m / 60))), onKeyDown: (e) => { if (e.key === 'Enter') setZoom(Math.min(23, Math.floor(m / 60))) } } : {})}>{formatearHora(m)}</span>)}
                    <div className="ri-vuelos" style={{ left: ANCHO_HORAS_PX }}>
                      {segs.map((s) => (s.tramo ? (
                        <BloqueTramo key={s.clave} seg={s} vuelos={vuelos} seleccion={seleccion} onSeleccion={onSeleccion}
                                     desdeMin={rango.desdeMin} altoHora={altoHora} conSegundos={zoom !== null} />
                      ) : (
                        <EventoVuelo key={`${s.clave}|${s.fecha}`} seg={s} vuelo={porClave.get(s.clave)} desdeMin={rango.desdeMin}
                                     altoHora={altoHora} anchoCol={anchos[i] - ANCHO_HORAS_PX} conSegundos={zoom !== null} />
                      )))}
                    </div>
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
