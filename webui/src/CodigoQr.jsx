import qrcode from 'qrcode-generator'

// Dibuja el QR como <path> de un solo SVG (un modulo oscuro = un cuadrado de
// 1x1 en el viewBox de la matriz). Nada de <img>/canvas: en el panel
// resistivo de 480x320 el vector sale nitido a cualquier escala, y a
// diferencia de canvas es testeable en jsdom sin dependencias extra.
function moduloPath(qr) {
  const n = qr.getModuleCount()
  let d = ''
  for (let fila = 0; fila < n; fila++) {
    for (let col = 0; col < n; col++) {
      if (qr.isDark(fila, col)) d += `M${col},${fila}h1v1h-1z`
    }
  }
  return { d, n }
}

export default function CodigoQr({ url, tam }) {
  // typeNumber 0 = que la libreria elija el tamano minimo que quepa la URL.
  // Nivel 'L' (no 'M'): la URL de consentimiento de Google ronda los 300
  // caracteres y en el panel de 480x320 cada modulo cae por debajo de 4 px,
  // por debajo de lo que resuelve la camara de un movil a pulso. Bajar la
  // correccion de errores quita dos versiones de rejilla y engorda cada
  // modulo; el QR se lee de cerca y limpio, no impreso ni sucio, asi que la
  // redundancia extra no aportaba nada aqui.
  const qr = qrcode(0, 'L')
  qr.addData(url)
  qr.make()
  const { d, n } = moduloPath(qr)
  // `tam` es opcional: solo se fija ancho/alto si el llamante lo pasa, para
  // no alterar el comportamiento por defecto (tamano lo marca el CSS de la
  // clase `pair-qr-svg`).
  const estilo = tam ? { width: tam, height: tam } : undefined
  return (
    <svg
      className="pair-qr-svg"
      viewBox={`0 0 ${n} ${n}`}
      role="img"
      aria-label="Codigo QR para vincular este equipo"
      style={estilo}
    >
      <path d={d} fill="#0a0a0a" />
    </svg>
  )
}
