// Medición de la subida del resultado: serie para la gráfica y diagnóstico del
// cuello de botella. Funciones PURAS (sin React) para poder probarlas con datos
// fabricados. Regla de la casa: solo se afirma lo que el backend mide
// (`cloud_upload.upload_file` → `medidas`); si un dato no llega, no se opina.

export const VENTANA_VELOCIDAD_S = 5      // la velocidad «actual» es la media de los últimos 5 s
export const MAX_MUESTRAS = 600           // tope de la serie; al pasarse se aclara a la mitad
const UMBRAL_MIN_TIEMPO_HILO_S = 5        // por debajo, aún no hay muestra para opinar
const UMBRAL_MIN_BYTES = 50e6

const CAUSAS = ['429', '5xx', 'auth', 'http', 'red']
const sumaReintentos = (m) => CAUSAS.reduce((a, c) => a + Number(m?.reintentos?.[c] || 0), 0)

// Añade una muestra de un evento `estado` a la serie. Devuelve la serie nueva
// (no muta). Ignora eventos sin `t` (backend antiguo) o sin bytes enviados.
// Muestra: {t, bytes, mbs|null, hilos, hilosMax, marcas:[{tipo, n}]}
//   mbs   = MB/s medios en la ventana; null mientras lista/comprueba (no se sube).
//   marcas = reintentos nuevos desde la muestra anterior (por causa) y pausas.
export function añadirMuestra(serie, e) {
  if (!e || typeof e.t !== 'number' || typeof e.bytes_enviados !== 'number') return serie
  const ult = serie[serie.length - 1]
  // Una segunda pasada (urgencia → normal) reinicia `t` y los bytes en el backend:
  // se encadena a continuación de la primera sumando los desplazamientos.
  let offT = ult ? ult.offT : 0
  let offB = ult ? ult.offB : 0
  const nuevaPasada = Boolean(ult) && e.t + 1e-6 < ult.rawT
  if (nuevaPasada) { offT = ult.t; offB = ult.bytes }
  const t = offT + e.t
  const bytes = offB + e.bytes_enviados
  const subiendoAhora = e.fase !== 'listando' && !e.comprobando
  let mbs = null
  if (subiendoAhora) {
    // Primera muestra dentro de la ventana (la más antigua que aún cuenta).
    let ref = null
    for (let i = serie.length - 1; i >= 0; i--) {
      ref = serie[i]
      if (t - serie[i].t >= VENTANA_VELOCIDAD_S) break
    }
    if (ref && t - ref.t > 0.5) mbs = Math.max(0, (bytes - ref.bytes) / (t - ref.t) / 1e6)
    else mbs = ult ? ult.mbs : 0
  }
  const marcas = []
  const m = e.medidas
  if (m && ult && ult.medidas && !nuevaPasada) {
    for (const c of CAUSAS) {
      const d = Number(m.reintentos?.[c] || 0) - Number(ult.medidas.reintentos?.[c] || 0)
      if (d > 0) marcas.push({ tipo: c, n: d })
    }
    // Pausa = tiempo-hilo de espera (backoff + renovación de token) que ha crecido
    // más de 2 s entre dos muestras: hilos parados esperando, no subiendo.
    const dEsp = Number(m.espera_s || 0) + Number(m.token_s || 0) - Number(ult.medidas.espera_s || 0) - Number(ult.medidas.token_s || 0)
    if (dEsp >= 2) marcas.push({ tipo: 'pausa', n: 1, seg: dEsp })
  }
  const nueva = {
    t, bytes, rawT: e.t, offT, offB, mbs,
    hilos: e.hilos_activos ?? null, hilosMax: e.hilos_max ?? null,
    medidas: m || null, marcas,
  }
  let out = [...serie, nueva]
  if (out.length > MAX_MUESTRAS) {
    // Aclarado: se quita una de cada dos de las antiguas, pero las marcas no se pierden
    // (pasan a la muestra que se conserva).
    const mitad = Math.floor(out.length / 2)
    const viejas = out.slice(0, mitad)
    const compactas = []
    for (let i = 0; i < viejas.length; i += 2) {
      const marcasJuntas = [...viejas[i].marcas, ...(viejas[i + 1]?.marcas || [])]
      compactas.push({ ...viejas[i], marcas: marcasJuntas })
    }
    out = [...compactas, ...out.slice(mitad)]
  }
  return out
}

// Estadísticas de la serie para el pie de la gráfica y los KPI.
export function estadisticasSerie(serie) {
  if (!serie.length) return { pico: 0, media: null, actual: null, bytes: 0, t: 0 }
  const ult = serie[serie.length - 1]
  const conBytes = serie.find((s) => s.bytes > 0)
  let media = null
  if (conBytes) {
    // Desde la última muestra SIN bytes (justo antes del primer envío), para no
    // contar el tiempo de listar el bucket como tiempo de subida.
    const i = serie.indexOf(conBytes)
    const t0 = i > 0 ? serie[i - 1].t : 0
    const dt = ult.t - t0
    if (dt > 0.5) media = ult.bytes / dt / 1e6
  }
  const pico = serie.reduce((a, s) => Math.max(a, s.mbs || 0), 0)
  return { pico, media, actual: ult.mbs, bytes: ult.bytes, t: ult.t }
}

