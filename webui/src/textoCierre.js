// Texto del estado final cuando el run terminó con avisos o fallos. Puro: lo
// usa ProgressModal y se prueba aparte. El tiempo y la lista de CSV los añade el JSX.
const plural = (n, uno, varios) => (n === 1 ? uno : varios)

function cola(detalle) {
  if (!detalle || !detalle.length) return ''
  const primeros = detalle.slice(0, 3).join(' | ')
  const resto = detalle.length > 3 ? ` (+${detalle.length - 3} más)` : ''
  return `: ${primeros}${resto}`
}

export function textoTerminadoConProblemas(finished) {
  if (finished.kind === 'warning') {
    const n = finished.warnings
    const cuenta = n ? `${n} ${plural(n, 'aviso', 'avisos')}` : 'avisos'
    return `⚠ Terminado con ${cuenta}${cola(finished.detalle_avisos)}`
  }
  const n = finished.errors
  return `✗ Terminado con ${n} ${plural(n, 'fallo', 'fallos')}${cola(finished.detalle_errores)}`
}
