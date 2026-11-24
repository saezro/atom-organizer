import { useCallback, useState } from 'react'
import PasoCarpeta from './PasoCarpeta'
import PasoInspeccion from './PasoInspeccion'
import PasoEstadillo from './PasoEstadillo'
import PanelOrganizar from './PanelOrganizar'
import ColaOrganizar from './ColaOrganizar'
import PanelSubida from './PanelSubida'
import PanelResultado from './PanelResultado'
import useEstadilloAuto from './useEstadilloAuto'

const ESTADILLO_OMITIDO = { rutas: [], listo: true, subiendo: false, subir: async () => {} }

// `destinoInicial` ('local' | 'bucket' | 'nube') solo preselecciona la pestaña:
// 'bucket' abre «Subir sin organizar»; 'nube' (subir y organizar en la nube) abre esa
// misma pestaña con la casilla «Organizar después en la nube» marcada.
export default function TrabajoScreen({ ready, running, onRun, onCloudStatusChange, destinoInicial = null, acceso = null }) {
  // `acceso` = `{organizer, estadillos}` (login usuario+contraseña); null = todo.
  const verOrganizer = acceso?.organizer !== false
  const verEstadillos = acceso?.estadillos !== false
  const [carpeta, setCarpeta] = useState('')
  const [prefijo, setPrefijo] = useState('')
  const [elegida, setElegida] = useState(null)
  const [estadillo, setEstadillo] = useState({ rutas: [], listo: false, subiendo: false, subir: async () => {} })
  // Casilla de «Subir sin organizar»: encadena la organización en la nube al
  // terminar la subida (antes era el tercer destino, 'nube').
  const [organizarNube, setOrganizarNube] = useState(destinoInicial === 'nube')
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

  // Pestañas: «Organizar» (carpeta → inspección → estadillo → organizar aquí), «Subir sin
  // organizar» (mismo formulario, las imágenes van a la nube tal cual) y «Subir resultado»
  // (subir al bucket la carpeta ya organizada, venga de organizar aquí o de antes).
  const [pestana, setPestana] = useState(destinoInicial === 'bucket' || destinoInicial === 'nube' ? 'sinorg' : 'organizar')
  // Carpeta organizada que ofrece la pestaña Organizar (su «Carpeta final») y la que el
  // usuario fije a mano en Subir resultado (manda sobre la anterior).
  const [destinoOrganizado, setDestinoOrganizado] = useState('')
  const [carpetaSalida, setCarpetaSalida] = useState('')
  // Inspección de la pestaña Subir: sigue a la elegida en Organizar mientras nadie la toque aquí;
  // si el usuario la elige en esta pestaña manda la suya (subir algo ya organizado antes, sin
  // pasar por Organizar).
  const [inspSub, setInspSub] = useState(null) // null | {prefijo, elegida} elegida a mano en Subir
  const nuevoDestino = useCallback((d) => { setDestinoOrganizado(d); setCarpetaSalida('') }, [])
  // Actividad de la subida del resultado, para el punto/porcentaje de la pestaña.
  const [actividad, setActividad] = useState({ subiendo: false, pct: 0, error: false })

  const ocupado = running || subidaOcupada
  // Modo cola: mientras corre un run se puede preparar OTRO trabajo (carpeta, inspección,
  // estadillo, destino) y añadirlo a la cola; solo la subida en curso bloquea el formulario.
  const ocupadoForm = subidaOcupada
  // Sin módulo estadillos no hay paso de estadillo: se trata como «subir sin
  // estadillo» (listo, sin ficheros).
  // Para organizar el backend SÍ exige estadillo: sin `PasoEstadillo` se
  // autodetecta en el origen y se muestra el resultado (sin fallback mudo).
  const auto = useEstadilloAuto(carpeta, !verEstadillos)
  const est = verEstadillos ? estadillo : { ...ESTADILLO_OMITIDO, rutas: auto.rutas }

  const carpetaResultado = carpetaSalida || destinoOrganizado
  const inspResultado = inspSub ? inspSub.elegida : elegida
  const subiendoResultado = actividad.subiendo

  return (
    <div className="card">
      {verOrganizer && (
        <nav className="seg seg-sub" role="tablist" aria-label="Trabajo">
          <button type="button" role="tab" id="tab-organizar" aria-controls="panel-trabajo"
            aria-selected={pestana === 'organizar'} className={'seg-btn' + (pestana === 'organizar' ? ' active' : '')}
            onClick={() => setPestana('organizar')}>
            Organizar
          </button>
          <button type="button" role="tab" id="tab-sinorg" aria-controls="panel-trabajo"
            aria-selected={pestana === 'sinorg'} className={'seg-btn' + (pestana === 'sinorg' ? ' active' : '')}
            onClick={() => setPestana('sinorg')}>
            Subir sin organizar
          </button>
          <button type="button" role="tab" id="tab-subir" aria-controls="panel-subir"
            aria-selected={pestana === 'subir'} className={'seg-btn' + (pestana === 'subir' ? ' active' : '')}
            onClick={() => setPestana('subir')}>
            Subir resultado
            {subiendoResultado && (
              <>
                <span className="seg-punto" aria-hidden="true" />
                <span className="seg-pct" aria-label={`Subiendo, ${actividad.pct} por ciento`}>{actividad.pct} %</span>
              </>
            )}
            {!subiendoResultado && actividad.error && <span className="seg-punto seg-punto-error" role="img" aria-label="Error en la última subida" />}
          </button>
        </nav>
      )}

      {/* Un solo formulario (carpeta, inspección, estadillo) para «Organizar» y «Subir sin organizar». */}
      <div role="tabpanel" id="panel-trabajo" aria-labelledby={pestana === 'sinorg' ? 'tab-sinorg' : 'tab-organizar'} hidden={verOrganizer && pestana === 'subir'} className="trabajo-panel">
      <PasoCarpeta label="Carpeta del vuelo" value={carpeta} onChange={setCarpeta} disabled={ocupadoForm} />
      <PasoInspeccion
        ready={ready}
        prefijo={prefijo}
        carpeta={carpeta}
        estadilloRutas={est.rutas}
        onChange={(p, e) => {
          setPrefijo(p)
          setElegida(e)
        }}
        disabled={ocupadoForm || est.subiendo}
        reloadToken={inspeccionReloadToken}
      />
      {verEstadillos && (
        <PasoEstadillo
          prefijo={prefijo}
          carpeta={carpeta}
          inspeccion={elegida}
          disabled={ocupadoForm}
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

      {verOrganizer && (<>
      {pestana === 'organizar' && !carpeta && (
        <span className="field-hint">Elige antes la carpeta del vuelo.</span>
      )}

      {carpeta && pestana === 'organizar' && (
        <PanelOrganizar
          origen={carpeta} estadillos={est.rutas} inspeccion={elegida} ready={ready} running={running} onRun={onRun}
          onDestino={nuevoDestino}
        />
      )}

      {pestana === 'organizar' && <ColaOrganizar />}

      {carpeta && pestana === 'sinorg' && (
        <label className="check">
          <input
            type="checkbox"
            checked={organizarNube}
            disabled={ocupadoForm}
            onChange={(e) => setOrganizarNube(e.target.checked)}
          />
          <span>Organizar después en la nube</span>
        </label>
      )}

      {carpeta && pestana === 'sinorg' && (
        <PanelSubida
          carpeta={carpeta}
          prefijo={prefijo}
          inspeccionId={elegida?.id}
          destino={organizarNube ? 'nube' : 'bucket'}
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

      {/* Siempre montada (oculta si no es su pestaña): conserva la gráfica y recibe la subida
          automática que se lanza al terminar de organizar aunque no se haya abierto. */}
      {verOrganizer && (
        <div role="tabpanel" id="panel-subir" aria-labelledby="tab-subir" hidden={pestana !== 'subir'} className="trabajo-panel">
          {pestana === 'subir' && (
            <>
              <PasoCarpeta label="Carpeta del resultado organizado" value={carpetaResultado} onChange={setCarpetaSalida} disabled={ocupado || subiendoResultado} />
              <PasoInspeccion
                ready={ready}
                prefijo={inspSub ? inspSub.prefijo : prefijo}
                carpeta={carpetaResultado}
                estadilloRutas={[]}
                onChange={(p, e) => setInspSub({ prefijo: p, elegida: e })}
                disabled={ocupado || subiendoResultado}
                reloadToken={inspeccionReloadToken}
              />
            </>
          )}
          <PanelResultado
            carpeta={carpetaResultado}
            inspeccionId={inspResultado?.id ?? null}
            ready={ready}
            variante={destinoOrganizado && carpetaResultado === destinoOrganizado ? 'tras-organizar' : 'ya-organizado'}
            onActividad={setActividad}
          />
        </div>
      )}
    </div>
  )
}
