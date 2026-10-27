// Arnés visual de la pestaña «Subir resultado» (TrabajoScreen entero) con un bridge
// falso y una subida SIMULADA. Solo para mirar la UI con capturas (`ui-check`/playwright);
// no es producción ni usa datos reales.
//
//   ?escenario=idle       estado del bucket + CTAs, sin subir
//   ?escenario=subiendo   subida a medias con 429 y una pausa
//   ?escenario=disco      subida limitada por disco
//   ?escenario=resumen    subida terminada (tarjeta + desglose + gráfica + cuello)
import { createRoot } from 'react-dom/client'
import '../../src/index.css'
import '../../src/App.css'
import TrabajoScreen from '../../src/trabajo/TrabajoScreen.jsx'

const params = new URLSearchParams(window.location.search)
const escenario = params.get('escenario') || 'idle'

const INSPECCION = { id: 7, prefijo: 'CASTRUM/INSPECCIONES/TERMICA_MODULOS/2026_2', etiqueta: 'CASTRUM · Térmica módulos · 2026_2' }
const respuestas = {
  cloud_inspecciones: { ok: true, origen: 'suite', inspecciones: [INSPECCION] },
  cloud_status: { configured: true, logged_in: true, email: 'rebeca@ejemplo.com', estado: 'ok' },
  resultado_credencial: { ok: true, tipo: 'google', usuario: 'rebeca@ejemplo.com' },
  resultado_prechequeo: { ok: true, checks: [{ id: 'a', titulo: 'Carpeta con imágenes', estado: 'ok', detalle: '' }] },
  folder_is_empty: { empty: true },
  estadillo_existente: { existe: false },
  estadillos_detectar: { rutas: [] },
  pick_folder: '/datos/CASTRUM/RESULTADO',
  inspeccion_sugerir: { estado: 'unica', prefijo: INSPECCION.prefijo },
  resultado_inspeccionar_destino: {
    ok: true, existe: true, n_objetos: 8600, bytes: 9.1e9, sobrescribe: [], prefijo: INSPECCION.prefijo,
    desglose: {
      urgentes: { total: 6122, en_bucket: 6122, pendientes: 0, distintos: 0, bytes_pendientes: 0 },
      resto: { total: 12000, en_bucket: 5900, pendientes: 6100, distintos: 3, bytes_pendientes: 14.6e9 },
    },
  },
}
// Inspección de destino en hilo: devuelve {started} y empuja el resultado por atom:resultado con el token.
const inspeccionarStart = async (_destino, _inspeccionId, token) => {
  setTimeout(() => window.dispatchEvent(new CustomEvent('atom:resultado', {
    detail: { ...respuestas.resultado_inspeccionar_destino, kind: 'destino', token },
  })), 150)
  return { started: true }
}
window.pywebview = {
  api: new Proxy({ __vivo: () => true, resultado_inspeccionar_destino_start: inspeccionarStart }, {
    get: (t, k) => (k in t ? t[k] : async () => (k in respuestas ? respuestas[k] : { ok: true })),
  }),
}

const med = (o = {}) => ({ disco_s: 0, red_s: 0, sesion_s: 0, espera_s: 0, token_s: 0, reintentos: { '429': 0, '5xx': 0, auth: 0, http: 0, red: 0 }, ...o })
const emit = (d) => window.dispatchEvent(new CustomEvent('atom:resultado', { detail: d }))

// Velocidad (MB/s) a lo largo de la subida simulada: calentamiento, meseta con ruido, valle por 429.
function mbs(t, esc) {
  const base = esc === 'disco' ? 11 : 24
  const rampa = Math.min(1, t / 20)
  const valle = esc === 'subiendo' && t > 170 && t < 200 ? 0.15 : 1
  return base * rampa * valle * (1 + 0.12 * Math.sin(t / 7) + 0.05 * Math.sin(t / 2.3))
}

