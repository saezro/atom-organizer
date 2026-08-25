import { useState } from 'react'
import PasoCarpeta from './PasoCarpeta'
import PasoInspeccion from './PasoInspeccion'
import PasoEstadillo from './PasoEstadillo'
import PanelOrganizar from './PanelOrganizar'
import PanelSubida from './PanelSubida'

const DESTINOS = [
  { id: 'local', titulo: 'Organizar aquí', detalle: 'Se organiza en este ordenador, en la carpeta que elijas.' },
  { id: 'bucket', titulo: 'Subir al bucket', detalle: 'Las imágenes van a la nube tal cual; se organizan después.' },
  { id: 'nube', titulo: 'Subir y organizar en la nube', detalle: 'Se suben y ATOM las organiza sin ocupar este ordenador.' },
]

export default function TrabajoScreen({ ready, running, onRun, onCloudStatusChange }) {
  const [carpeta, setCarpeta] = useState('')
  const [prefijo, setPrefijo] = useState('')
  const [elegida, setElegida] = useState(null)
  const [estadillo, setEstadillo] = useState({ rutas: [], listo: false, subiendo: false, subir: async () => {} })
  const [destino, setDestino] = useState(null)

  return (
    <div className="card">
      <PasoCarpeta label="Carpeta del vuelo" value={carpeta} onChange={setCarpeta} disabled={running} />
      <PasoInspeccion
        ready={ready}
        prefijo={prefijo}
        onChange={(p, e) => {
          setPrefijo(p)
          setElegida(e)
        }}
        disabled={running || estadillo.subiendo}
      />
      <PasoEstadillo prefijo={prefijo} disabled={running} onEstado={setEstadillo} />

      {carpeta && (
        <div className="field">
          <span className="field-label">¿Qué hacemos con este trabajo?</span>
          <div className="destinos">
            {DESTINOS.map((d) => (
              <button
                key={d.id}
                type="button"
                className={`destino${destino === d.id ? ' destino-activo' : ''}`}
                aria-pressed={destino === d.id}
                onClick={() => setDestino(d.id)}
              >
                <span className="destino-titulo">{d.titulo}</span>
                <span className="destino-detalle">{d.detalle}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {destino === 'local' && (
        <PanelOrganizar origen={carpeta} estadillos={estadillo.rutas} ready={ready} running={running} onRun={onRun} />
      )}

      {(destino === 'bucket' || destino === 'nube') && (
        <PanelSubida
          carpeta={carpeta}
          prefijo={prefijo}
          inspeccionId={elegida?.id}
          estadilloListo={estadillo.listo}
          estadilloSubiendo={estadillo.subiendo}
          subirEstadillo={estadillo.subir}
          ready={ready}
          onCloudStatusChange={onCloudStatusChange}
        />
      )}
    </div>
  )
}
