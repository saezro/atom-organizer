import { useState } from 'react'
import PasoCarpeta from './PasoCarpeta'
import PasoInspeccion from './PasoInspeccion'
import PasoEstadillo from './PasoEstadillo'
import PanelOrganizar from './PanelOrganizar'
import PanelSubida from './PanelSubida'
import useEstadilloAuto from './useEstadilloAuto'

const ESTADILLO_OMITIDO = { rutas: [], listo: true, subiendo: false, subir: async () => {} }

const DESTINOS = [
  { id: 'local', titulo: 'Organizar aquí', detalle: 'Se organiza en este ordenador, en la carpeta que elijas.' },
  { id: 'bucket', titulo: 'Subir al bucket', detalle: 'Las imágenes van a la nube tal cual; se organizan después.' },
  { id: 'nube', titulo: 'Subir y organizar en la nube', detalle: 'Se suben y ATOM las organiza sin ocupar este ordenador.' },
]

// `destinoInicial` llega desde las cards de «Inicio» (organizar → 'local',
// subir en crudo → 'bucket'). App.jsx remonta el componente con `key`, así que
// basta con usarlo como valor inicial del state.
export default function TrabajoScreen({ ready, running, onRun, onCloudStatusChange, destinoInicial = null, acceso = null }) {
  // `acceso` = `{organizer, estadillos}` (login usuario+contraseña); null = todo.
  const verOrganizer = acceso?.organizer !== false
  const verEstadillos = acceso?.estadillos !== false
  const [carpeta, setCarpeta] = useState('')
  const [prefijo, setPrefijo] = useState('')
  const [elegida, setElegida] = useState(null)
  const [estadillo, setEstadillo] = useState({ rutas: [], listo: false, subiendo: false, subir: async () => {} })
  const [destino, setDestino] = useState(destinoInicial)
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
  // Sin módulo estadillos no hay paso de estadillo: se trata como «subir sin
  // estadillo» (listo, sin ficheros).
  // Para organizar el backend SÍ exige estadillo: sin `PasoEstadillo` se
  // autodetecta en el origen y se muestra el resultado (sin fallback mudo).
  const auto = useEstadilloAuto(carpeta, !verEstadillos)
  const est = verEstadillos ? estadillo : { ...ESTADILLO_OMITIDO, rutas: auto.rutas }

  return (
    <div className="card">
      <PasoCarpeta label="Carpeta del vuelo" value={carpeta} onChange={setCarpeta} disabled={ocupado} />
      <PasoInspeccion
        ready={ready}
        prefijo={prefijo}
        carpeta={carpeta}
        estadilloRutas={est.rutas}
        onChange={(p, e) => {
          setPrefijo(p)
          setElegida(e)
        }}
        disabled={ocupado || est.subiendo}
        reloadToken={inspeccionReloadToken}
      />
      {verEstadillos && (
        <PasoEstadillo
          prefijo={prefijo}
          carpeta={carpeta}
          inspeccion={elegida}
          disabled={ocupado}
          onEstado={setEstadillo}
        />
      )}

      {!verEstadillos && carpeta && auto.estado === 'ok' && (
        <span className="field-hint hint-ok">Estadillo: {auto.rutas.map((r) => r.split(/[\\/]/).pop()).join(', ')}</span>
      )}
      {!verEstadillos && carpeta && auto.estado === 'buscando' && (
        <span className="field-hint">{auto.mensaje || 'Buscando estadillo…'}</span>
      )}
      {!verEstadillos && carpeta && auto.estado === 'nada' && (
        <span className="field-hint hint-warn">No hay estadillo en la carpeta de origen</span>
      )}
      {!verEstadillos && carpeta && auto.estado === 'error' && (
        <span className="field-hint hint-warn">{auto.mensaje}</span>
      )}

      {/* Los destinos se ven SIEMPRE, deshabilitados hasta que haya carpeta:
          si solo aparecían al elegirla, no se entendía que organizar fuese
          una opción (feedback de Rodrigo, 2026-08-28). */}
      {verOrganizer && (<>
      <div className="field">
        <span className="field-label">¿Qué hacemos con este trabajo?</span>
        {!carpeta && (
          <span className="field-hint">Elige antes la carpeta del vuelo.</span>
        )}
        <div className="destinos">
          {DESTINOS.map((d) => (
            <button
              key={d.id}
              type="button"
              className={`destino${destino === d.id ? ' destino-activo' : ''}`}
              aria-pressed={destino === d.id}
              disabled={!carpeta || ocupado}
              onClick={() => setDestino(d.id)}
            >
              <span className="destino-titulo">{d.titulo}</span>
              <span className="destino-detalle">{d.detalle}</span>
            </button>
          ))}
        </div>
      </div>

      {carpeta && destino === 'local' && (
        <PanelOrganizar origen={carpeta} estadillos={est.rutas} inspeccion={elegida} ready={ready} running={running} onRun={onRun} />
      )}

      {carpeta && (destino === 'bucket' || destino === 'nube') && (
        <PanelSubida
          carpeta={carpeta}
          prefijo={prefijo}
          inspeccionId={elegida?.id}
          destino={destino}
          estadilloListo={est.listo}
          estadilloSubiendo={est.subiendo}
          subirEstadillo={est.subir}
          ready={ready}
          onCloudStatusChange={onCloudStatusChange}
          onOcupadoChange={setSubidaOcupada}
          onLoginOk={() => setInspeccionReloadToken((n) => n + 1)}
        />
      )}
      </>)}
    </div>
  )
}
