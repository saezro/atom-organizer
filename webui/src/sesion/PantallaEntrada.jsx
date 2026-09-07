import { useEffect, useState } from 'react'
import { api } from '../bridge.js'

// Deriva un color de fondo estable a partir del email, para el avatar de los
// perfiles sin foto (o con foto rota): mismo email, mismo color siempre,
// entre sesiones y entre equipos, sin guardar nada aparte. Hash de cadena
// muy simple (no criptográfico, solo reparte el hue 0-359 en la rueda).
function colorDesdeEmail(texto) {
  let h = 0
  for (let i = 0; i < texto.length; i += 1) {
    h = (h * 31 + texto.charCodeAt(i)) % 360
  }
  return `hsl(${h}, 45%, 35%)`
}

// Un tile del selector: avatar (foto / inicial) + nombre, con su X de borrado
// en modo edición. Separado de PantallaEntrada para no anidar el `onError`
// de la foto (estado `fotoRota`) por cada perfil dentro del mismo componente.
function TilePerfil({ perfil, editando, onEntrar, onQuitar }) {
  const [fotoRota, setFotoRota] = useState(false)
  const etiqueta = perfil.modo === 'invitado' ? 'Invitado' : (perfil.nombre || perfil.email)
  const inicial = (perfil.nombre || perfil.email || '?').trim().charAt(0).toUpperCase()
  const tieneFoto = perfil.modo !== 'invitado' && perfil.picture && !fotoRota

  return (
    <div className="entrada-perfil">
      <button
        type="button"
        className="entrada-avatar"
        onClick={() => onEntrar(perfil)}
        aria-label={`Entrar como ${etiqueta}`}
        style={tieneFoto ? undefined : { background: colorDesdeEmail(perfil.email || etiqueta) }}
      >
        {tieneFoto ? (
          <img
            src={perfil.picture}
            alt=""
            className="entrada-avatar-img"
            onError={() => setFotoRota(true)}
          />
        ) : (
          <span className="entrada-avatar-inicial">{perfil.modo === 'invitado' ? '·' : inicial}</span>
        )}
      </button>
      {editando && (
        <button
          type="button"
          className="entrada-perfil-quitar"
          aria-label={`Quitar ${etiqueta}`}
          onClick={() => onQuitar(perfil.email)}
        >
          ×
        </button>
      )}
      <p className="entrada-perfil-nombre">{etiqueta}</p>
    </div>
  )
}

// Pantalla previa a la de trabajo: se pinta cuando `useSesion().entrado` es
// falso. Con perfiles guardados en este equipo se ofrece un selector tipo
// Netflix (activar sin repetir el consentimiento de Google); sin perfiles se
// mantiene el arranque limpio de siempre: dos botones.
//
// `onPerfilActivado` es opcional: `App.jsx` no se toca en este cambio, así
// que no hay forma de que se entere de una activación hecha aquí dentro (el
// hook `useSesion` que sostiene `cuenta`/`entrado` vive en `App.jsx`, no
// aquí). Sin esa prop, la única forma de que la app recoja la sesión recién
// activada es recargar: el WebView vuelve a montar todo y `useSesion` lee el
// `cloud_status` ya bueno.
export default function PantallaEntrada({ onGoogle, onInvitado, cargando, error, onPerfilActivado }) {
  const [perfiles, setPerfiles] = useState(null) // null = aún sin respuesta
  const [editando, setEditando] = useState(false)
  const [activando, setActivando] = useState(false)

  useEffect(() => {
    let vivo = true
    // `?.()` a propósito: builds/mocks viejos que no expongan el método NO
    // deben romper el arranque, solo caer al selector vacío (dos botones).
    Promise.resolve(api.listarPerfiles?.())
      .then((lista) => { if (vivo) setPerfiles(Array.isArray(lista) ? lista : []) })
      .catch(() => { if (vivo) setPerfiles([]) })
    return () => { vivo = false }
  }, [])

  const activar = async (perfil) => {
    if (perfil.modo === 'invitado') {
      onInvitado()
      return
    }
    setActivando(true)
    try {
      const respuesta = await api.activarPerfil(perfil.email)
      if (respuesta && respuesta.ok) {
        if (onPerfilActivado) onPerfilActivado()
        else window.location.reload()
        return
      }
    } catch {
      // credencial caducada / bridge sin el método: relogin silencioso.
    } finally {
      setActivando(false)
    }
    // Sin confirmación ni error: es una decisión de producto, no un fallo
    // que haya que explicarle al operador.
    onGoogle()
  }

  const quitar = async (email) => {
    try {
      await api.borrarPerfil(email)
    } catch {
      // best-effort: si falla, simplemente sigue apareciendo en la lista.
    }
    try {
      const lista = await api.listarPerfiles()
      setPerfiles(Array.isArray(lista) ? lista : [])
    } catch {
      // deja la lista como estaba
    }
  }

  // Mientras se resuelve la primera carga de perfiles, no parpadeamos: se
  // pinta directamente la pantalla de dos botones (es el caso más común —
  // primer arranque, sin perfiles— y evita el flash "selector -> dos
  // botones" en la inmensa mayoría de arranques).
  const hayPerfiles = Array.isArray(perfiles) && perfiles.length > 0

  return (
    <div className="entrada" data-testid="pantalla-entrada">
      <div className="entrada-marca brand">
        <h1>
          <span className="atom">ATOM</span> <span className="org">ORGANIZER</span>
        </h1>
      </div>

      {hayPerfiles ? (
        <>
          <div className="entrada-perfiles">
            {perfiles.map((p) => (
              <TilePerfil
                key={p.email || 'invitado'}
                perfil={p}
                editando={editando}
                onEntrar={activar}
                onQuitar={quitar}
              />
            ))}
            <button
              type="button"
              className="entrada-perfil entrada-anadir"
              onClick={onGoogle}
              aria-label="Añadir cuenta"
              disabled={cargando || activando}
            >
              <span className="entrada-avatar entrada-avatar-anadir">+</span>
              <p className="entrada-perfil-nombre">Añadir cuenta</p>
            </button>
          </div>
          <button
            type="button"
            className="btn-ghost entrada-administrar"
            onClick={() => setEditando((v) => !v)}
          >
            {editando ? 'Listo' : 'Administrar'}
          </button>
        </>
      ) : (
        <div className="entrada-acciones">
          <button
            type="button"
            className="btn-run entrada-btn"
            onClick={onGoogle}
            disabled={cargando}
          >
            Entrar con Google
          </button>
          {/* Nunca se deshabilita: es la salida cuando el login de Google se
              queda a medias (ventana de consentimiento cerrada, red caída).
              Sin cuenta se puede trabajar en local igual. */}
          <button
            type="button"
            className="btn-ghost entrada-btn"
            onClick={onInvitado}
          >
            Entrar sin cuenta
          </button>
        </div>
      )}

      {error && <p className="entrada-error">{error}</p>}
    </div>
  )
}
