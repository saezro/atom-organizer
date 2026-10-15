// Texto del estado final cuando el run terminó con avisos o fallos. Puro: lo
// usa ProgressModal y se prueba aparte. El tiempo y la lista de CSV los añade el JSX.
const plural = (n, uno, varios) => (n === 1 ? uno : varios)

// `total` = nº real de entradas (el detalle llega truncado por el backend).
function cola(detalle, total) {
  if (!detalle || !detalle.length) return ''
  const primeros = detalle.slice(0, 3).join(' | ')
  const n = Math.max(Number(total) || 0, detalle.length)
  const resto = n > 3 ? ` (+${n - 3} más)` : ''
  return `: ${primeros}${resto}`
}

export function textoTerminadoConProblemas(finished) {
  if (finished.kind === 'warning') {
    // `warnings` cuenta bloques de aviso del log; si llega el total del detalle, es el real.
    const n = finished.n_detalle_avisos || finished.warnings
    const cuenta = n ? `${n} ${plural(n, 'aviso', 'avisos')}` : 'avisos'
    return `⚠ Terminado con ${cuenta}${cola(finished.detalle_avisos, finished.n_detalle_avisos)}`
  }
  const n = finished.errors || 0
  return `✗ Terminado con ${n} ${plural(n, 'fallo', 'fallos')}${cola(finished.detalle_errores, finished.n_detalle_errores)}`
}
