import { Fragment, useState } from 'react'
import { api } from './bridge'
import BotonToque from './pulsacion.jsx'
import BotonMantener from './BotonMantener.jsx'

// Cuántos estadillos caben por página cuando se usa en el kiosco (`tactil`):
// el panel resistivo de la Pi no tiene scroll de página (`.kiosk-estadillo`
// vive dentro de `.kiosk`, que es `overflow:hidden`), así que igual que la
// lista de modelos de `KioskAjustes` la lista se pagina (▲/▼ + N/M, mismo
// patrón que `KioskTareas`) en vez de crecer, para que "Organizar" y la
// barra de progreso de debajo queden siempre alcanzables. En escritorio
// (`tactil` falsy) no cambia nada: se sigue pintando la lista entera.
const ESTADILLOS_POR_PAGINA = 2

// Igual que `ESTADILLOS_POR_PAGINA`: el panel resistivo de la Pi no tiene
// scroll de página (arrastrar con el dedo no genera un gesto de scroll, solo
// toques sueltos — el mismo motivo por el que `KioskTareas`/`KioskAjustes`
// paginan en vez de crecer). El intento anterior de resolver la tarjeta
// «Estadillo recibido» cortada en 480x320 con `overflow-y:auto` +
// `touch-action:pan-y` (`.estad-recibido-cuerpo`) no podía funcionar por eso:
// no hay gesto de arrastre que active ese scroll. Se pagina en su lugar, con
// el mismo patrón `.estad-nav` de la lista de varios ficheros de más abajo.
//
// Subido de 2 a 5 (2026-09-23, queja de Rodrigo: "esta full cortado... no
// se ve nada"): con el selector de carpeta ya fuera de `.kiosk-estadillo`
// mientras se espera/recibe (ver KioskScreen.jsx) la tarjeta dispone de casi
// toda la pantalla, y con 2 filas por página un estadillo de 3 vuelos
// (5 campos + 3 vuelos + total = 9 secciones) necesitaba 5 páginas para leer
// el total. Con 5 caben los campos de detalle juntos y cada página deja de
// sentirse "cortada a media fila".
const RECIBIDO_FILAS_POR_PAGINA = 5

// Nombre a mostrar: solo el fichero, sin la ruta completa (que en escritorio
// puede ser larguísima y desbordar la caja).
function nombreFichero(path) {
  if (!path) return ''
  const partes = String(path).split(/[\\/]/)
  return partes[partes.length - 1] || path
}

// Minutos -> "Xh MMmin" / "MMmin", para el tiempo de vuelo total y la
// duración de cada vuelo de la tarjeta de «Estadillo recibido».
function formatearDuracion(min) {
  const total = Math.max(0, Math.round(min))
  const h = Math.floor(total / 60)
  const m = total % 60
  if (h === 0) return `${m} min`
  return `${h}h ${String(m).padStart(2, '0')}min`
}

// Check verde, mismo trazo que el resto de iconos propios del proyecto (ver
// `IconoCheck` en `EsperaEstadillo.jsx`): sin `react-icons`, cinco iconos no
// merecen la pena en el bundle (ver `NavIcon.jsx`).
function IconoCheckEstadillo() {
  return (
    <svg viewBox="0 0 24 24" width="1.1rem" height="1.1rem" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" />
      <path d="M7.5 12.5l3 3 6-6.5" />
    </svg>
  )
}