// Veredicto del cuello de botella. `medidas` es la acumulada de la pasada.
// Devuelve {clave, titulo, detalle, partes:[{clave,label,pct,s}], advertencia}
//   clave: 'medir' | 'reintentos' | 'red-cortes' | 'disco' | 'sesion' | 'red' | 'mixto'
export function diagnosticarCuello({ medidas, bytes, mbpsMedio, ficheros, hilosMax }) {
  if (!medidas) return null
  const s = {
    red: Number(medidas.red_s || 0),
    disco: Number(medidas.disco_s || 0),
    sesion: Number(medidas.sesion_s || 0),
    espera: Number(medidas.espera_s || 0),
    token: Number(medidas.token_s || 0),
  }
  const total = s.red + s.disco + s.sesion + s.espera + s.token
  const pct = (v) => (total > 0 ? (v / total) * 100 : 0)
  const partes = [
    { clave: 'red', label: 'Enviando por red', s: s.red, pct: pct(s.red) },
    { clave: 'disco', label: 'Leyendo disco', s: s.disco, pct: pct(s.disco) },
    { clave: 'sesion', label: 'Abriendo sesión por fichero', s: s.sesion, pct: pct(s.sesion) },
    { clave: 'espera', label: 'Esperando tras un fallo', s: s.espera, pct: pct(s.espera) },
    { clave: 'token', label: 'Renovando credencial', s: s.token, pct: pct(s.token) },
  ]
  const advertencia = 'Tiempo acumulado de todos los hilos. La lectura de disco puede quedar oculta por la caché del sistema.'
  if (total < UMBRAL_MIN_TIEMPO_HILO_S || Number(bytes || 0) < UMBRAL_MIN_BYTES) {
    return { clave: 'medir', titulo: 'Midiendo…', detalle: 'Hacen falta unos segundos de subida para poder opinar.', partes, advertencia }
  }
  const r = medidas.reintentos || {}
  const nRet = sumaReintentos(medidas)
  const pEsperas = pct(s.espera + s.token)
  if (pEsperas >= 25) {
    const servidor = Number(r['429'] || 0) + Number(r['5xx'] || 0) + Number(r.auth || 0)
    const red = Number(r.red || 0) + Number(r.http || 0)
    const porTokenTxt = s.token > s.espera ? ' (sobre todo renovando la credencial)' : ''
    if (servidor >= red) {
      const causa = Number(r['429'] || 0) >= Math.max(Number(r['5xx'] || 0), Number(r.auth || 0)) ? 'límite de peticiones (429)'
        : Number(r['5xx'] || 0) >= Number(r.auth || 0) ? 'errores del servidor (5xx)' : 'renovaciones de credencial (401/403)'
      return { clave: 'reintentos', titulo: 'Limitado por reintentos del servidor',
        detalle: `Los hilos pasan el ${Math.round(pEsperas)} % del tiempo esperando${porTokenTxt}; ${nRet} reintentos, sobre todo por ${causa}.`, partes, advertencia }
    }
    return { clave: 'red-cortes', titulo: 'Limitado por cortes de red',
      detalle: `Los hilos pasan el ${Math.round(pEsperas)} % del tiempo esperando tras ${nRet} reintentos (cortes o tiempos agotados).`, partes, advertencia }
  }
  if (pct(s.disco) >= 40) {
    return { clave: 'disco', titulo: 'Limitado por disco',
      detalle: `Los hilos pasan el ${Math.round(pct(s.disco))} % del tiempo leyendo del disco (lectura y comprobación MD5).`, partes, advertencia }
  }
  if (pct(s.sesion) >= 30) {
    const medio = ficheros > 0 ? bytes / ficheros / 1e6 : null
    return { clave: 'sesion', titulo: 'Limitado por el trámite de cada fichero',
      detalle: `El ${Math.round(pct(s.sesion))} % del tiempo se va abriendo sesiones de subida${medio != null ? ` (ficheros de ${medio.toFixed(1).replace('.', ',')} MB de media)` : ''}.`, partes, advertencia }
  }
  if (pct(s.red) >= 50) {
    const mbps = mbpsMedio != null ? Math.round(mbpsMedio) : null
    return { clave: 'red', titulo: `Limitado por red${mbps != null ? ` (${mbps} Mbps)` : ''}`,
      detalle: `Los hilos pasan el ${Math.round(pct(s.red))} % del tiempo enviando datos${hilosMax ? ` con ${hilosMax} hilos` : ''}; sin reintentos relevantes. Más velocidad exige más ancho de banda.`, partes, advertencia }
  }
  return { clave: 'mixto', titulo: 'Sin un cuello claro',
    detalle: 'El tiempo se reparte entre varias causas; mira el desglose.', partes, advertencia }
}
