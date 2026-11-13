// Ajuste «Esperar mi respuesta tras el índice (min)». Vive en el front (localStorage) y viaja en
// `params.revision_indice.espera_min` al lanzar el run: `Config.ini` no se toca porque
// `write_config` lo reescribe entero. Sin valor guardado: 30 min. Valor ilegible: error visible.
export const CLAVE_ESPERA_REVISION = 'atom.revision_indice.espera_min'
export const ESPERA_REVISION_DEFECTO_MIN = 30

export function validarEsperaMin(texto) {
  const s = String(texto ?? '').trim()
  const n = Number(s)
  if (s === '' || !Number.isFinite(n) || n <= 0 || n > 24 * 60) {
    throw new Error(`Espera inválida: ${JSON.stringify(texto)} (minutos, mayor que 0 y como mucho 1440).`)
  }
  return n
}

export function leerEsperaMin() {
  let bruto = null
  try { bruto = localStorage.getItem(CLAVE_ESPERA_REVISION) } catch { bruto = null }
  if (bruto == null) return ESPERA_REVISION_DEFECTO_MIN
  return validarEsperaMin(bruto)
}

export function guardarEsperaMin(texto) {
  const n = validarEsperaMin(texto)
  localStorage.setItem(CLAVE_ESPERA_REVISION, String(n))
  return n
}

// Valor que el usuario guardó explícitamente: `{min: null}` si no hay ninguno (no se envía nada),
// `{error}` si está corrupto. No lanza: quien lo llama decide cómo avisar.
export function esperaGuardada() {
  let bruto = null
  try { bruto = localStorage.getItem(CLAVE_ESPERA_REVISION) } catch { bruto = null }
  if (bruto == null) return { min: null, error: null }
  try { return { min: validarEsperaMin(bruto), error: null } } catch (e) { return { min: null, error: e.message } }
}
