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
  // Subida en curso dentro de `PanelSubida` (login/preparación/subida en
  // hilo). El original (`BucketScreen`) tenía `busy`/`uploading` en el mismo
  // componente que carpeta/inspección/estadillo y los sumaba todos a un único
  // `ocupado` que deshabilitaba el formulario entero; aquí viven en paneles
  // distintos, así que `PanelSubida` lo reporta hacia arriba.
  const [subidaOcupada, setSubidaOcupada] = useState(false)
  // Se incrementa cuando `PanelSubida` avisa de un login recién hecho, para
  // que `PasoInspeccion` recargue su catálogo sin perder la inspección ya
  // elegida (cambiar de valor basta, no importa a qué).
  const [inspeccionReloadToken, setInspeccionReloadToken] = useState(0)

  const ocupado = running || subidaOcupada

  return (
    <div className="card">
      <PasoCarpeta label="Carpeta del vuelo" value={carpeta} onChange={setCarpeta} disabled={ocupado} />
      <PasoInspeccion
        ready={ready}
        prefijo={prefijo}
        onChange={(p, e) => {
          setPrefijo(p)
          setElegida(e)
        }}
        disabled={ocupado || estadillo.subiendo}
        reloadToken={inspeccionReloadToken}
      />
      <PasoEstadillo prefijo={prefijo} disabled={ocupado} onEstado={setEstadillo} />

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
          onOcupadoChange={setSubidaOcupada}
          onLoginOk={() => setInspeccionReloadToken((n) => n + 1)}
        />
      )}
    </div>
  )
}
