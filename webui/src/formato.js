// Formateo compartido entre la UI de escritorio (`App.jsx`) y el kiosco
// (`KioskScreen.jsx`). Vive en su propio módulo para que el kiosco no tenga
// que importar de `App.jsx`, que a su vez importa el kiosco (ciclo).

// Tamaño en la unidad más legible: «0 B», «812 B», «1.4 GB».
export function formatBytes(n) {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`
}

// Duración en lenguaje llano: «18 min 12 s», «1 h 04 min», «45 s». Se usa
// tanto para el tiempo que lleva la subida como para el que acabó tardando, y
// por eso no lleva ni «hace» ni «quedan»: lo pone quien la llama.
export function formatDuracion(segundos) {
  if (segundos == null || !Number.isFinite(segundos) || segundos < 0) return '—'
  const s = Math.round(segundos)
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}
