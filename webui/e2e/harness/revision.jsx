// Arnés visual del calendario de revisión del índice (escritorio) con API falsa. Solo para mirar
// la UI con capturas; datos de ejemplo de la planta PRUEBA. No toca App.jsx ni el backend.
//
//   ?escenario=esperando     cuenta atrás «se continuará solo en 9:41 si no tocas nada»
//   ?escenario=desatendida   deadline vencido: aviso de lo aplicado y lo que queda en SIN_ORDENAR
//   ?escenario=sin_pendientes  sin libres ni recomendaciones
//   ?escenario=muchas        2.000 fotos libres (rendimiento: cubetas por hora en vuelos)
//   &fallo=1                 la API falsa devuelve {ok:false, error}
import { createRoot } from 'react-dom/client'
import '../../src/index.css'
import '../../src/App.css'
import RevisionIndiceModal from '../../src/RevisionIndiceModal.jsx'
import { modeloDesdePropuesta } from '../../src/calendarioHoras.js'
import propuesta from './fixtures/propuesta-revision.json'

const params = new URLSearchParams(window.location.search)
const escenario = params.get('escenario') || 'esperando'
const fallo = params.get('fallo') === '1'

const modelo = modeloDesdePropuesta(propuesta)
const AHORA = Date.now()

if (escenario === 'esperando') {
  // En la espera, la recomendación alta todavía está pendiente de aceptar.
  modelo.recomendaciones = modelo.recomendaciones.map((r) => ({ ...r, aplicada: false }))
  modelo.espera = { ...modelo.espera, estado: 'esperando', deadline_epoch: AHORA + (9 * 60 + 41) * 1000 }
} else if (escenario === 'desatendida') {
  modelo.libres = modelo.libres.filter((f) => f.grupo_id !== 'g1')
  modelo.espera = { ...modelo.espera, estado: 'desatendida', deadline_epoch: AHORA - 1000 }
} else if (escenario === 'sin_pendientes') {
  modelo.libres = []
  modelo.recomendaciones = []
  modelo.espera = null
} else if (escenario === 'muchas') {
  // 2.000 libres repartidas por los huecos entre vuelos (nunca dentro de un vuelo).
  const huecos = [['2026-05-04', 6 * 60, 8 * 60 + 50], ['2026-05-04', 12 * 60 + 10, 13 * 60 + 50], ['2026-05-04', 14 * 60 + 50, 17 * 60], ['2026-05-05', 7 * 60, 9 * 60 + 20], ['2026-05-05', 10 * 60 + 55, 13 * 60]]
  const extra = []
  for (let i = 0; i < 2000; i++) {
    const [dia, a, b] = huecos[i % huecos.length]
    const seg = a * 60 + Math.floor(((i * 7919) % 1000) / 1000 * (b - a) * 60)
    const ts = `${dia}T${String(Math.floor(seg / 3600)).padStart(2, '0')}:${String(Math.floor((seg % 3600) / 60)).padStart(2, '0')}:${String(seg % 60).padStart(2, '0')}`
    extra.push({ clave: `PRUEBA/m/IMG_${i}.jpg`, nombre: `IMG_${i}.jpg`, tipo: i % 2 ? 'TERMICA' : 'RGB', ts, grupo_id: `m${i % huecos.length}`, motivo: 'fuera-de-ventana' })
  }
  modelo.libres = extra
  modelo.espera = { ...modelo.espera, estado: 'esperando', deadline_epoch: AHORA + 29 * 60 * 1000 }
}

const ok = async (nombre, ...args) => {
  console.log('[api falsa]', nombre, args)
  return fallo ? { ok: false, error: 'no se pudo asignar: grupo inexistente' } : { ok: true }
}
const api = {
  latido: () => console.log('[api falsa] latido'),
  aceptar: (ids) => ok('aceptar', ids),
  asignar: (claves, fila) => ok('asignar', claves.length, fila),
  continuar: (modo) => ok('continuar', modo),
  cancelar: () => ok('cancelar'),
}

createRoot(document.getElementById('root')).render(
  <div className="app" style={{ minHeight: '100vh' }}>
    <RevisionIndiceModal modelo={modelo} api={api} />
  </div>,
)
document.body.dataset.listo = '1'