function simular(esc, hasta, final) {
  emit({ kind: 'start', modo: 'normal' })
  const totalB = 24e9
  const prefix = INSPECCION.prefijo + '/'
  emit({ kind: 'estado', fase: 'listando', modo: 'normal', prefix, t: 1, bytes_enviados: 0, bytes_done: 0, bytes_total: totalB, urgentes: { hechos: 0, total: 6122 }, resto: { hechos: 0, total: 6000 }, medidas: med(), hilos_max: 16, hilos_activos: 0 })
  let bytes = 0
  const m = med()
  for (let t = 5; t <= hasta; t += 2) {
    bytes += mbs(t, esc) * 1e6 * 2
    const f = Math.min(1, bytes / totalB)
    if (esc === 'disco') { m.disco_s += 2 * 16 * 0.55; m.red_s += 2 * 16 * 0.38; m.sesion_s += 2 * 16 * 0.05 }
    else { m.red_s += 2 * 16 * 0.86; m.disco_s += 2 * 16 * 0.05; m.sesion_s += 2 * 16 * 0.04 }
    if (esc === 'subiendo' && t > 170 && t < 200) { m.espera_s += 2 * 16 * 0.5; m.reintentos['429'] += 6; m.token_s += 2 * 3 }
    emit({
      kind: 'estado', fase: f < 0.5 ? 'urgentes' : 'resto', modo: 'normal', prefix, t, bytes_enviados: bytes, bytes_done: bytes,
      bytes_total: totalB, eta: ((totalB - bytes) / (mbs(t, esc) * 1e6)) || null, mbps: (bytes / t) * 8 / 1e6, files_per_s: (bytes / t) / 2e6,
      urgentes: { hechos: Math.round(6122 * Math.min(1, f * 2)), total: 6122 },
      resto: { hechos: Math.round(6000 * Math.max(0, f * 2 - 1)), total: 6000 },
      hilos_activos: 15, hilos_max: 16, medidas: JSON.parse(JSON.stringify(m)),
    })
  }
  if (final) {
    const g = { omitidos: 0, pendientes: 0, bytes_omitidos: 0, por_tipo: {}, vuelos: 28 }
    emit({
      kind: 'done', ok: true, modo: 'normal', continua: false, prefix, urgentes: { hechos: 6122, total: 6122 }, resto: { hechos: 6000, total: 6000 },
      conflictos: [], fallidas: [], avisos: [], metricas: {
        duracion_s: 1052, bytes_enviados: 23.7e9, hilos_max: 16, conflictos_n: 0, medidas: m,
        urgentes: { ...g, subidos: 6122, bytes_subidos: 11.2e9, subidos_por_tipo: { tiff_termica: { ficheros: 6000, bytes: 11.1e9 }, csv: { ficheros: 122, bytes: 1e8 } }, subidos_por_vuelo: Object.fromEntries(Array.from({ length: 28 }, (_, i) => [`PB${1 + (i % 4)}_V${1 + Math.floor(i / 4)}`, { ficheros: 218 + i, bytes: (0.4 + (i % 5) * 0.05) * 1e9 }])) },
        resto: { ...g, subidos: 6000, bytes_subidos: 12.5e9, subidos_por_tipo: { termica_jpg: { ficheros: 3000, bytes: 1.5e9 }, rgb: { ficheros: 3000, bytes: 11e9 } }, subidos_por_vuelo: Object.fromEntries(Array.from({ length: 28 }, (_, i) => [`PB${1 + (i % 4)}_V${1 + Math.floor(i / 4)}`, { ficheros: 214, bytes: 0.45e9 }])) },
      },
    })
  }
}

createRoot(document.getElementById('root')).render(
  <div className="app" style={{ padding: '1.5rem', margin: '0 auto' }}>
    <TrabajoScreen ready running={false} onRun={() => {}} />
  </div>,
)

// Entra en la pestaña, elige carpeta e inspección y lanza el escenario.
async function guion() {
  const esperar = (ms) => new Promise((r) => setTimeout(r, ms))
  await esperar(400)
  Array.from(document.querySelectorAll('[role=tab]')).find((b) => /Subir resultado/.test(b.textContent))?.click()
  await esperar(300)
  Array.from(document.querySelectorAll('#panel-subir button')).find((b) => /Elegir/i.test(b.textContent))?.click()
  await esperar(600)
  if (escenario === 'subiendo') simular('subiendo', 250, false)
  if (escenario === 'disco') simular('disco', 200, false)
  if (escenario === 'resumen') simular('resumen', 1050, true)
  document.body.dataset.listo = '1'
}
guion()
