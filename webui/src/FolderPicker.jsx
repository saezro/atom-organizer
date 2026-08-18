import { useCallback, useEffect, useState } from 'react'
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
export default function FolderPicker({ mode = 'folder', startPath = null, onPick, onCancel }) {
  const [estado, setEstado] = useState({ cargando: true, datos: null, error: null })

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

  const { cargando, datos, error } = estado

  return (
    <div className="pm-overlay" role="dialog" aria-modal="true">
      <div className="pm-card picker-card">
        <h2 className="pm-title">
          {mode === 'file' ? 'Elegir fichero' : 'Elegir carpeta'}
        </h2>
        <div className="picker-ruta" title={datos?.path || ''}>{datos?.path || '…'}</div>

        {error && <p className="pm-status err">{error}</p>}

        <ul className="picker-lista">
          {datos?.parent && (
            <li>
              <button type="button" className="picker-fila" onClick={() => cargar(datos.parent)}>
                <span className="picker-ico" aria-hidden="true">↑</span> .. subir
              </button>
            </li>
          )}
          {datos?.dirs.map((d) => (
            <li key={d.path}>
              <button type="button" className="picker-fila picker-dir" onClick={() => cargar(d.path)}>
                <span className="picker-ico" aria-hidden="true">📁</span> {d.name}
              </button>
            </li>
          ))}
          {mode === 'file' && datos?.files.map((f) => (
            <li key={f.path}>
              <button type="button" className="picker-fila picker-file" onClick={() => onPick(f.path)}>
                <span className="picker-ico" aria-hidden="true">📄</span> {f.name}
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
