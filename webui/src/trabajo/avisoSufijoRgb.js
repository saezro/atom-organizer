// Normaliza un sufijo/token: sin espacios, mayúsculas y con guion bajo inicial (`z`, `_z`, `_Z` → `_Z`).
export function normToken(t) {
  const s = String(t ?? '').trim().toUpperCase()
  if (!s) return ''
  return s.startsWith('_') ? s : `_${s}`
}

// Aviso (no bloqueante) si el sufijo RGB escrito no existe en la tanda autodetectada.
// Devuelve '' si no aplica.
export function avisoSufijoRgb(sufijo, detected) {
  const tok = normToken(sufijo)
  if (!tok || !detected?.ok || !detected.tokens) return ''
  const tokens = detected.tokens
  if (Number(tokens[tok]) > 0) return ''
  const otros = Object.entries(tokens)
    .filter(([k, n]) => Number(n) > 0 && k !== tok && k !== normToken(detected.thermal))
    .map(([k, n]) => `«${k}» (${n})`)
  const sinTok = `Esta tanda no tiene imágenes «${tok}»`
  return otros.length
    ? `${sinTok}: se procesarán como RGB las ${otros.join(', ')}`
    : `${sinTok}; el resto va a RGB`
}
