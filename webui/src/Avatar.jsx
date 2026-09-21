import { useEffect, useState } from 'react'

// Avatar de la cuenta de Google con fallback a la inicial. Compartido por
// `MenuCuenta.jsx` (escritorio) y `KioskScreen.jsx` (kiosco): sin red o DNS
// la <img> apuntando a `googleusercontent.com` falla, y en vez de dejar el
// icono de imagen rota se cae a la inicial sobre un círculo, igual en los
// dos frentes.
export default function Avatar({ src, alt = '', inicial, imgClassName, fallbackClassName, testId }) {
  const [rota, setRota] = useState(false)

  // Si cambia la URL (otra cuenta, u otro intento), se le da otra
  // oportunidad a la <img>: el fallo previo era de esa URL concreta.
  useEffect(() => {
    setRota(false)
  }, [src])

  if (src && !rota) {
    return (
      <img
        src={src}
        alt={alt}
        className={imgClassName}
        data-testid={testId}
        onError={() => setRota(true)}
      />
    )
  }
  return <span className={fallbackClassName}>{inicial}</span>
}