// Campo "Estadillo": lista ORDENADA de ficheros CSV/XLSX (antes solo se podía
// elegir uno). Con 0 o 1 fichero se ve y se comporta exactamente igual que el
// campo simple de siempre; la lista con quitar/reordenar solo aparece a partir
// del segundo, para no complicar el caso normal.
//
// El orden importa: aguas abajo la regla es "gana el primero" cuando dos
// estadillos cubren la misma imagen, así que el operador puede reordenarlos.
//
// `value` es siempre un array de rutas (puede estar vacío). `onChange` recibe
// el array nuevo completo (solo se usa para quitar/reordenar, no para
// teclear).
//
// Sin tecleo a mano en ningún lado (pedido de Rodrigo, 2026-09-22): el
// estadillo llega por autodetección en la carpeta elegida
// (`estadillosDetectar`, rellena `value` desde fuera), recibido por red desde
// el portátil (`EsperaEstadillo`, mismo mecanismo) o, SOLO en escritorio
// (`permitirElegir`, decisión de Rodrigo 2026-09-22), eligiendo el fichero a
// mano con el selector nativo (`api.pickFile`). `permitirElegir` lo pasa
// ÚNICAMENTE `PasoEstadillo.jsx`: el kiosco (`tactil`) nunca lo pasa, así que
// el botón «Elegir…» no puede aparecer ahí. Con la lista vacía y sin
// `permitirElegir` no pinta nada (el "sin estadillo" ya lo dice el indicador
// propio de cada pantalla, `kiosk-estadillo-en-carpeta` en el kiosco o el
// aviso de autodetección en `PasoEstadillo`); con uno o más, muestra el/los
// nombre(s) de solo lectura, con quitar/reordenar cuando hay más de uno.
export default function EstadilloField({ value, onChange, disabled, tactil, permitirElegir, infoRecibido }) {
  const files = value || []
  const [pagina, setPagina] = useState(0)

  // Selector nativo con filtro CSV/XLSX (mismo bridge que el resto de
  // pickers, `api.pickFile`). Solo se llama cuando `permitirElegir` (nunca en
  // kiosco). Añade el fichero elegido a la lista; si ya estaba, no lo duplica.
  async function elegir() {
    const path = await api.pickFile('csv_xlsx')
    if (!path || files.includes(path)) return
    onChange([...files, path])
  }

  function quitar(i) {
    onChange(files.filter((_, idx) => idx !== i))
  }

  function mover(i, delta) {
    const j = i + delta
    if (j < 0 || j >= files.length) return
    const next = [...files]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }

  // Sin estadillo: en kiosco (o sin `permitirElegir`) no hay nada que
  // mostrar aquí, el "sin estadillo" ya lo dice el indicador propio de cada
  // pantalla (evita duplicar el mensaje). En escritorio, con `permitirElegir`,
  // se ofrece el botón «Elegir…» para no depender solo de la autodetección.
  if (files.length === 0) {
    if (!permitirElegir) return null
    return (
      <div className="field">
        <span className="field-label">Estadillo *</span>
        <div className="field-row">
          <button type="button" className="btn-ghost" disabled={disabled} onClick={elegir}>
            Elegir…
          </button>
        </div>
      </div>
    )
  }

  // Caso simple (1 fichero) recibido por red: tarjeta de solo lectura con el
  // resumen rico que mandó `EsperaEstadillo` (`onRecibido`, segundo
  // argumento) — nada de mostrar solo el nombre del CSV como si fuera un
  // campo editable (pedido de Rodrigo: tras «OK, seguir» el resumen no debe
  // desaparecer). `quitar(0)` es la MISMA acción que ya existe para la lista
  // de varios ficheros, reusada aquí para «Quitar» — en kiosco exige
  // mantener pulsado (`BotonMantener`), en escritorio es un click normal.
  if (files.length === 1 && infoRecibido) {
    const fechas = infoRecibido.fechas?.length
      ? infoRecibido.fechas
      : infoRecibido.fecha
        ? [infoRecibido.fecha]
        : []
    const pilotos = infoRecibido.pilotos || []
    const drones = infoRecibido.drones || []
    const vuelos = infoRecibido.vuelos || []
    const hayTotal = typeof infoRecibido.tiempoTotalMin === 'number'
    const hayAvisos = infoRecibido.avisos?.length > 0

    // Cada dato es una fila individual (no todo el detalle como un único
    // bloque): con el hueco real que deja el kiosco para esta tarjeta (poco,
    // `.kiosk-estadillo` comparte pantalla con las dos tarjetas grandes de
    // arriba) hasta 4-5 filas de detalle + varios vuelos no caben juntos.
    const camposDetalle = []
    if (infoRecibido.planta) camposDetalle.push({ dt: 'Planta', dd: infoRecibido.planta })
    if (fechas.length > 0) camposDetalle.push({ dt: fechas.length === 1 ? 'Fecha' : 'Fechas', dd: fechas.join(', ') })
    if (pilotos.length > 0) camposDetalle.push({ dt: pilotos.length === 1 ? 'Piloto' : 'Pilotos', dd: pilotos.join(', ') })
    if (drones.length > 0) camposDetalle.push({ dt: drones.length === 1 ? 'Dron' : 'Drones', dd: drones.join(', ') })
    if (typeof infoRecibido.numVuelos === 'number') camposDetalle.push({ dt: 'Vuelos', dd: infoRecibido.numVuelos })

    // El cuerpo (detalle/vuelos/total/avisos) NO cabe entero en 480x320 con
    // varios vuelos y el panel resistivo de la Pi no genera gesto de scroll
    // al arrastrar (solo toques sueltos): un `overflow-y:auto` con
    // `touch-action:pan-y` aquí no tiene nada que lo dispare, por eso la
    // tarjeta salía cortada e infuncional (mismo motivo por el que
    // `ESTADILLOS_POR_PAGINA` pagina en vez de crecer). Se pagina el cuerpo
    // igual que la lista de varios ficheros de más abajo (`.estad-nav`),
    // solo en kiosco: en escritorio sigue viéndose todo de una vez, como
    // siempre. Cada dato (un campo del detalle, un vuelo, el total o los
    // avisos) cuenta como una fila de cara a `RECIBIDO_FILAS_POR_PAGINA`.
    const secciones = []
    camposDetalle.forEach((c) => secciones.push({ tipo: 'campo', c }))
    vuelos.forEach((v, i) => secciones.push({ tipo: 'vuelo', v, i }))
    if (hayTotal) secciones.push({ tipo: 'total' })
    if (hayAvisos) secciones.push({ tipo: 'avisos' })

    const totalPaginasRecibido = tactil
      ? Math.max(1, Math.ceil(secciones.length / RECIBIDO_FILAS_POR_PAGINA))
      : 1
    const paginaSeguraRecibido = Math.min(pagina, totalPaginasRecibido - 1)
    const inicioRecibido = tactil ? paginaSeguraRecibido * RECIBIDO_FILAS_POR_PAGINA : 0
    const finRecibido = tactil ? inicioRecibido + RECIBIDO_FILAS_POR_PAGINA : secciones.length
    const seccionesVisibles = secciones.slice(inicioRecibido, finRecibido)
    const conPaginacionRecibido = tactil && secciones.length > RECIBIDO_FILAS_POR_PAGINA

    // Campos de detalle y vuelos consecutivos de la página actual se
    // agrupan en un único `<dl>`/`<ul>` (mismo testid/estructura que antes
    // cuando cabían todos de una vez).
    const cuerpoNodos = []
    let campoBuffer = []
    let vueloBuffer = []
    const flushCampos = () => {
      if (!campoBuffer.length) return
      cuerpoNodos.push(
        <dl key={'detalle-' + campoBuffer[0].c.dt} className="espera-recibido-detalle">
          {campoBuffer.map(({ c }) => (
            <Fragment key={c.dt}>
              <dt>{c.dt}</dt>
              <dd>{c.dd}</dd>
            </Fragment>
          ))}
        </dl>
      )
      campoBuffer = []
    }
    const flushVuelos = () => {
      if (!vueloBuffer.length) return
      cuerpoNodos.push(
        <ul
          key={'vuelos-' + vueloBuffer[0].i}
          className="estad-recibido-vuelos"
          data-testid="estadillo-recibido-vuelos"
        >
          {vueloBuffer.map(({ v, i }) => (
            <li key={i} className="estad-recibido-vuelo">
              <span>Vuelo {v.n ?? i + 1}</span>
              <span className="espera-mono">
                {v.inicio && v.fin ? `${v.inicio}–${v.fin}` : v.inicio || ''}
                {typeof v.duracionMin === 'number' ? ` · ${formatearDuracion(v.duracionMin)}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )
      vueloBuffer = []
    }
    seccionesVisibles.forEach((s) => {
      if (s.tipo === 'campo') {
        flushVuelos()
        campoBuffer.push(s)
        return
      }
      if (s.tipo === 'vuelo') {
        flushCampos()
        vueloBuffer.push(s)
        return
      }
      flushCampos()
      flushVuelos()
      if (s.tipo === 'total') {
        cuerpoNodos.push(
          <span key="total" className="estad-recibido-total" data-testid="estadillo-recibido-total">
            Tiempo de vuelo total: {formatearDuracion(infoRecibido.tiempoTotalMin)}
          </span>
        )
      } else if (s.tipo === 'avisos') {
        cuerpoNodos.push(
          <div key="avisos" className="field-hint hint-warn espera-errores">
            {infoRecibido.avisos.map((a, i) => (
              <div key={i}>{a}</div>
            ))}
          </div>
        )
      }
    })
    flushCampos()
    flushVuelos()

    return (
      <div className="field">
        <span className="field-label">Estadillo *</span>
        <div className="estad-recibido" data-testid="estadillo-recibido-card">
          <div className="estad-recibido-cabecera">
            <IconoCheckEstadillo />
            <span className="estad-recibido-titulo">Estadillo recibido</span>
          </div>
          {/* Cabecera fija arriba (icono/título, ya fuera de este bloque);
              cuerpo paginado en kiosco (ver `secciones` más arriba) para que
              todo quede alcanzable sin depender de un gesto de arrastre que
              el panel resistivo de la Pi no genera. */}
          <div className="estad-recibido-cuerpo">{cuerpoNodos}</div>
          {/* Paginador + «Quitar» EN LA MISMA FILA (antes cada uno tenía su
              propia fila de `var(--toque-min)`): dentro de `.kiosk-estadillo`
              el hueco real es minúsculo (comparte pantalla con las dos
              tarjetas grandes de "Elegir carpeta"/"Recibir estadillo" de
              arriba, ver `KioskScreen.jsx`) y dos filas de acción entera no
              dejaban nada para el cuerpo. Fija al pie de la tarjeta (fuera de
              `.estad-recibido-cuerpo`, que es lo único que se recorta). */}
          <div className="estad-recibido-pie">
            {conPaginacionRecibido && (
              <div className="estad-nav">
                <BotonToque
                  className="estad-nav-btn"
                  tactil={tactil}
                  disabled={disabled || paginaSeguraRecibido <= 0}
                  onActivar={() => setPagina((p) => Math.max(0, p - 1))}
                  data-testid="estadillo-recibido-pag-arriba"
                  aria-label="Página anterior del estadillo recibido"
                >
                  ▲
                </BotonToque>
                <span className="estad-pagina" data-testid="estadillo-recibido-pagina">
                  {paginaSeguraRecibido + 1}/{totalPaginasRecibido}
                </span>
                <BotonToque
                  className="estad-nav-btn"
                  tactil={tactil}
                  disabled={disabled || paginaSeguraRecibido >= totalPaginasRecibido - 1}
                  onActivar={() => setPagina((p) => Math.min(totalPaginasRecibido - 1, p + 1))}
                  data-testid="estadillo-recibido-pag-abajo"
                  aria-label="Página siguiente del estadillo recibido"
                >
                  ▼
                </BotonToque>
              </div>
            )}
            <BotonMantener
              className="config-del estad-recibido-quitar"
              tactil={tactil}
              title="Quitar este estadillo"
              aria-label="Quitar este estadillo"
              data-testid="estad-recibido-quitar"
              disabled={disabled}
              onActivar={() => quitar(0)}
            >
              Quitar
            </BotonMantener>
          </div>
        </div>
      </div>
    )
  }

  // Caso simple (1 fichero): una sola fila de solo lectura, con «Elegir…»
  // para añadir otro (o sustituir vía «Quitar» + «Elegir…») solo en escritorio.
  if (files.length === 1) {
    return (
      <div className="field">
        <span className="field-label">Estadillo *</span>
        <div className="field-row">
          <span className="glass-input estad-actual" data-testid="estadillo-actual">
            {nombreFichero(files[0])}
          </span>
        </div>
        {permitirElegir && (
          <button
            type="button"
            className="link-inline estad-add"
            disabled={disabled}
            onClick={elegir}
          >
            + Añadir otro estadillo
          </button>
        )}
      </div>
    )
  }

  // Varios ficheros: lista con quitar y reordenar (arriba = mayor prioridad).
  // En kiosco (`tactil`) se pagina para no desbordar `.kiosk-estadillo`; en
  // escritorio se sigue pintando la lista completa, tal cual antes.
  const totalPaginasEstad = Math.max(1, Math.ceil(files.length / ESTADILLOS_POR_PAGINA))
  const paginaSeguraEstad = Math.min(pagina, totalPaginasEstad - 1)
  const inicioEstad = tactil ? paginaSeguraEstad * ESTADILLOS_POR_PAGINA : 0
  const finEstad = tactil ? inicioEstad + ESTADILLOS_POR_PAGINA : files.length
  const filasVisibles = files.map((path, i) => ({ path, i })).slice(inicioEstad, finEstad)
  const conPaginacionEstad = tactil && files.length > ESTADILLOS_POR_PAGINA

  return (
    <div className="field">
      <span className="field-label">Estadillos * (gana el primero de la lista)</span>
      <ul className="estad-list">
        {filasVisibles.map(({ path, i }) => (
          <li key={i} className="estad-item">
            <span className="estad-order" title="Prioridad de este estadillo">
              {i + 1}º
            </span>
            <span className="glass-input estad-nombre" data-testid={`estad-nombre-${i}`}>
              {nombreFichero(path)}
            </span>
            <span className="estad-move">
              <button
                type="button"
                className="estad-arrow"
                disabled={disabled || i === 0}
                title="Subir prioridad"
                onClick={() => mover(i, -1)}
              >
                ▲
              </button>
              <button
                type="button"
                className="estad-arrow"
                disabled={disabled || i === files.length - 1}
                title="Bajar prioridad"
                onClick={() => mover(i, 1)}
              >
                ▼
              </button>
            </span>
            {/* Destructivo: quita el estadillo de la lista sin deshacer. En
                kiosco exige mantener pulsado (BotonMantener); en escritorio
                (tactil falsy) sigue siendo un click normal, como antes. */}
            <BotonMantener
              className="config-del"
              tactil={tactil}
              title="Quitar este estadillo"
              aria-label="Quitar este estadillo"
              data-testid={`estad-quitar-${i}`}
              disabled={disabled}
              onActivar={() => quitar(i)}
            >
              ✕
            </BotonMantener>
          </li>
        ))}
      </ul>
      {conPaginacionEstad && (
        <div className="estad-nav">
          <BotonToque
            className="estad-nav-btn"
            tactil={tactil}
            disabled={disabled || paginaSeguraEstad <= 0}
            onActivar={() => setPagina((p) => Math.max(0, p - 1))}
            data-testid="estad-pag-arriba"
            aria-label="Página anterior de estadillos"
          >
            ▲
          </BotonToque>
          <span className="estad-pagina" data-testid="estad-pagina">
            {paginaSeguraEstad + 1}/{totalPaginasEstad}
          </span>
          <BotonToque
            className="estad-nav-btn"
            tactil={tactil}
            disabled={disabled || paginaSeguraEstad >= totalPaginasEstad - 1}
            onActivar={() => setPagina((p) => Math.min(totalPaginasEstad - 1, p + 1))}
            data-testid="estad-pag-abajo"
            aria-label="Página siguiente de estadillos"
          >
            ▼
          </BotonToque>
        </div>
      )}
      {permitirElegir && (
        <button type="button" className="link-inline estad-add" disabled={disabled} onClick={elegir}>
          + Añadir otro estadillo
        </button>
      )}
      <span className="field-hint">
        Se organiza contra todos a la vez. Si dos estadillos cubren la misma imagen, gana el
        de arriba: reordénalos con las flechas si hace falta.
      </span>
    </div>
  )
}
