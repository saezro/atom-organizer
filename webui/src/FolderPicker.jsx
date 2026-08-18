import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './bridge.js'

// Sustituye al dialogo nativo de ficheros, que solo existia via Qt/pywebview.
// Aparte de no estar disponible en modo servidor (Raspberry Pi), un dialogo
// nativo en una pantalla de 480x320 manejada con el dedo seria inutilizable
// de todas formas: aqui las filas son objetivos de toque grandes y la
// navegacion es un nivel de carpeta cada vez.
//
// Contrato real de `api.listDir` (Task 7, `Api.list_dir` en app_webview.py):
//   exito -> {ok:true, path, parent: string|null, dirs:[{name,path}],
//             files:[{name,path,size}]}
//   error -> {ok:false, error}
// `parent` es null en la raiz. Sin argumento lista el home del usuario.

// Los iconos van en SVG y no en emoji a proposito: la Pi no tiene fuente de
// emoji instalada (y meterla exige sudo, que no tenemos), asi que 📁/📄 salian
// como el cuadrado del glifo ausente.
function Ico({ tipo }) {
  const comun = {
    className: 'picker-ico', width: '1em', height: '1em', viewBox: '0 0 16 16',
    fill: 'none', stroke: 'currentColor', strokeWidth: '1.4',
    strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
  }
  if (tipo === 'subir') {
    return <svg {...comun}><path d="M8 13V3M8 3 4 7M8 3l4 4" /></svg>
  }
  if (tipo === 'fichero') {
    return <svg {...comun}><path d="M9.5 2H4.5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V5zM9.5 2v3h3" /></svg>
  }
  return <svg {...comun}><path d="M2 12.5v-9a1 1 0 0 1 1-1h3l1.5 2H13a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z" /></svg>
}

// El panel resistivo de la Pi se presenta por evdev como puntero absoluto, no
// como touchscreen: Chromium recibe eventos de raton, asi que deslizar el dedo
// sobre la lista no hace scroll (arrastra). Se emula a mano moviendo scrollTop
// con los pointer events, y se cancela el click si el dedo llego a arrastrar,
// para no navegar a una carpeta al soltar.
const UMBRAL_REM = 0.625

export default function FolderPicker({ mode = 'folder', startPath = null, onPick, onCancel }) {
  const [estado, setEstado] = useState({ cargando: true, datos: null, error: null })
  const listaRef = useRef(null)
  const arrastre = useRef({ activo: false, y0: 0, top0: 0, umbral: 10, movido: false })

  const cargar = useCallback(async (ruta) => {
    setEstado((s) => ({ ...s, cargando: true, error: null }))
    try {
      const datos = await api.listDir(ruta)
      if (!datos.ok) {
        setEstado({ cargando: false, datos: null, error: datos.error })
        return
      }
      setEstado({ cargando: false, datos, error: null })
    } catch (e) {
      setEstado({ cargando: false, datos: null, error: String(e.message || e) })
    }
  }, [])

  useEffect(() => { cargar(startPath) }, [cargar, startPath])

  const alPulsar = (e) => {
    const ul = listaRef.current
    if (!ul) return
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    arrastre.current = {
      activo: true, y0: e.clientY, top0: ul.scrollTop,
      umbral: UMBRAL_REM * rem, movido: false,
    }
  }

  const alMover = (e) => {
    const a = arrastre.current
    const ul = listaRef.current
    if (!a.activo || !ul) return
    const dy = e.clientY - a.y0
    if (!a.movido && Math.abs(dy) < a.umbral) return
    a.movido = true
    ul.scrollTop = a.top0 - dy
  }

  const alSoltar = () => { arrastre.current.activo = false }

  // En captura, antes de que el click llegue a la fila: si hubo arrastre no es
  // un toque, es un scroll.
  const alHacerClick = (e) => {
    if (!arrastre.current.movido) return
    arrastre.current.movido = false
    e.preventDefault()
    e.stopPropagation()
  }

  const { cargando, datos, error } = estado

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card picker-card">
        <h2 className="pm-title">
          {mode === 'file' ? 'Elegir fichero' : 'Elegir carpeta'}
        </h2>
        <div className="picker-ruta" title={datos?.path || ''}>{datos?.path || '…'}</div>

        {error && <p className="pm-status err">{error}</p>}

        <ul
          className="picker-lista"
          ref={listaRef}
          onPointerDown={alPulsar}
          onPointerMove={alMover}
          onPointerUp={alSoltar}
          onPointerCancel={alSoltar}
          onPointerLeave={alSoltar}
          onClickCapture={alHacerClick}
        >
          {datos?.parent && (
            <li>
              <button type="button" className="picker-fila" onClick={() => cargar(datos.parent)}>
                <Ico tipo="subir" /> .. subir
              </button>
            </li>
          )}
          {datos?.dirs.map((d) => (
            <li key={d.path}>
              <button type="button" className="picker-fila picker-dir" onClick={() => cargar(d.path)}>
                <Ico tipo="carpeta" /> {d.name}
              </button>
            </li>
          ))}
          {mode === 'file' && datos?.files.map((f) => (
            <li key={f.path}>
              <button type="button" className="picker-fila picker-file" onClick={() => onPick(f.path)}>
                <Ico tipo="fichero" /> {f.name}
              </button>
            </li>
          ))}
          {datos && datos.dirs.length === 0 && !(mode === 'file' && datos.files.length > 0) && (
            <li className="picker-vacio">Carpeta vacía.</li>
          )}
        </ul>

        {cargando && <p className="pm-status">Cargando…</p>}

        <div className="pm-actions picker-acciones">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancelar
          </button>
          {mode === 'folder' && (
            <button
              type="button"
              className="btn-run"
              disabled={!datos}
              onClick={() => onPick(datos.path)}
            >
              Usar esta carpeta
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
